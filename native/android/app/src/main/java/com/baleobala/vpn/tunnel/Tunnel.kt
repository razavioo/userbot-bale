package com.baleobala.vpn.tunnel

import android.util.Log
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.Semaphore
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Selective-repeat ARQ tunnel — port of
 * [src/baleobala/vpn/tunnel.py](../../../../../../../../src/baleobala/vpn/tunnel.py).
 *
 * Moves IP packets over a [Transport] with session-id framing,
 * 24-bit selective-repeat ARQ, and MTU-split reassembly.
 *
 * Threading:
 *  - rxThread: reads frames from the transport and dispatches them
 *  - retryThread: re-sends unacked frames after [ackTimeoutMs]
 *  - sendPacket() runs on the caller's thread and blocks on the
 *    sliding window semaphore.
 */
class Tunnel(
    transport: Transport,
    val sessId: Int,
    // Window stays at 32. With MULTI_ACK + MTU 9000 the sustainable
    // sender→receiver throughput is ~240 KB/s, gated by the SFU's
    // outbound rate cap (~20 frames/sec returned to each participant).
    // Raising the window to 128 briefly hit 400+ KB/s peaks but the
    // in-flight queue (window/ack_rate ≈ 3.2s) exceeded ackTimeout
    // (1.5s), triggering retry storms that ended in tunnel_dead after
    // 5 minutes of otherwise-healthy throughput. window=32 keeps
    // in-flight time at ~0.8s, well under the timeout.
    private val window: Int = 32,
    private val ackTimeoutMs: Long = 1500,
    private val maxRetries: Int = 16,
    private val recvTimeoutMs: Long = 200,
    private val mtuOverride: Int? = null,
) {
    private val txLock = ReentrantLock()
    private var tx: Transport = transport

    private val pending = ConcurrentHashMap<Int, Pending>()
    private val sendSlot = Semaphore(window)
    private val seqNext = java.util.concurrent.atomic.AtomicInteger(0)

    private val stopFlag = AtomicBoolean(false)
    private var rxThread: Thread? = null
    private var retryThread: Thread? = null
    private var ackThread: Thread? = null

    // Batched ACK sender — mirrors python's _run_ack. The rx thread
    // enqueues acked seqs and this drain thread bundles them into a
    // MULTI_ACK frame every 25ms (~40Hz). Cuts the ACK frame rate by
    // up to ~20× under heavy receive, freeing the SFU's frame-rate
    // budget for data frames in both directions.
    private val ackQueue = ConcurrentLinkedQueue<Int>()
    private val ackFlushIntervalMs: Long = 25L
    private val ackWakeLock = Object()
    private var onPacket: ((ByteArray) -> Unit)? = null
    private val onEvent = ArrayList<(String, Map<String, Any?>) -> Unit>()

    private val rxBuf = HashMap<Int, RxPacket>()
    private val seenLock = Any()
    private val seen = LinkedHashSet<Int>()
    private val seenCap = 4096

    /** Consecutive frames that were dropped after exhausting retries
     *  without any successful ACK in between. When this crosses
     *  [deadThreshold] we emit a single `tunnel_dead` event so the
     *  carrier can tear down + reconnect — the underlying transport
     *  may still claim to be alive even though no packet is making
     *  it through (LiveKit DataChannel silent failure mode). */
    private val consecutiveDrops = java.util.concurrent.atomic.AtomicInteger(0)
    private val deadEmitted = java.util.concurrent.atomic.AtomicBoolean(false)
    // Raised 8 → 24. With window=128 there can be many more frames in
    // flight at any moment, and a brief network glitch can cause a burst
    // of consecutive max-retries drops once the retry timers (16 ×
    // 1.5s = 24s per frame) finally expire. The old threshold tripped
    // tunnel_dead after ~5 min of otherwise-healthy 240+ KB/s on the
    // MULTI_ACK build. 24 consecutive drops with no intervening ACK
    // represents truly dead silence (~8+ minutes per frame group).
    private val deadThreshold = 24

    init {
        require(sessId in 0 until VpnFraming.SESS_MODULO) { "sess_id out of range" }
    }

    private data class Pending(val frame: VpnFraming.Frame, var sentAt: Long, var retries: Int = 0)
    private data class RxPacket(val parts: HashMap<Int, ByteArray> = HashMap(), var gotLast: Boolean = false)

    fun addEventHandler(handler: (String, Map<String, Any?>) -> Unit) {
        onEvent.add(handler)
    }

    fun start(onPacketCb: (ByteArray) -> Unit) {
        check(rxThread == null) { "tunnel already started" }
        onPacket = onPacketCb
        stopFlag.set(false)
        rxThread = Thread({ runRx() }, "vpn-tun-rx").also { it.isDaemon = true; it.start() }
        retryThread = Thread({ runRetry() }, "vpn-tun-retry").also { it.isDaemon = true; it.start() }
        ackThread = Thread({ runAck() }, "vpn-tun-ack").also { it.isDaemon = true; it.start() }
    }

    fun stop() {
        stopFlag.set(true)
        for (i in 0 until window) {
            try { sendSlot.release() } catch (_: Throwable) {}
        }
        synchronized(ackWakeLock) { ackWakeLock.notifyAll() }
        try { rxThread?.join(2000) } catch (_: InterruptedException) {}
        try { retryThread?.join(2000) } catch (_: InterruptedException) {}
        try { ackThread?.join(2000) } catch (_: InterruptedException) {}
        rxThread = null
        retryThread = null
        ackThread = null
    }

    fun sendPacket(packet: ByteArray) {
        val effectiveMtu = mtuOverride ?: tx.mtu
        val maxPayload = maxOf(1, effectiveMtu - VpnFraming.HEADER_SIZE)
        val start: Int
        val frames: List<VpnFraming.Frame>
        synchronized(this) {
            start = seqNext.get()
            frames = VpnFraming.splitPacket(packet, sessId, start, maxPayload)
            seqNext.set((start + frames.size) % VpnFraming.SEQ_MODULO)
        }
        for (f in frames) {
            sendSlot.acquire()
            if (stopFlag.get()) return
            pending[f.seq] = Pending(f, System.currentTimeMillis())
            try {
                txLock.withLock { tx.sendBytes(f.encode()) }
            } catch (e: Throwable) {
                emit("transport_send_failed", mapOf("error" to e.message, "seq" to f.seq))
                throw e
            }
        }
    }

    fun pendingCount(): Int = pending.size

    fun swapTransport(newTransport: Transport): Transport {
        var old: Transport
        txLock.withLock {
            old = tx
            tx = newTransport
        }
        emit("transport_swapped", mapOf(
            "old" to old::class.java.simpleName,
            "new" to newTransport::class.java.simpleName,
        ))
        return old
    }

    private fun runRx() {
        while (!stopFlag.get()) {
            val current = txLock.withLock { tx }
            val buf = try {
                current.recvBytes(recvTimeoutMs)
            } catch (t: Throwable) {
                emit("transport_recv_failed", mapOf("error" to t.message))
                Thread.sleep(50)
                continue
            } ?: continue
            val frame = VpnFraming.decode(buf) ?: continue
            if (frame.sessId != sessId) continue
            if (frame.flags and VpnFraming.Flag.ACK != 0) {
                handleAck(frame.seq)
                if (frame.flags and VpnFraming.Flag.MULTI_ACK != 0 && frame.payload.isNotEmpty()) {
                    // Payload is 3-byte little-endian seqs (extras).
                    var i = 0
                    while (i + 2 < frame.payload.size) {
                        val extra = (frame.payload[i].toInt() and 0xFF) or
                            ((frame.payload[i + 1].toInt() and 0xFF) shl 8) or
                            ((frame.payload[i + 2].toInt() and 0xFF) shl 16)
                        handleAck(extra)
                        i += 3
                    }
                }
                continue
            }
            sendAck(frame.seq)
            synchronized(seenLock) {
                if (frame.seq in seen) return@synchronized
                seen.add(frame.seq)
                while (seen.size > seenCap) {
                    val it = seen.iterator(); it.next(); it.remove()
                }
                deliver(frame)
            }
        }
    }

    private fun runRetry() {
        val sleepMs = ackTimeoutMs / 2
        while (!stopFlag.get()) {
            try { Thread.sleep(sleepMs) } catch (_: InterruptedException) { break }
            val now = System.currentTimeMillis()
            val toRetry = ArrayList<Pending>()
            val toDrop = ArrayList<Int>()
            for ((seq, p) in pending) {
                if (now - p.sentAt < ackTimeoutMs) continue
                if (p.retries >= maxRetries) { toDrop.add(seq); continue }
                p.retries++
                p.sentAt = now
                toRetry.add(p)
            }
            for (seq in toDrop) {
                pending.remove(seq)
                sendSlot.release()
                emit("frame_dropped", mapOf("seq" to seq, "reason" to "max_retries"))
                Log.w(TAG, "dropping seq=$seq after max retries")
                val streak = consecutiveDrops.incrementAndGet()
                if (streak >= deadThreshold && deadEmitted.compareAndSet(false, true)) {
                    emit("tunnel_dead", mapOf("consecutive_drops" to streak))
                    Log.w(TAG, "tunnel appears dead: $streak consecutive drops without an ACK")
                }
            }
            for (p in toRetry) {
                val rf = VpnFraming.Frame(
                    p.frame.sessId, p.frame.seq,
                    p.frame.flags or VpnFraming.Flag.RETRY, p.frame.payload,
                )
                try {
                    txLock.withLock { tx.sendBytes(rf.encode()) }
                } catch (e: Throwable) {
                    emit("transport_retry_failed", mapOf("error" to e.message, "seq" to p.frame.seq))
                }
            }
        }
    }

    private fun handleAck(seq: Int) {
        if (pending.remove(seq) != null) {
            sendSlot.release()
            // Any successful ACK resets the carrier-death counter; only
            // a true silent-stall sequence ever fires `tunnel_dead`.
            consecutiveDrops.set(0)
            deadEmitted.set(false)
        }
    }

    private fun sendAck(seq: Int) {
        // Non-blocking enqueue; runAck() drains and emits MULTI_ACK
        // frames at ~40Hz.
        ackQueue.offer(seq)
        synchronized(ackWakeLock) { ackWakeLock.notify() }
    }

    private fun runAck() {
        val mtu = tx.mtu
        val maxExtras = maxOf(0, (mtu - VpnFraming.HEADER_SIZE) / 3)
        val chunkSize = maxExtras + 1
        while (!stopFlag.get()) {
            // Wait for new acks or the flush interval, whichever first.
            if (ackQueue.isEmpty()) {
                synchronized(ackWakeLock) {
                    if (ackQueue.isEmpty() && !stopFlag.get()) {
                        try { ackWakeLock.wait(ackFlushIntervalMs) } catch (_: InterruptedException) { return }
                    }
                }
            } else {
                try { Thread.sleep(ackFlushIntervalMs) } catch (_: InterruptedException) { return }
            }
            if (stopFlag.get()) return
            val batch = ArrayList<Int>()
            val seen = HashSet<Int>()
            while (true) {
                val s = ackQueue.poll() ?: break
                if (seen.add(s)) batch.add(s)
            }
            if (batch.isEmpty()) continue
            var start = 0
            while (start < batch.size) {
                val end = minOf(start + chunkSize, batch.size)
                val primary = batch[start]
                val extras = if (end - start > 1) batch.subList(start + 1, end) else emptyList()
                val payload = if (extras.isNotEmpty()) {
                    val pl = ByteArray(extras.size * 3)
                    var p = 0
                    for (s in extras) {
                        pl[p] = (s and 0xFF).toByte()
                        pl[p + 1] = ((s ushr 8) and 0xFF).toByte()
                        pl[p + 2] = ((s ushr 16) and 0xFF).toByte()
                        p += 3
                    }
                    pl
                } else ByteArray(0)
                val flags = if (extras.isNotEmpty())
                    VpnFraming.Flag.ACK or VpnFraming.Flag.MULTI_ACK
                else VpnFraming.Flag.ACK
                val ack = VpnFraming.Frame(sessId, primary, flags, payload)
                try {
                    txLock.withLock { tx.sendBytes(ack.encode()) }
                } catch (_: Throwable) { /* best-effort */ }
                start = end
            }
        }
    }

    private fun deliver(frame: VpnFraming.Frame) {
        val cb = onPacket ?: return
        if (frame.flags and VpnFraming.Flag.SPLIT == 0) {
            try { cb(frame.payload) } catch (e: Throwable) { Log.e(TAG, "onPacket cb threw", e) }
            return
        }
        val start = findOrOpenStart(frame.seq)
        val pkt = rxBuf.getOrPut(start) { RxPacket() }
        pkt.parts[frame.seq] = frame.payload
        if (frame.flags and VpnFraming.Flag.LAST != 0) pkt.gotLast = true
        if (pkt.gotLast) tryEmit(start, cb)
    }

    private fun findOrOpenStart(seq: Int): Int {
        for ((start, _) in rxBuf) {
            if (start <= seq && seq - start < 256) return start
        }
        rxBuf[seq] = RxPacket()
        return seq
    }

    private fun tryEmit(start: Int, cb: (ByteArray) -> Unit) {
        val pkt = rxBuf[start] ?: return
        if (!pkt.gotLast) return
        val last = pkt.parts.keys.max()
        val expected = last - start + 1
        if (pkt.parts.size != expected) return
        val parts = ArrayList<ByteArray>(expected)
        for (i in 0 until expected) {
            parts.add(pkt.parts[start + i] ?: return)
        }
        rxBuf.remove(start)
        val total = parts.sumOf { it.size }
        val out = ByteArray(total)
        var off = 0
        for (p in parts) {
            System.arraycopy(p, 0, out, off, p.size)
            off += p.size
        }
        try { cb(out) } catch (e: Throwable) { Log.e(TAG, "onPacket cb threw", e) }
    }

    private fun emit(event: String, payload: Map<String, Any?>) {
        for (h in onEvent.toList()) {
            try { h(event, payload) } catch (e: Throwable) { Log.e(TAG, "event handler threw", e) }
        }
    }

    companion object { private const val TAG = "Tunnel" }
}
