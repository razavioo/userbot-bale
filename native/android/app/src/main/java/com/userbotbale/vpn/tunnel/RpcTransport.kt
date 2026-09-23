package com.userbot_bale.vpn.tunnel

import java.util.Base64
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Minimal Bale messaging surface required by [RpcTransport] — Kotlin
 * equivalent of Python `MessagingBackend` in bale/messaging_backend.py.
 */
interface MessagingBackend {
    fun start(timeoutMs: Long = 15_000)
    fun stop()
    fun sendMessage(peerId: Long, body: ByteArray, peerType: Int = 1)
    fun listenMessages(peerId: Long, callback: (ByteArray) -> Unit)
}

/**
 * RPC transport: store-and-forward VPN frames as Bale chat messages.
 *
 * Port of [src/userbot-bale/vpn/transports/rpc_transport.py](../../../../../../../../src/userbot-bale/vpn/transports/rpc_transport.py).
 *
 * When a Bale call drops (or WebRTC data channels are blocked), the chat
 * channel usually still works. Each VPN frame is base64-encoded behind a
 * zero-width-space tag so plain chat from the peer is ignored on recv.
 *
 * Framing matches Python `MSG_PREFIX = "​bb-vpn:"`.
 */
class RpcTransport(
    private val backend: MessagingBackend,
    private val peerId: Long,
    private val peerType: Int = 1,
) : Transport {
    override val mtu: Int = MTU
    override val rateHint: Int = RATE_HINT

    private val inbound = LinkedBlockingQueue<ByteArray>()
    private val closed = AtomicBoolean(false)
    private var decodeFailures = 0
    private var b64Failures = 0
    private var unexpectedPrefix = 0

    init {
        wireReceive()
    }

    fun stats(): Map<String, Int> = mapOf(
        "decode_failures" to decodeFailures,
        "b64_failures" to b64Failures,
        "unexpected_prefix" to unexpectedPrefix,
    )

    override fun sendBytes(data: ByteArray) {
        if (closed.get()) return
        require(data.size <= MTU) { "frame ${data.size} > rpc MTU $MTU" }
        val encoded = MSG_PREFIX + Base64.getEncoder().encodeToString(data)
        backend.sendMessage(peerId, encoded.toByteArray(Charsets.UTF_8), peerType)
    }

    override fun recvBytes(timeoutMs: Long): ByteArray? {
        if (closed.get()) return null
        return inbound.poll(timeoutMs, TimeUnit.MILLISECONDS)
    }

    override fun close() {
        closed.set(true)
    }

    private fun wireReceive() {
        try {
            backend.listenMessages(peerId) { body -> onMessage(body) }
        } catch (t: Throwable) {
            // Inbound VPN frames over chat will be silently dropped until
            // the backend can register a listener.
        }
    }

    private fun onMessage(body: ByteArray) {
        if (closed.get()) return
        val text = try {
            String(body, Charsets.UTF_8)
        } catch (_: Exception) {
            decodeFailures++
            return
        }
        if (!text.startsWith(MSG_PREFIX)) {
            unexpectedPrefix++
            return
        }
        val b64 = text.substring(MSG_PREFIX.length)
        val frame = try {
            Base64.getDecoder().decode(b64)
        } catch (_: IllegalArgumentException) {
            b64Failures++
            return
        }
        inbound.offer(frame)
    }

    companion object {
        const val MTU = 3 * 1024
        const val RATE_HINT = 4_000
        /** Zero-width space + tag — nearly invisible in chat; matches Python. */
        const val MSG_PREFIX = "​bb-vpn:"
    }
}
