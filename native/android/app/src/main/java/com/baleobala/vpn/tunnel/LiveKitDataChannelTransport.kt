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
import kotlinx.coroutines.runBlocking
import java.util.concurrent.LinkedBlockingQueue

class LiveKitDataChannelTransport(
    private val appContext: Context,
    private val url: String,
    private val token: String,
    private val topic: String = "vpn",
    private val reliable: Boolean = true,
    private val onLog: (String) -> Unit = {},
) : Transport {
    override val mtu: Int = 14 * 1024
    override val rateHint: Int = 200_000

    private val inbox = LinkedBlockingQueue<ByteArray>(4096)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var room: Room? = null
    @Volatile private var closed = false

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
                        }
                        is RoomEvent.Disconnected -> onLog("LiveKit disconnected")
                        else -> {}
                    }
                }
            }
            try {
                r.connect(url, token)
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
            val result = runBlocking(Dispatchers.IO) {
                r.localParticipant.publishData(data, reliability = reliability, topic = topic)
            }
            if (result.isSuccess) return
            lastFailure = result.exceptionOrNull()
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
}
