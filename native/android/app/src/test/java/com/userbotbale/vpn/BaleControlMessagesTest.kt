package com.userbot_bale.vpn

import com.userbot_bale.vpn.coordinator.BaleControlMessages
import org.junit.Assert.*
import org.junit.Test

class BaleControlMessagesTest {

    @Test
    fun `makeHello encodes magic prefix`() {
        val bytes = BaleControlMessages.makeHello("dev-1")
        val text = bytes.toString(Charsets.UTF_8)
        assertTrue("should start with BBCOORD1:", text.startsWith("BBCOORD1:"))
    }

    @Test
    fun `makeHello round-trips via decode`() {
        val bytes = BaleControlMessages.makeHello("android-client", "1.2.3")
        val msg = BaleControlMessages.decode(bytes)
        assertNotNull(msg)
        assertEquals(BaleControlMessages.Kind.HELLO, msg!!.kind)
        assertEquals("android-client", msg.str("client_id"))
        assertEquals("1.2.3", msg.str("app_version"))
    }

    @Test
    fun `decode returns null for wrong magic`() {
        val bytes = """{"kind":"HELLO","client_id":"x"}""".toByteArray()
        assertNull(BaleControlMessages.decode(bytes))
    }

    @Test
    fun `decode returns null for empty payload`() {
        assertNull(BaleControlMessages.decode(ByteArray(0)))
    }

    @Test
    fun `decode returns null for truncated magic`() {
        val bytes = "BBCOORD".toByteArray()
        assertNull(BaleControlMessages.decode(bytes))
    }

    @Test
    fun `parseAssign returns peerId and sessionId`() {
        val raw = BaleControlMessages.encode(
            BaleControlMessages.Kind.ASSIGN,
            mapOf("relay_peer_id" to 12345L, "session_id" to "abc", "expires_in_secs" to 30),
        )
        val msg = BaleControlMessages.decode(raw)!!
        val (peerId, sid) = BaleControlMessages.parseAssign(msg)!!
        assertEquals(12345L, peerId)
        assertEquals("abc", sid)
    }

    @Test
    fun `parseAssign returns null for wrong kind`() {
        val msg = BaleControlMessages.ControlMessage(
            BaleControlMessages.Kind.DENY,
            mapOf("relay_peer_id" to 1L, "session_id" to "x"),
        )
        assertNull(BaleControlMessages.parseAssign(msg))
    }

    @Test
    fun `parseDenyReason returns reason string`() {
        val raw = BaleControlMessages.encode(
            BaleControlMessages.Kind.DENY,
            mapOf("reason" to BaleControlMessages.DenyReason.NO_CAPACITY),
        )
        val msg = BaleControlMessages.decode(raw)!!
        assertEquals(BaleControlMessages.DenyReason.NO_CAPACITY, BaleControlMessages.parseDenyReason(msg))
    }

    @Test
    fun `encode decode strips kind and v from body`() {
        val raw = BaleControlMessages.encode(BaleControlMessages.Kind.ONLINE, mapOf("relay_id" to "r1"))
        val msg = BaleControlMessages.decode(raw)!!
        assertEquals(BaleControlMessages.Kind.ONLINE, msg.kind)
        assertEquals("r1", msg.str("relay_id"))
        assertFalse("body should not have kind key", msg.body.containsKey("kind"))
        assertFalse("body should not have v key", msg.body.containsKey("v"))
    }

    @Test
    fun `makeHello output matches Python protocol magic`() {
        val bytes = BaleControlMessages.makeHello("test")
        val PYTHON_MAGIC = "BBCOORD1:"
        val text = bytes.toString(Charsets.UTF_8)
        assertTrue(text.startsWith(PYTHON_MAGIC))
        assertTrue(text.contains("\"kind\":\"HELLO\""))
    }
}
