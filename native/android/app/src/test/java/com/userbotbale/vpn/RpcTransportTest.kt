package com.userbot_bale.vpn

import com.userbot_bale.vpn.tunnel.MessagingBackend
import com.userbot_bale.vpn.tunnel.RpcTransport
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap

private class FakeMessagingBackend : MessagingBackend {
    val sent = ArrayList<Triple<Long, ByteArray, Int>>()
    private val listeners = ConcurrentHashMap<Long, (ByteArray) -> Unit>()
    var started = false
    var stopped = false

    override fun start(timeoutMs: Long) { started = true }
    override fun stop() { stopped = true; started = false }

    override fun sendMessage(peerId: Long, body: ByteArray, peerType: Int) {
        sent.add(Triple(peerId, body, peerType))
    }

    override fun listenMessages(peerId: Long, callback: (ByteArray) -> Unit) {
        listeners[peerId] = callback
    }

    fun deliver(peerId: Long, body: ByteArray) {
        val cb = listeners[peerId] ?: error("no listener for $peerId")
        cb(body)
    }
}

class RpcTransportTest {

    @Test fun roundTripEncodesAndDecodesFrame() {
        val backend = FakeMessagingBackend()
        val left = RpcTransport(backend, peerId = 42)
        val right = RpcTransport(backend, peerId = 42)

        val payload = byteArrayOf(1, 2, 3, 4, 5)
        left.sendBytes(payload)

        assertEquals(1, backend.sent.size)
        val (peer, body, peerType) = backend.sent[0]
        assertEquals(42L, peer)
        assertEquals(1, peerType)
        val text = String(body, Charsets.UTF_8)
        assertTrue(text.startsWith(RpcTransport.MSG_PREFIX))

        backend.deliver(42, body)
        val got = right.recvBytes(1000)
        assertArrayEquals(payload, got)
    }

    @Test fun ignoresPlainChatMessages() {
        val backend = FakeMessagingBackend()
        val t = RpcTransport(backend, peerId = 1)
        backend.deliver(1, "salam".toByteArray(Charsets.UTF_8))
        assertNull(t.recvBytes(50))
        assertEquals(1, t.stats()["unexpected_prefix"])
    }

    @Test fun rejectsOversizeFrame() {
        val backend = FakeMessagingBackend()
        val t = RpcTransport(backend, peerId = 1)
        val oversize = ByteArray(RpcTransport.MTU + 1)
        var raised = false
        try {
            t.sendBytes(oversize)
        } catch (_: IllegalArgumentException) {
            raised = true
        }
        assertTrue(raised)
        assertTrue(backend.sent.isEmpty())
    }

    @Test fun closeDropsFurtherSends() {
        val backend = FakeMessagingBackend()
        val t = RpcTransport(backend, peerId = 1)
        t.close()
        t.close()
        t.sendBytes(byteArrayOf(9))
        assertTrue(backend.sent.isEmpty())
        assertNull(t.recvBytes(10))
    }

    @Test fun matchesPythonPrefixEncoding() {
        // Python: MSG_PREFIX = "​bb-vpn:" + base64
        val frame = "vpn".toByteArray()
        val b64 = Base64.getEncoder().encodeToString(frame)
        val full = RpcTransport.MSG_PREFIX + b64
        assertTrue(full.startsWith("​bb-vpn:"))
        val decoded = Base64.getDecoder().decode(full.substring(RpcTransport.MSG_PREFIX.length))
        assertArrayEquals(frame, decoded)
    }
}
