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
        onLog("LiveKit peer ready")

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
