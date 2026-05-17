package com.baleobala.vpn.tunnel

import com.baleobala.vpn.proto.Le

/**
 * VPN framing — exact port of [src/baleobala/vpn/framing_vpn.py](../../../../../../../../src/baleobala/vpn/framing_vpn.py).
 *
 * Header layout (8 bytes):
 *   offset  size  field    notes
 *   0       1     magic    0xBB
 *   1       1     version  0x01
 *   2       2     sess_id  u16 LE
 *   4       3     seq      24-bit LE
 *   7       1     flags    bit0 ACK, bit1 RETRY, bit2 LAST, bit3 SPLIT
 */
object VpnFraming {
    const val MAGIC: Int = 0xBB
    const val VERSION: Int = 0x01
    const val HEADER_SIZE: Int = 8
    const val SEQ_MODULO: Int = 1 shl 24
    const val SESS_MODULO: Int = 1 shl 16

    object Flag {
        const val ACK: Int = 0x01
        const val RETRY: Int = 0x02
        const val LAST: Int = 0x04
        const val SPLIT: Int = 0x08
    }

    data class Frame(val sessId: Int, val seq: Int, val flags: Int, val payload: ByteArray) {
        fun encode(): ByteArray {
            require(sessId in 0 until SESS_MODULO) { "sess_id out of range" }
            require(seq in 0 until SEQ_MODULO) { "seq out of range" }
            val out = ByteArray(HEADER_SIZE + payload.size)
            out[0] = MAGIC.toByte()
            out[1] = VERSION.toByte()
            Le.u16(out, 2, sessId)
            Le.u24(out, 4, seq)
            out[7] = (flags and 0xFF).toByte()
            System.arraycopy(payload, 0, out, HEADER_SIZE, payload.size)
            return out
        }

        override fun equals(other: Any?): Boolean = other is Frame &&
            sessId == other.sessId && seq == other.seq &&
            flags == other.flags && payload.contentEquals(other.payload)

        override fun hashCode(): Int {
            var h = sessId
            h = 31 * h + seq
            h = 31 * h + flags
            h = 31 * h + payload.contentHashCode()
            return h
        }
    }

    fun decode(buf: ByteArray): Frame? {
        if (buf.size < HEADER_SIZE) return null
        if ((buf[0].toInt() and 0xFF) != MAGIC) return null
        if ((buf[1].toInt() and 0xFF) != VERSION) return null
        val sessId = Le.readU16(buf, 2)
        val seq = Le.readU24(buf, 4)
        val flags = buf[7].toInt() and 0xFF
        val payload = buf.copyOfRange(HEADER_SIZE, buf.size)
        return Frame(sessId, seq, flags, payload)
    }

    /** Split one IP packet into 1..N frames using consecutive seq numbers. */
    fun splitPacket(
        packet: ByteArray,
        sessId: Int,
        startSeq: Int,
        maxPayload: Int,
    ): List<Frame> {
        require(maxPayload > 0) { "maxPayload must be positive" }
        if (packet.size <= maxPayload) {
            return listOf(Frame(sessId, startSeq % SEQ_MODULO, Flag.LAST, packet))
        }
        val out = ArrayList<Frame>()
        var i = 0
        var remaining = packet.size
        var seq = startSeq
        var off = 0
        var index = 0
        val totalChunks = (packet.size + maxPayload - 1) / maxPayload
        while (remaining > 0) {
            val len = minOf(maxPayload, remaining)
            val chunk = packet.copyOfRange(off, off + len)
            var flags = Flag.SPLIT
            if (index == totalChunks - 1) flags = flags or Flag.LAST
            out.add(Frame(sessId, seq % SEQ_MODULO, flags, chunk))
            off += len
            remaining -= len
            seq = (seq + 1) % SEQ_MODULO
            index++
            i++
        }
        return out
    }
}
