package com.userbot_bale.vpn

import java.net.InetAddress
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * IPv4 + TCP packet helpers used by [TcpForwarder]. Just enough to
 * fabricate SYN-ACK / data / FIN-ACK / RST segments back to the TUN
 * with correct IP and TCP (pseudo-header) checksums.
 */
object Ipv4Tcp {

    object Flag {
        const val FIN = 0x01
        const val SYN = 0x02
        const val RST = 0x04
        const val PSH = 0x08
        const val ACK = 0x10
    }

    data class Parsed(
        val srcAddr: ByteArray,
        val dstAddr: ByteArray,
        val srcPort: Int,
        val dstPort: Int,
        val seq: Long,         // unsigned 32-bit
        val ack: Long,         // unsigned 32-bit
        val flags: Int,
        val window: Int,
        val tcpHeaderLen: Int,
        val payloadOffset: Int,
        val payloadLength: Int,
    )

    /** Parse a TCP segment from an IPv4+TCP buffer. The IP header must
     *  start at offset 0; TCP header starts at [ipHeaderLen]. */
    fun parse(buf: ByteArray, length: Int): Parsed? {
        val parsedIp = Ipv4Packet.parse(buf, length) ?: return null
        if (parsedIp.protocol != Ipv4Packet.PROTO_TCP) return null
        val tcpStart = parsedIp.payloadOffset
        if (length < tcpStart + 20) return null
        val srcPort = u16(buf, tcpStart)
        val dstPort = u16(buf, tcpStart + 2)
        val seq = u32(buf, tcpStart + 4)
        val ack = u32(buf, tcpStart + 8)
        val dataOffsetWords = (buf[tcpStart + 12].toInt() and 0xFF) ushr 4
        val tcpHeaderLen = dataOffsetWords * 4
        val flags = buf[tcpStart + 13].toInt() and 0xFF
        val window = u16(buf, tcpStart + 14)
        val payloadOffset = tcpStart + tcpHeaderLen
        val payloadLength = parsedIp.payloadLength - tcpHeaderLen
        if (payloadLength < 0) return null
        return Parsed(
            srcAddr = parsedIp.srcAddr,
            dstAddr = parsedIp.dstAddr,
            srcPort = srcPort,
            dstPort = dstPort,
            seq = seq,
            ack = ack,
            flags = flags,
            window = window,
            tcpHeaderLen = tcpHeaderLen,
            payloadOffset = payloadOffset,
            payloadLength = payloadLength,
        )
    }

    /** Build an IPv4+TCP segment (no TCP options). */
    fun build(
        srcAddr: InetAddress,
        srcPort: Int,
        dstAddr: InetAddress,
        dstPort: Int,
        seq: Long,
        ack: Long,
        flags: Int,
        window: Int = 65535,
        payload: ByteArray = ByteArray(0),
        payloadOffset: Int = 0,
        payloadLength: Int = payload.size,
    ): ByteArray {
        val ipHdr = 20
        val tcpHdr = 20
        val total = ipHdr + tcpHdr + payloadLength
        val out = ByteArray(total)
        // IPv4
        out[0] = 0x45.toByte()                  // ver/IHL
        out[1] = 0x00
        out[2] = ((total ushr 8) and 0xFF).toByte()
        out[3] = (total and 0xFF).toByte()
        out[4] = 0; out[5] = 0                  // ident
        out[6] = 0; out[7] = 0                  // flags + frag
        out[8] = 64.toByte()                    // TTL
        out[9] = Ipv4Packet.PROTO_TCP.toByte()  // protocol
        out[10] = 0; out[11] = 0                // header checksum (filled later)
        System.arraycopy(srcAddr.address, 0, out, 12, 4)
        System.arraycopy(dstAddr.address, 0, out, 16, 4)
        // TCP
        out[20] = ((srcPort ushr 8) and 0xFF).toByte()
        out[21] = (srcPort and 0xFF).toByte()
        out[22] = ((dstPort ushr 8) and 0xFF).toByte()
        out[23] = (dstPort and 0xFF).toByte()
        putU32(out, 24, seq)
        putU32(out, 28, ack)
        out[32] = (5 shl 4).toByte()            // dataOffset=5 words (no options), reserved=0
        out[33] = (flags and 0xFF).toByte()
        out[34] = ((window ushr 8) and 0xFF).toByte()
        out[35] = (window and 0xFF).toByte()
        out[36] = 0; out[37] = 0                // checksum (filled later)
        out[38] = 0; out[39] = 0                // urgent ptr
        if (payloadLength > 0) System.arraycopy(payload, payloadOffset, out, 40, payloadLength)

        // IPv4 header checksum
        val ipCksum = ipHeaderChecksum(out)
        out[10] = ((ipCksum ushr 8) and 0xFF).toByte()
        out[11] = (ipCksum and 0xFF).toByte()
        // TCP checksum (over pseudo-header + tcp-header + tcp-payload)
        val tcpCksum = tcpChecksum(out)
        out[36] = ((tcpCksum ushr 8) and 0xFF).toByte()
        out[37] = (tcpCksum and 0xFF).toByte()
        return out
    }

