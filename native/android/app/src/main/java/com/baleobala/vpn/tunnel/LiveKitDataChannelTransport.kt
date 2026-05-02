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
import kotlinx.coroutines.launch
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
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

    fun connect(timeoutMs: Long = 30_000): LiveKitDataChannelTransport {
        val r = LiveKit.create(appContext)
        room = r
        val ready = java.util.concurrent.CountDownLatch(1)
        var error: Throwable? = null
        scope.launch {
            launch {
                r.events.collect { event ->
                    when (event) {
                        is RoomEvent.DataReceived -> {
                            if (event.topic == topic || event.topic == null) inbox.offer(event.data)
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
                            onLog("LiveKit disconnected")
                            fireDisconnected("RoomEvent.Disconnected")
                        }
                        else -> {}
                    }
                }
            }
            try {
                r.connect(url, token)
                connectedOnce.set(true)
                onLog("LiveKit connected")
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
        return this
    }

    override fun sendBytes(data: ByteArray) {
        if (closed) throw IllegalStateException("LiveKit transport closed")
        if (data.size > mtu) throw IllegalArgumentException("frame ${data.size} > LiveKit MTU $mtu")
        val r = room ?: throw IllegalStateException("LiveKit room not connected")
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
                if (res != null && res.isSuccess) return
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

    private fun fireDisconnected(reason: String) {
        if (closed) return
        if (!disconnectFired.compareAndSet(false, true)) return
        try { onDisconnected() } catch (t: Throwable) { onLog("onDisconnected handler threw: ${t.message}") }
    }
}
