package com.userbot_bale.vpn

import com.userbot_bale.vpn.tunnel.VpnFraming
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnFramingTest {

    @Test fun encodeDecodeRoundtrips() {
        val payload = byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8, 9)
        val f = VpnFraming.Frame(0xABCD, 0x123456, VpnFraming.Flag.LAST, payload)
        val bytes = f.encode()
        assertEquals(VpnFraming.HEADER_SIZE + payload.size, bytes.size)
        assertEquals(VpnFraming.MAGIC.toByte(), bytes[0])
        assertEquals(VpnFraming.VERSION.toByte(), bytes[1])
        val back = VpnFraming.decode(bytes)
        assertNotNull(back)
        assertEquals(f, back)
    }

    @Test fun decodeRejectsWrongMagic() {
        val good = VpnFraming.Frame(1, 1, 0, ByteArray(0)).encode()
        good[0] = 0xAA.toByte()
        assertNull(VpnFraming.decode(good))
    }

    @Test fun decodeRejectsTooShort() {
        assertNull(VpnFraming.decode(ByteArray(VpnFraming.HEADER_SIZE - 1)))
    }

    @Test fun splitSinglePacketReturnsLastFrame() {
        val pkt = ByteArray(50) { it.toByte() }
        val frames = VpnFraming.splitPacket(pkt, sessId = 7, startSeq = 100, maxPayload = 200)
        assertEquals(1, frames.size)
        assertEquals(VpnFraming.Flag.LAST, frames[0].flags)
        assertEquals(100, frames[0].seq)
        assertArrayEquals(pkt, frames[0].payload)
    }

    @Test fun splitMultiPacketUsesSplitFlagAndLast() {
        val pkt = ByteArray(250) { (it and 0xFF).toByte() }
        val frames = VpnFraming.splitPacket(pkt, sessId = 9, startSeq = 0, maxPayload = 100)
        assertEquals(3, frames.size)
        assertEquals(VpnFraming.Flag.SPLIT, frames[0].flags)
        assertEquals(VpnFraming.Flag.SPLIT, frames[1].flags)
        assertEquals(VpnFraming.Flag.SPLIT or VpnFraming.Flag.LAST, frames[2].flags)
        assertEquals(0, frames[0].seq)
        assertEquals(1, frames[1].seq)
        assertEquals(2, frames[2].seq)
        // Payload concat must equal original packet.
        val rebuilt = frames.fold(ByteArray(0)) { a, f -> a + f.payload }
        assertArrayEquals(pkt, rebuilt)
    }

    @Test fun seqWrapsAt2Pow24() {
        val frames = VpnFraming.splitPacket(ByteArray(10), sessId = 0,
            startSeq = VpnFraming.SEQ_MODULO - 1, maxPayload = 5)
        assertEquals(2, frames.size)
        assertEquals(VpnFraming.SEQ_MODULO - 1, frames[0].seq)
        assertEquals(0, frames[1].seq)
    }
}