    private fun u16(buf: ByteArray, off: Int): Int =
        ((buf[off].toInt() and 0xFF) shl 8) or (buf[off + 1].toInt() and 0xFF)
    private fun u32(buf: ByteArray, off: Int): Long =
        ((buf[off].toLong() and 0xFF) shl 24) or
        ((buf[off + 1].toLong() and 0xFF) shl 16) or
        ((buf[off + 2].toLong() and 0xFF) shl 8) or
        (buf[off + 3].toLong() and 0xFF)
    private fun putU32(buf: ByteArray, off: Int, v: Long) {
        buf[off] = ((v ushr 24) and 0xFF).toByte()
        buf[off + 1] = ((v ushr 16) and 0xFF).toByte()
        buf[off + 2] = ((v ushr 8) and 0xFF).toByte()
        buf[off + 3] = (v and 0xFF).toByte()
    }

    private fun ipHeaderChecksum(buf: ByteArray): Int = onesComplementSum(buf, 0, 20)

    private fun tcpChecksum(buf: ByteArray): Int {
        val ipTotal = u16(buf, 2)
        val tcpLen = ipTotal - 20
        // pseudo-header: src(4) || dst(4) || zero(1) || proto(1) || tcpLen(2)
        var sum = 0
        sum = addWord(sum, u16(buf, 12))
        sum = addWord(sum, u16(buf, 14))
        sum = addWord(sum, u16(buf, 16))
        sum = addWord(sum, u16(buf, 18))
        sum = addWord(sum, Ipv4Packet.PROTO_TCP)
        sum = addWord(sum, tcpLen)
        // TCP header + payload (with checksum field zero already)
        var i = 20
        val end = 20 + tcpLen
        while (i + 1 < end) {
            sum = addWord(sum, u16(buf, i))
            i += 2
        }
        if (i < end) {
            sum = addWord(sum, (buf[i].toInt() and 0xFF) shl 8)
        }
        return sum.inv() and 0xFFFF
    }

    private fun onesComplementSum(buf: ByteArray, offset: Int, length: Int): Int {
        var sum = 0
        var i = 0
        while (i < length - 1) {
            sum = addWord(sum, ((buf[offset + i].toInt() and 0xFF) shl 8) or (buf[offset + i + 1].toInt() and 0xFF))
            i += 2
        }
        if (i < length) {
            sum = addWord(sum, (buf[offset + i].toInt() and 0xFF) shl 8)
        }
        return sum.inv() and 0xFFFF
    }

    private fun addWord(sum: Int, word: Int): Int {
        var s = sum + (word and 0xFFFF)
        if (s and 0x10000 != 0) s = (s and 0xFFFF) + 1
        return s
    }
}
