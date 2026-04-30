package com.baleobala.vpn.proto

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Hand-rolled protobuf wire-format codec — same shape as the Python
 * [src/baleobala/bale/protos.py](../../../../../../../../src/baleobala/bale/protos.py)
 * helpers (_enc_tag/_enc_varint/_enc_len_delim and _walk_len_delim).
 *
 * Only the wire types we need for Bale auth are implemented:
 *   - VARINT (0): varuint64, signed via zig-zag if requested
 *   - LEN_DELIM (2): bytes / strings / sub-messages
 *
 * No reflection, no dependencies. Matches the Python's manual approach
 * so we can side-step protoc tooling for the small handful of messages
 * we actually use.
 */
object ProtoCodec {
    const val WT_VARINT = 0
    const val WT_LEN_DELIM = 2

    fun encTag(out: ByteArrayOutputStream, fieldNumber: Int, wireType: Int) {
        encVarint(out, ((fieldNumber.toLong() shl 3) or wireType.toLong()))
    }

    fun encVarint(out: ByteArrayOutputStream, value: Long) {
        var v = value
        while (v and 0x7Fu.toLong().inv() != 0L) {
            out.write(((v and 0x7F) or 0x80).toInt())
            v = v ushr 7
        }
        out.write((v and 0x7F).toInt())
    }

    fun encLenDelim(out: ByteArrayOutputStream, fieldNumber: Int, payload: ByteArray) {
        encTag(out, fieldNumber, WT_LEN_DELIM)
        encVarint(out, payload.size.toLong())
        out.write(payload)
    }

    fun encVarintField(out: ByteArrayOutputStream, fieldNumber: Int, value: Long) {
        encTag(out, fieldNumber, WT_VARINT)
        encVarint(out, value)
    }

    /** Walk top-level fields of [buf]. Yields (fieldNumber, wireType, value).
     *  For VARINT, value is Long. For LEN_DELIM, value is ByteArray (a slice). */
    fun walk(buf: ByteArray): List<Field> {
        val out = ArrayList<Field>()
        var off = 0
        while (off < buf.size) {
            val tagStart = off
            val (tag, after) = readVarint(buf, off) ?: break
            off = after
            val fieldNumber = (tag ushr 3).toInt()
            val wireType = (tag and 0x07).toInt()
            when (wireType) {
                WT_VARINT -> {
                    val (v, after2) = readVarint(buf, off) ?: return out
                    out.add(Field(fieldNumber, wireType, FieldValue.VarInt(v)))
                    off = after2
                }
                WT_LEN_DELIM -> {
                    val (len, after2) = readVarint(buf, off) ?: return out
                    val l = len.toInt()
                    if (after2 + l > buf.size || l < 0) return out
                    val slice = buf.copyOfRange(after2, after2 + l)
                    out.add(Field(fieldNumber, wireType, FieldValue.LenDelim(slice)))
                    off = after2 + l
                }
                1 -> off += 8  // FIXED64
                5 -> off += 4  // FIXED32
                else -> {
                    // Unknown / group — bail rather than corrupt cursor
                    return out
                }
            }
            if (off <= tagStart) return out
        }
        return out
    }

    fun readVarint(buf: ByteArray, start: Int): Pair<Long, Int>? {
        var shift = 0
        var result = 0L
        var off = start
        while (off < buf.size) {
            val b = buf[off].toInt() and 0xFF
            result = result or ((b and 0x7F).toLong() shl shift)
            off++
            if (b and 0x80 == 0) return Pair(result, off)
            shift += 7
            if (shift > 63) return null
        }
        return null
    }

    data class Field(val number: Int, val wireType: Int, val value: FieldValue)

    sealed class FieldValue {
        data class VarInt(val v: Long) : FieldValue()
        data class LenDelim(val bytes: ByteArray) : FieldValue() {
            override fun equals(other: Any?): Boolean =
                other is LenDelim && bytes.contentEquals(other.bytes)
            override fun hashCode(): Int = bytes.contentHashCode()
        }
    }
}

/** Little-endian helpers used by the VPN framing layer. */
object Le {
    fun u16(out: ByteArray, offset: Int, value: Int) {
        out[offset] = (value and 0xFF).toByte()
        out[offset + 1] = ((value ushr 8) and 0xFF).toByte()
    }

    fun readU16(buf: ByteArray, offset: Int): Int =
        (buf[offset].toInt() and 0xFF) or ((buf[offset + 1].toInt() and 0xFF) shl 8)

    fun u24(out: ByteArray, offset: Int, value: Int) {
        out[offset] = (value and 0xFF).toByte()
        out[offset + 1] = ((value ushr 8) and 0xFF).toByte()
        out[offset + 2] = ((value ushr 16) and 0xFF).toByte()
    }

    fun readU24(buf: ByteArray, offset: Int): Int =
        (buf[offset].toInt() and 0xFF) or
        ((buf[offset + 1].toInt() and 0xFF) shl 8) or
        ((buf[offset + 2].toInt() and 0xFF) shl 16)
}
