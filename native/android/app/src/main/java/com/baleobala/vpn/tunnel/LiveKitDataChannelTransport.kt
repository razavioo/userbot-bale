package com.baleobala.vpn.tunnel

import android.content.Context
import io.livekit.android.LiveKit
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import io.livekit.android.room.track.DataPublishReliability
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

class LiveKitDataChannelTransport(
    private val appContext: Context,
    private val url: String,
    private val token: String,
    private val topic: String = "vpn",
    private val reliable: Boolean = true,
    private val onLog: (String) -> Unit = {},
    /**
     * Called once when the LiveKit room transitions to Disconnected
     * after a successful connect, OR when FailedToConnect fires post-
     * connect. Lets the carrier owner trigger an auto-reconnect — the
     * TUN reader/writer loops never throw on data-channel death by
     * themselves, so without this signal a half-dead session sits
     * dropping every ARQ frame in silence.
     */
    private val onDisconnected: () -> Unit = {},
) : Transport {
    // MUST match the python LiveKit transport's MTU constant
    // (src/baleobala/bale/livekit_backend.py::MTU). When the two ends
    // disagree, the side with the larger MTU silently emits frames the
    // other can't receive and the only symptom is the tunnel-dead
    // counter ticking up in one direction. 14 KiB is the conservative
    // value that's been live-tested through Bale's SFU.
    override val mtu: Int = 14 * 1024
    override val rateHint: Int = 200_000

    private val inbox = LinkedBlockingQueue<ByteArray>(4096)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var room: Room? = null
    @Volatile private var closed = false
    private val disconnectFired = java.util.concurrent.atomic.AtomicBoolean(false)
    /** Set true after the first successful connect; gates the
     *  onDisconnected callback so we only treat *post-connect*
     *  disconnects as carrier death (not a failed initial dial). */
    private val connectedOnce = java.util.concurrent.atomic.AtomicBoolean(false)

    /** Client IP assigned by the relay's BBMESH1 provisioning message,
     *  populated during `connect()` for `topic == "vpn"`. The
     *  BaleVpnService reads this BEFORE calling VpnService.Builder.
     *  addAddress() so the device's TUN address matches the slot the
     *  relay's PacketRouter has attached — without this, return packets
     *  are silently dropped on the relay because Android sent from a
     *  hardcoded 10.77.0.2 while the relay had a different /30. */
    @Volatile var clientIp: String? = null
        private set
    @Volatile var gatewayIp: String? = null
        private set
    /** Subnet prefix length (e.g. 30 for a /30 — but VpnService usually
     *  wants 24 to share the gateway in the same broadcast domain). */
    @Volatile var prefixLen: Int = 0
        private set
    /** TUN MTU advertised by the relay in BBMESH1. 0 if absent; the
     *  carrier falls back to its built-in default in that case. Honoring
     *  this lets the operator raise the path MTU on the server side
     *  (--tun-mtu 9000) to multiply per-frame goodput, since the SFU
     *  rate-limits frames-per-second rather than bytes-per-second. */
    @Volatile var tunMtu: Int = 0
        private set

    // --- Throughput instrumentation ---
    // Bandwidth-cap test: counts bytes/msgs in each direction and logs a
    // rolling rate every 5s. Compare numbers between reliable=true and
    // reliable=false runs to tell whether the cap is SFU-side or
    // client-side.
    private val txBytes = AtomicLong(0)
    private val txMsgs = AtomicLong(0)
    private val txMaxMsg = AtomicInteger(0)
    private val rxBytes = AtomicLong(0)
    private val rxMsgs = AtomicLong(0)
    private val rxMaxMsg = AtomicInteger(0)

    // --- Send pacing ---
    // Bale's SFU evicts participants that publishData faster than ~50/s
    // sustained (observed: 8KB packets at full rate killed the tunnel
    // within ~48s with tunnel_dead). Pace publishData at the transport
    // layer to stay under that threshold. 20_000us = 20ms minimum
    // interval = 50 publishes/sec ceiling, which empirically holds the
    // tunnel alive indefinitely while still letting MTU-9000 carry
    // ~450 KB/s of payload. Override at runtime with `setprop
    // debug.baleobala.pace_us N`; 0 disables pacing.
    private val pacingMutex = Object()
    @Volatile private var lastSendNanos: Long = 0
    private val pacingIntervalNanos: Long = run {
        val override = try {
            val cls = Class.forName("android.os.SystemProperties")
            val get = cls.getMethod("get", String::class.java, String::class.java)
            ((get.invoke(null, "debug.baleobala.pace_us", "") as? String) ?: "")
                .toLongOrNull()
        } catch (_: Throwable) { null }
        (override ?: 20_000L) * 1_000L  // micros → nanos
    }
    private val pacedSleeps = AtomicLong(0)
    private val pacedSleepNanos = AtomicLong(0)

    fun connect(timeoutMs: Long = 30_000): LiveKitDataChannelTransport {
        val r = LiveKit.create(appContext)
        room = r
        val ready = java.util.concurrent.CountDownLatch(1)
        val peerReady = java.util.concurrent.CountDownLatch(1)
        var error: Throwable? = null
        scope.launch {
            launch {
                r.events.collect { event ->
                    when (event) {
                        is RoomEvent.ParticipantConnected -> {
                            onLog("LiveKit remote participant connected: ${event.participant.identity}")
                            peerReady.countDown()
                        }
                        is RoomEvent.ParticipantDisconnected -> {
                            // The Bale SFU emits ParticipantDisconnected on
                            // transient signaling glitches even while the
                            // peer's data channel keeps publishing. Treating
                            // this as fatal here used to kill the carrier
                            // and trigger a full reconnect for every blip.
                            // RoomEvent.Disconnected is the authoritative
                            // teardown signal — keep the carrier open until
                            // we see that or until ARQ on the Tunnel layer
                            // declares tunnel_dead from real frame loss.
                            onLog("LiveKit remote participant disconnected: ${event.participant.identity} (carrier staying up)")
                        }
                        is RoomEvent.DataReceived -> {
                            // Bale/LiveKit sits between two different SDKs
                            // (python livekit-rtc on the exit node, Android
                            // SDK on the handset). In live testing the room is
                            // dedicated to one VPN session, while the reported
                            // DataReceived topic is not a contract we can trust
                            // across SDK versions. If we filter too tightly here
                            // both sides can publish successfully but neither
                            // tunnel ever sees a frame, producing symmetric
                            // seq=0..N max-retry drops and tunnel_dead loops.
                            if (event.topic != null && event.topic != topic) {
                                onLog("LiveKit data topic=${event.topic}; ignoring non-VPN payload")
                                return@collect
                            }
                            rxBytes.addAndGet(event.data.size.toLong())
                            rxMsgs.incrementAndGet()
                            rxMaxMsg.accumulateAndGet(event.data.size) { a, b -> if (b > a) b else a }
                            inbox.offer(event.data)
                        }
                        is RoomEvent.FailedToConnect -> {
                            error = event.error
                            ready.countDown()
                            // If we'd already connected once and then
                            // failed to reconnect, treat it the same as
                            // a clean Disconnected for carrier-death
                            // purposes.
                            if (connectedOnce.get()) fireDisconnected("FailedToConnect: ${event.error.message}")
                        }
                        is RoomEvent.Disconnected -> {
                            onLog("LiveKit disconnected reason=${event.reason} error=${event.error?.message}")
                            fireDisconnected("RoomEvent.Disconnected")
                        }
                        else -> {}
                    }
                }
            }
            try {
                r.connect(url, token)
                connectedOnce.set(true)
                // Publish a microphone track so Bale's SFU keeps this
                // participant alive. Without an audio publish the SFU
                // evicts the participant in ~10–15s ("LiveKit remote
                // participant disconnected" on the relay side), which
                // killed the tunnel within ~12s of every connect. The
                // mic data is irrelevant — only the published track's
                // existence matters to the SFU.
                if (topic == "vpn") {
                    try {
                        r.localParticipant.setMicrophoneEnabled(true)
                        onLog("LiveKit microphone published (keeps SFU participant alive)")
                    } catch (t: Throwable) {
                        onLog("warn: setMicrophoneEnabled failed: ${t.message} (tunnel may drop after ~15s)")
                    }
                }
                if (r.remoteParticipants.isNotEmpty()) peerReady.countDown()
                onLog("LiveKit connected; remoteParticipants=${r.remoteParticipants.size}")
                ready.countDown()
            } catch (t: Throwable) {
                error = t
                ready.countDown()
            }
        }
        if (!ready.await(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)) {
            close()
            throw RuntimeException("LiveKit connect timed out")
        }
        error?.let {
            close()
            throw RuntimeException("LiveKit connect failed: ${it.message}", it)
        }
        if (!peerReady.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            close()
            throw RuntimeException("LiveKit peer did not join within ${timeoutMs}ms")
        }
        onLog("LiveKit peer ready (reliable=$reliable)")
        startThroughputLogger()
        maybeStartSaturationTest()

        // Mesh provisioning handshake — the relay sends BBMESH1:{kind:"assign",...}
        // immediately after joining. We must ACK it before VPN traffic can flow.
        // If BBMESH1 doesn't arrive within the window, the relay refused
        // the call (slot busy, account already in use, or PSK mismatch).
        // Falling through with default values produces a tun_up that's
        // connected to nothing — the carrier then sends frames into the
        // void and only notices via tunnel_dead 25 s later. Throwing here
        // lets the BaleVpnService
        // catch it and trigger an immediate retry instead.
        if (topic == "vpn") {
            val provRaw = inbox.poll(15_000, TimeUnit.MILLISECONDS)
            if (provRaw == null) {
                close()
                throw RuntimeException(
                    "no BBMESH1 provisioning received within 15s; " +
                    "relay likely rejected the call (race with EXPECT_CLIENT)"
                )
            }
            run {
                val provStr = provRaw.toString(Charsets.UTF_8)
                if (provStr.startsWith("BBMESH1:")) {
                    val sessionId = Regex(""""session_id"\s*:\s*(\d+)""")
                        .find(provStr)?.groupValues?.get(1)
                    // Capture the assigned client/gateway IPs so the
                    // BaleVpnService can rebuild VpnService.Builder with
                    // the correct TUN address. Without this each device
                    // hardcoded 10.77.0.2; second concurrent client got
                    // 10.77.0.6 from the relay and silently dropped all
                    // return traffic.
                    clientIp = Regex(""""client_ip"\s*:\s*"([^"]+)"""")
                        .find(provStr)?.groupValues?.get(1)
                    gatewayIp = Regex(""""gateway_ip"\s*:\s*"([^"]+)"""")
                        .find(provStr)?.groupValues?.get(1)
                    val prefixStr = Regex(""""prefix"\s*:\s*"[^/]+/(\d+)"""")
                        .find(provStr)?.groupValues?.get(1)
                    if (prefixStr != null) {
                        prefixLen = prefixStr.toIntOrNull() ?: 0
                    }
                    val tunMtuStr = Regex(""""tun_mtu"\s*:\s*(\d+)""")
                        .find(provStr)?.groupValues?.get(1)
                    if (tunMtuStr != null) {
                        tunMtu = tunMtuStr.toIntOrNull() ?: 0
                    }
                    if (sessionId != null) {
                        val ack = "BBMESH1:{\"kind\":\"ack\",\"session_id\":$sessionId}".toByteArray(Charsets.UTF_8)
                        sendBytes(ack)
                        onLog("provisioning: sent ACK session_id=$sessionId client_ip=$clientIp gateway_ip=$gatewayIp")
                    } else {
                        onLog("provisioning: BBMESH1 message missing session_id, skipping ACK")
                    }
                } else {
                    // Not a provisioning message — put it back for the carrier
                    inbox.put(provRaw)
                }
            }
        }

        return this
    }

    override fun sendBytes(data: ByteArray) {
        if (closed) throw IllegalStateException("LiveKit transport closed")
        if (data.size > mtu) throw IllegalArgumentException("frame ${data.size} > LiveKit MTU $mtu")
        val r = room ?: throw IllegalStateException("LiveKit room not connected")
        // Pace publishData so we never exceed the SFU's per-participant
        // rate limit. Synchronized block: callers serialize against the
        // last send timestamp. The actual publishData happens after the
        // lock is released so we don't hold it for the full publish.
        if (pacingIntervalNanos > 0) {
            synchronized(pacingMutex) {
                val now = System.nanoTime()
                val nextOk = lastSendNanos + pacingIntervalNanos
                if (now < nextOk) {
                    val sleepNs = nextOk - now
                    pacedSleeps.incrementAndGet()
                    pacedSleepNanos.addAndGet(sleepNs)
                    val ms = sleepNs / 1_000_000L
                    val ns = (sleepNs % 1_000_000L).toInt()
                    try { Thread.sleep(ms, ns) } catch (_: InterruptedException) { return }
                    lastSendNanos = System.nanoTime()
                } else {
                    lastSendNanos = now
                }
            }
        }
        val reliability = if (reliable) DataPublishReliability.RELIABLE else DataPublishReliability.LOSSY
        var lastFailure: Throwable? = null
        repeat(3) { attempt ->
            // Avoid runBlocking: it spins up a fresh event loop on the
            // calling thread, which can deadlock when the caller is itself
            // a coroutine dispatcher worker (e.g. Tunnel ARQ retry threads
            // sharing Dispatchers.IO). Hand the suspend call to our own
            // scope and block on a latch instead.
            val latch = CountDownLatch(1)
            val publishResult = AtomicReference<Result<Unit>?>(null)
            scope.launch {
                try {
                    publishResult.set(
                        r.localParticipant.publishData(data, reliability = reliability, topic = topic)
                    )
                } catch (t: Throwable) {
                    publishResult.set(Result.failure(t))
                } finally {
                    latch.countDown()
                }
            }
            if (!latch.await(5, TimeUnit.SECONDS)) {
                lastFailure = RuntimeException("LiveKit publishData timed out")
            } else {
                val res = publishResult.get()
                if (res != null && res.isSuccess) {
                    txBytes.addAndGet(data.size.toLong())
                    txMsgs.incrementAndGet()
                    txMaxMsg.accumulateAndGet(data.size) { a, b -> if (b > a) b else a }
                    return
                }
                lastFailure = res?.exceptionOrNull() ?: RuntimeException("publishData returned null")
            }
            if (attempt < 2) Thread.sleep(150L * (attempt + 1))
        }
        throw lastFailure ?: RuntimeException("LiveKit publishData failed")
    }

    override fun recvBytes(timeoutMs: Long): ByteArray? =
        inbox.poll(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)

    override fun close() {
        if (closed) return
        closed = true
        try { room?.disconnect() } catch (_: Throwable) {}
        room = null
        scope.cancel()
    }

    // Saturation lab: when `setprop debug.baleobala.saturate 1` is set,
    // publishes max-size synthetic frames to a side topic ("satlab") for
    // 30s as fast as publishData allows. Measures the raw SFU acceptance
    // ceiling independently of TUN/app traffic. Uses a different topic so
    // the relay's VPN path ignores the frames (DataReceived handler
    // filters by topic on both ends).
    private fun maybeStartSaturationTest() {
        val enabled = try {
            val cls = Class.forName("android.os.SystemProperties")
            val get = cls.getMethod("get", String::class.java, String::class.java)
            (get.invoke(null, "debug.baleobala.saturate", "") as? String) == "1"
        } catch (_: Throwable) { false }
        if (!enabled) return
        val r = room ?: return
        scope.launch {
            // small delay so connect/provisioning settles
            delay(2_000)
            onLog("SATLAB: starting saturation test reliable=$reliable size=${mtu}B duration=30s topic=satlab")
            val payload = ByteArray(mtu) { 0xA5.toByte() }
            val reliability = if (reliable) DataPublishReliability.RELIABLE else DataPublishReliability.LOSSY
            val tStart = System.currentTimeMillis()
            var sent = 0L
            var bytes = 0L
            var failures = 0L
            while (isActive && !closed && System.currentTimeMillis() - tStart < 30_000) {
                val res = try {
                    r.localParticipant.publishData(payload, reliability = reliability, topic = "satlab")
                } catch (t: Throwable) { Result.failure<Unit>(t) }
                if (res.isSuccess) {
                    sent++; bytes += payload.size
                } else {
                    failures++
                    if (failures % 50L == 1L) onLog("SATLAB: publish failure #$failures: ${res.exceptionOrNull()?.message}")
                }
            }
            val elapsed = (System.currentTimeMillis() - tStart) / 1000.0
            val kbs = bytes / 1024.0 / elapsed
            val msgPerS = sent / elapsed
            onLog(
                "SATLAB: done reliable=$reliable elapsed=${"%.1f".format(elapsed)}s " +
                "sent=$sent msgs (${"%.0f".format(msgPerS)} msg/s) " +
                "bytes=$bytes (${"%.1f".format(kbs)} KB/s) failures=$failures"
            )
        }
    }

    private fun startThroughputLogger() {
        scope.launch {
            var prevTxB = 0L; var prevTxM = 0L
            var prevRxB = 0L; var prevRxM = 0L
            val intervalMs = 5_000L
            while (isActive && !closed) {
                delay(intervalMs)
                val txB = txBytes.get(); val txM = txMsgs.get(); val txMax = txMaxMsg.get()
                val rxB = rxBytes.get(); val rxM = rxMsgs.get(); val rxMax = rxMaxMsg.get()
                val dTxB = txB - prevTxB; val dTxM = txM - prevTxM
                val dRxB = rxB - prevRxB; val dRxM = rxM - prevRxM
                prevTxB = txB; prevTxM = txM; prevRxB = rxB; prevRxM = rxM
                val secs = intervalMs / 1000.0
                val txKBs = dTxB / 1024.0 / secs
                val rxKBs = dRxB / 1024.0 / secs
                val txAvg = if (dTxM > 0) dTxB / dTxM else 0
                val rxAvg = if (dRxM > 0) dRxB / dRxM else 0
                val pSleeps = pacedSleeps.getAndSet(0)
                val pNanos = pacedSleepNanos.getAndSet(0)
                val pAvgMs = if (pSleeps > 0) (pNanos / pSleeps) / 1_000_000.0 else 0.0
                val paceMs = pacingIntervalNanos / 1_000_000.0
                onLog(
                    "throughput[reliable=$reliable,pace=${"%.1f".format(paceMs)}ms] " +
                    "tx=${"%.1f".format(txKBs)}KB/s (${dTxM}msg, avg=${txAvg}B, max=${txMax}B) " +
                    "rx=${"%.1f".format(rxKBs)}KB/s (${dRxM}msg, avg=${rxAvg}B, max=${rxMax}B) " +
                    "paced=$pSleeps sleeps avg=${"%.1f".format(pAvgMs)}ms"
                )
            }
        }
    }

    private fun fireDisconnected(reason: String) {
        if (closed) return
        if (!disconnectFired.compareAndSet(false, true)) return
        try { onDisconnected() } catch (t: Throwable) { onLog("onDisconnected handler threw: ${t.message}") }
    }
}
