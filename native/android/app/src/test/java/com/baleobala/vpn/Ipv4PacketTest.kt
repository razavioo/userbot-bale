package com.baleobala.vpn

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import java.net.InetAddress

class Ipv4PacketTest {

    @Test fun buildUdpRoundTripsViaParse() {
        val src = InetAddress.getByName("10.77.0.2")
        val dst = InetAddress.getByName("8.8.8.8")
        val payload = "hello-udp".toByteArray()
        val pkt = Ipv4Packet.buildUdp(src, 41000, dst, 53, payload, payload.size)
        val ip = Ipv4Packet.parse(pkt, pkt.size)
        assertNotNull(ip)
        assertEquals(Ipv4Packet.PROTO_UDP, ip!!.protocol)
        assertArrayEquals(src.address, ip.srcAddr)
        assertArrayEquals(dst.address, ip.dstAddr)

        val udp = Ipv4Packet.parseUdp(pkt, ip.payloadOffset, ip.payloadLength)
        assertNotNull(udp)
        val (sp, dp, dataOff, dataLen) = listOf(udp!![0], udp[1], udp[2], udp[3])
        assertEquals(41000, sp)
        assertEquals(53, dp)
        assertEquals(payload.size, dataLen)
        assertArrayEquals(payload, pkt.copyOfRange(dataOff, dataOff + dataLen))
    }

    @Test fun parseRejectsTooShort() {
        assertNull(Ipv4Packet.parse(ByteArray(10), 10))
    }

    @Test fun parseRejectsNonIpv4() {
        val buf = ByteArray(20)
        buf[0] = 0x65 // version 6
        assertNull(Ipv4Packet.parse(buf, 20))
    }

    @Test fun parseRejectsTotalLengthLargerThanBuffer() {
        val src = InetAddress.getByName("1.2.3.4")
        val dst = InetAddress.getByName("5.6.7.8")
        val pkt = Ipv4Packet.buildUdp(src, 1, dst, 2, ByteArray(0), 0)
        // Truncate by 1 byte; parser should report null because totalLength > length.
        assertNull(Ipv4Packet.parse(pkt, pkt.size - 1))
    }

    @Test fun ipChecksumIsCorrect() {
        val src = InetAddress.getByName("192.168.1.1")
        val dst = InetAddress.getByName("192.168.1.2")
        val pkt = Ipv4Packet.buildUdp(src, 1234, dst, 5678, byteArrayOf(0xAA.toByte(), 0xBB.toByte()), 2)
        // Recompute checksum over the IP header (with checksum field treated as zero, then verified):
        // After insertion, the one's complement sum across the 20-byte header must equal 0xFFFF.
        var sum = 0
        for (i in 0 until 20 step 2) {
            val w = ((pkt[i].toInt() and 0xff) shl 8) or (pkt[i + 1].toInt() and 0xff)
            sum += w
            if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
        }
        assertEquals(0xFFFF, sum)
    }

    @Test fun parseUdpRejectsTooShort() {
        assertNull(Ipv4Packet.parseUdp(ByteArray(4), 0, 4))
    }
}
