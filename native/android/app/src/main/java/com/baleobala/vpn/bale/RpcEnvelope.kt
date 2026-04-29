package com.baleobala.vpn.bale

import com.baleobala.vpn.proto.ProtoCodec
import java.io.ByteArrayOutputStream

/**
 * Bale RPC envelope — port of [src/baleobala/bale/rpc_envelope.py](../../../../../../../../src/baleobala/bale/rpc_envelope.py).
 *
 * Wire layout for a Request frame (binary WS message):
 *   outer field 1 (len-delim) wraps {
 *     field 1 string  service
 *     field 2 string  method
 *     field 3 bytes   payload
 *     field 4 metadata-map (custom encoding — see _encMetadata)
 *     field 5 varint  seq
 *   }
 *
 * Response frames echo seq either inline (field 3 or 5) or wrapped in
 * outer field 1's sub-message.
 */
object RpcEnvelope {
    val DEFAULT_METADATA: Map<String, String> = mapOf(
        "app_version" to "151668",
        "browser_type" to "1",
        "browser_version" to "147.0.0.0",
        "os_type" to "4",
        "mt_app_version" to "151668",
        "mt_browser_type" to "1",
        "mt_browser_version" to "147.0.0.0",
        "mt_os_type" to "4",
    )

    fun encodeRequest(
        service: String,
        method: String,
        payload: ByteArray,
        seq: Int,
        metadata: Map<String, String> = DEFAULT_METADATA,
    ): ByteArray {
        val body = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(body, 1, service.toByteArray(Charsets.UTF_8))
        ProtoCodec.encLenDelim(body, 2, method.toByteArray(Charsets.UTF_8))
        if (payload.isNotEmpty()) ProtoCodec.encLenDelim(body, 3, payload)
        body.write(encodeMetadata(metadata))
        ProtoCodec.encVarintField(body, 5, seq.toLong())
        val outer = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(outer, 1, body.toByteArray())
        return outer.toByteArray()
    }

    private fun encodeMetadata(md: Map<String, String>): ByteArray {
        val entries = ByteArrayOutputStream()
        for ((k, v) in md) {
            val keyBytes = ByteArrayOutputStream().also {
                ProtoCodec.encLenDelim(it, 1, k.toByteArray(Charsets.UTF_8))
            }.toByteArray()
            val valWrapInner = ByteArrayOutputStream().also {
                ProtoCodec.encLenDelim(it, 1, v.toByteArray(Charsets.UTF_8))
            }.toByteArray()
            val valWrap = ByteArrayOutputStream().also {
                ProtoCodec.encLenDelim(it, 2, valWrapInner)
            }.toByteArray()
            ProtoCodec.encLenDelim(entries, 1, keyBytes + valWrap)
        }
        val out = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(out, 4, entries.toByteArray())
        return out.toByteArray()
    }

    data class Response(val seq: Int?, val payload: ByteArray, val raw: ByteArray) {
        override fun equals(other: Any?): Boolean = other is Response &&
            seq == other.seq && payload.contentEquals(other.payload) && raw.contentEquals(other.raw)
        override fun hashCode(): Int = 31 * (31 * (seq ?: 0) + payload.contentHashCode()) + raw.contentHashCode()
    }

    fun decodeResponse(buf: ByteArray): Response = decodeResponseInner(buf, top = true).copy(raw = buf)

    private fun decodeResponseInner(buf: ByteArray, top: Boolean): Response {
        var seq: Int? = null
        var payload = ByteArray(0)
        for (f in ProtoCodec.walk(buf)) {
            when (f.value) {
                is ProtoCodec.FieldValue.VarInt -> {
                    if (f.number == 3 || f.number == 5) seq = f.value.v.toInt()
                }
                is ProtoCodec.FieldValue.LenDelim -> {
                    when (f.number) {
                        1 -> if (top) {
                            val sub = decodeResponseInner(f.value.bytes, top = false)
                            if (sub.seq != null && seq == null) seq = sub.seq
                            if (sub.payload.isNotEmpty()) payload = sub.payload
                        }
                        2 -> payload = f.value.bytes
                    }
                }
            }
        }
        return Response(seq, payload, buf)
    }
}
