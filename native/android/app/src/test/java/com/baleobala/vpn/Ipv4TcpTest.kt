package com.baleobala.vpn

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress

class Ipv4TcpTest {

    @Test fun synBuildAndParseRoundTrips() {
        val src = InetAddress.getByName("10.77.0.2")
        val dst = InetAddress.getByName("93.184.216.34")
        val pkt = Ipv4Tcp.build(
            srcAddr = src, srcPort = 50001,
            dstAddr = dst, dstPort = 443,
            seq = 0xDEADBEEFL, ack = 0L,
            flags = Ipv4Tcp.Flag.SYN,
        )
        val parsed = Ipv4Tcp.parse(pkt, pkt.size)
        assertNotNull(parsed)
        assertEquals(50001, parsed!!.srcPort)
        assertEquals(443, parsed.dstPort)
        assertEquals(0xDEADBEEFL, parsed.seq)
        assertEquals(Ipv4Tcp.Flag.SYN, parsed.flags)
        assertEquals(0, parsed.payloadLength)
        assertArrayEquals(src.address, parsed.srcAddr)
        assertArrayEquals(dst.address, parsed.dstAddr)
    }

    @Test fun dataSegmentRoundTripsPayload() {
        val src = InetAddress.getByName("10.77.0.2")
        val dst = InetAddress.getByName("1.2.3.4")
        val payload = ByteArray(1000) { (it and 0xFF).toByte() }
        val pkt = Ipv4Tcp.build(
            srcAddr = src, srcPort = 1, dstAddr = dst, dstPort = 80,
            seq = 1L, ack = 2L,
            flags = Ipv4Tcp.Flag.ACK or Ipv4Tcp.Flag.PSH,
            payload = payload,
        )
        val parsed = Ipv4Tcp.parse(pkt, pkt.size)!!
        assertEquals(payload.size, parsed.payloadLength)
        val got = pkt.copyOfRange(parsed.payloadOffset, parsed.payloadOffset + parsed.payloadLength)
        assertArrayEquals(payload, got)
    }

    @Test fun parseRejectsTooShort() {
        assertNull(Ipv4Tcp.parse(ByteArray(20), 20))
    }

    @Test fun parseRejectsWrongProtocol() {
        // UDP packet must not parse as TCP.
        val src = InetAddress.getByName("1.2.3.4")
        val dst = InetAddress.getByName("5.6.7.8")
        val udp = Ipv4Packet.buildUdp(src, 1, dst, 2, byteArrayOf(0), 1)
        assertNull(Ipv4Tcp.parse(udp, udp.size))
    }

    @Test fun tcpChecksumValidatesViaPseudoHeader() {
        val src = InetAddress.getByName("10.0.0.1")
        val dst = InetAddress.getByName("10.0.0.2")
        val payload = "abc".toByteArray()
        val pkt = Ipv4Tcp.build(
            srcAddr = src, srcPort = 1234, dstAddr = dst, dstPort = 5678,
            seq = 100, ack = 200, flags = Ipv4Tcp.Flag.ACK, payload = payload,
        )
        // Recompute TCP checksum over pseudo-header + tcp segment;
        // for a valid checksummed packet the one's complement sum must be 0xFFFF.
        val tcpStart = 20
        val tcpLen = pkt.size - tcpStart
        var sum = 0
        // Pseudo header: src(4) + dst(4) + zero(1) + proto(1) + tcpLen(2)
        for (i in 12..15 step 2) {
            sum += ((pkt[i].toInt() and 0xff) shl 8) or (pkt[i + 1].toInt() and 0xff)
            if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
        }
        for (i in 16..19 step 2) {
            sum += ((pkt[i].toInt() and 0xff) shl 8) or (pkt[i + 1].toInt() and 0xff)
            if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
        }
        sum += Ipv4Packet.PROTO_TCP
        if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
        sum += tcpLen
        if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
        var i = tcpStart
        while (i < pkt.size - 1) {
            sum += ((pkt[i].toInt() and 0xff) shl 8) or (pkt[i + 1].toInt() and 0xff)
            if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
            i += 2
        }
        if (i < pkt.size) {
            sum += (pkt[i].toInt() and 0xff) shl 8
            if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
        }
        assertEquals(0xFFFF, sum)
    }

    @Test fun finAckFlagsCombineCorrectly() {
        val src = InetAddress.getByName("10.0.0.1")
        val dst = InetAddress.getByName("10.0.0.2")
        val pkt = Ipv4Tcp.build(
            srcAddr = src, srcPort = 1, dstAddr = dst, dstPort = 2,
            seq = 1, ack = 1,
            flags = Ipv4Tcp.Flag.FIN or Ipv4Tcp.Flag.ACK,
        )
        val parsed = Ipv4Tcp.parse(pkt, pkt.size)!!
        assertTrue(parsed.flags and Ipv4Tcp.Flag.FIN != 0)
        assertTrue(parsed.flags and Ipv4Tcp.Flag.ACK != 0)
        assertEquals(0, parsed.flags and Ipv4Tcp.Flag.SYN)
    }
}
