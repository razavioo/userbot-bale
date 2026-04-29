package com.baleobala.vpn

import java.net.InetAddress
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Minimal IPv4 + UDP packet helpers used by the userspace forwarder. */
object Ipv4Packet {
    const val PROTO_ICMP = 1
    const val PROTO_TCP = 6
    const val PROTO_UDP = 17

    data class Parsed(
        val ihlBytes: Int,
        val totalLength: Int,
        val protocol: Int,
        val srcAddr: ByteArray,
        val dstAddr: ByteArray,
        val payloadOffset: Int,
        val payloadLength: Int,
    )

    /** Parse the IPv4 header. Returns null if buf isn't a valid IPv4 packet. */
    fun parse(buf: ByteArray, length: Int): Parsed? {
        if (length < 20) return null
        val versionIhl = buf[0].toInt() and 0xff
        val version = versionIhl ushr 4
        if (version != 4) return null
        val ihl = versionIhl and 0x0f
        val ihlBytes = ihl * 4
        if (ihlBytes < 20 || length < ihlBytes) return null
        val totalLength = ((buf[2].toInt() and 0xff) shl 8) or (buf[3].toInt() and 0xff)
        if (totalLength > length) return null
        val protocol = buf[9].toInt() and 0xff
        val src = buf.copyOfRange(12, 16)
        val dst = buf.copyOfRange(16, 20)
        return Parsed(
            ihlBytes = ihlBytes,
            totalLength = totalLength,
            protocol = protocol,
            srcAddr = src,
            dstAddr = dst,
            payloadOffset = ihlBytes,
            payloadLength = totalLength - ihlBytes,
        )
    }

    /** Parse a UDP header sitting at [offset] in [buf]. Returns (srcPort, dstPort, dataOffset, dataLen). */
    fun parseUdp(buf: ByteArray, offset: Int, udpLen: Int): IntArray? {
        if (udpLen < 8) return null
        val srcPort = ((buf[offset].toInt() and 0xff) shl 8) or (buf[offset + 1].toInt() and 0xff)
        val dstPort = ((buf[offset + 2].toInt() and 0xff) shl 8) or (buf[offset + 3].toInt() and 0xff)
        val length = ((buf[offset + 4].toInt() and 0xff) shl 8) or (buf[offset + 5].toInt() and 0xff)
        if (length < 8 || length > udpLen) return null
        return intArrayOf(srcPort, dstPort, offset + 8, length - 8)
    }

    /** Build an IPv4+UDP packet from the given parameters. */
    fun buildUdp(
        srcAddr: InetAddress,
        srcPort: Int,
        dstAddr: InetAddress,
        dstPort: Int,
        payload: ByteArray,
        payloadLen: Int,
        ttl: Int = 64,
        ident: Int = 0,
    ): ByteArray {
        val srcBytes = srcAddr.address
        val dstBytes = dstAddr.address
        require(srcBytes.size == 4 && dstBytes.size == 4) { "IPv4 only" }
        val totalLen = 20 + 8 + payloadLen
        val out = ByteArray(totalLen)
        val bb = ByteBuffer.wrap(out).order(ByteOrder.BIG_ENDIAN)
        // IPv4 header
        bb.put(0x45.toByte())                  // version 4, IHL 5
        bb.put(0x00)                           // DSCP/ECN
        bb.putShort(totalLen.toShort())        // total length
        bb.putShort(ident.toShort())           // ident
        bb.putShort(0x0000)                    // flags + fragment offset
        bb.put(ttl.toByte())                   // TTL
        bb.put(PROTO_UDP.toByte())             // protocol
        bb.putShort(0x0000)                    // header checksum (zero, fill in)
        bb.put(srcBytes)
        bb.put(dstBytes)
        // UDP header
        bb.putShort(srcPort.toShort())
        bb.putShort(dstPort.toShort())
        bb.putShort((8 + payloadLen).toShort())
        bb.putShort(0x0000)                    // UDP checksum (optional in IPv4, leave 0)
        bb.put(payload, 0, payloadLen)

        // IPv4 header checksum
        val cksum = onesComplementSum(out, 0, 20)
        out[10] = (cksum ushr 8 and 0xff).toByte()
        out[11] = (cksum and 0xff).toByte()
        return out
    }

    private fun onesComplementSum(buf: ByteArray, offset: Int, length: Int): Int {
        var sum = 0
        var i = 0
        while (i < length - 1) {
            val word = ((buf[offset + i].toInt() and 0xff) shl 8) or (buf[offset + i + 1].toInt() and 0xff)
            sum += word
            if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
            i += 2
        }
        if (i < length) {
            sum += (buf[offset + i].toInt() and 0xff) shl 8
            if (sum and 0x10000 != 0) sum = (sum and 0xffff) + 1
        }
        return sum.inv() and 0xffff
    }
}
