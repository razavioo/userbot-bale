package com.baleobala.vpn.bale

import com.baleobala.vpn.proto.ProtoCodec
import java.io.ByteArrayOutputStream

/**
 * Hand-rolled subset of Bale's protobuf messages — port of the
 * relevant sections of [src/baleobala/bale/protos.py](../../../../../../../../src/baleobala/bale/protos.py).
 *
 * Same approach as the Python: write the wire format by hand for the
 * small handful of messages we actually need (StartPhoneAuth,
 * ValidateCode, GetJWTToken). Avoids pulling in protoc/protobuf-kotlin.
 */
object BaleProtos {
    const val AUTH_SERVICE = "bale.auth.v1.Auth"
    const val WEB_APP_ID = 4L
    const val WEB_API_KEY = "C28D46DC4C3A7A26564BFCC48B929086A95C93C98E789A19847BEE8627DE4E7D"

    /**
     * RequestStartPhoneAuth — fields:
     *   1: phone_number (int64)
     *   2: app_id (int64)
     *   3: api_key (string)
     *   4: device_hash (bytes)
     *   5: device_title (string)
     *   6: time_zone (string, optional)
     *   7: preferred_languages (repeated string)
     *   9: send_code_type (int32, optional)
     *  10: options (int32, optional)
     */
    fun encodeStartPhoneAuth(
        phoneNumber: Long,
        appId: Long = WEB_APP_ID,
        apiKey: String = WEB_API_KEY,
        deviceHash: ByteArray = ByteArray(0),
        deviceTitle: String = "baleobala",
        timeZone: String = "",
        preferredLanguages: List<String> = emptyList(),
        sendCodeType: Int = 1,            // DEFAULT — matches captures/rpcs/00_*StartPhoneAuth.req.bin
        options: IntArray = intArrayOf(0, 1), // [SUPPORT_TELEGRAM_GATEWAY, SIX_DIGIT_OTP] — same as web client
    ): ByteArray {
        val out = ByteArrayOutputStream()
        ProtoCodec.encVarintField(out, 1, phoneNumber)
        ProtoCodec.encVarintField(out, 2, appId)
        ProtoCodec.encLenDelim(out, 3, apiKey.toByteArray(Charsets.UTF_8))
        ProtoCodec.encLenDelim(out, 4, deviceHash)
        ProtoCodec.encLenDelim(out, 5, deviceTitle.toByteArray(Charsets.UTF_8))
        if (timeZone.isNotEmpty())
            ProtoCodec.encLenDelim(out, 6, timeZone.toByteArray(Charsets.UTF_8))
        for (lang in preferredLanguages)
            ProtoCodec.encLenDelim(out, 7, lang.toByteArray(Charsets.UTF_8))
        if (sendCodeType != 0) ProtoCodec.encVarintField(out, 9, sendCodeType.toLong())
        if (options.isNotEmpty()) {
            // Packed repeated varint per proto3: tag is wire-type 2, payload is concat of varints.
            val packed = ByteArrayOutputStream()
            for (v in options) ProtoCodec.encVarint(packed, v.toLong())
            ProtoCodec.encLenDelim(out, 10, packed.toByteArray())
        }
        return out.toByteArray()
    }

    /**
     * RequestValidateCode — fields:
     *   1: transaction_hash (string)
     *   2: code (string)
     *   3: is_jwt (bool, encoded as 1 if true; omitted otherwise)
     */
    fun encodeValidateCode(transactionHash: String, code: String, isJwt: Boolean = true): ByteArray {
        val out = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(out, 1, transactionHash.toByteArray(Charsets.UTF_8))
        ProtoCodec.encLenDelim(out, 2, code.toByteArray(Charsets.UTF_8))
        if (isJwt) ProtoCodec.encVarintField(out, 3, 1L)
        return out.toByteArray()
    }

    /** RequestGetJWTToken: empty body — server identifies session via headers. */
    fun encodeGetJWTToken(): ByteArray = ByteArray(0)

    /**
     * Find the JWT inside a ResponseAuth payload using the same heuristic
     * as the Python source: regex-scan for an "eyJ..." token.
     */
    fun parseJwt(buf: ByteArray): String? {
        val asAscii = buf.toString(Charsets.ISO_8859_1)
        val m = Regex("(eyJ[A-Za-z0-9_\\-.]{50,})").find(asAscii) ?: return null
        return m.groupValues[1]
    }

    /** Scan ResponseAuth for the user_id (10_000 .. 5_000_000_000 range). */
    fun parseUserIdFromAuth(buf: ByteArray): Long? {
        val candidates = ArrayList<Long>()
        for (f in ProtoCodec.walk(buf)) {
            when (val v = f.value) {
                is ProtoCodec.FieldValue.VarInt -> if (v.v in 10_000L..5_000_000_000L) candidates.add(v.v)
                is ProtoCodec.FieldValue.LenDelim -> for (sub in ProtoCodec.walk(v.bytes)) {
                    val s = sub.value
                    if (s is ProtoCodec.FieldValue.VarInt && s.v in 10_000L..5_000_000_000L)
                        candidates.add(s.v)
                }
            }
        }
        return candidates.firstOrNull()
    }

    /** Scan ResponseStartPhoneAuth for the transaction_hash string. */
    fun parseTransactionHash(buf: ByteArray): String? {
        // Walk len-delim fields and pick the first string of plausible shape.
        for (f in ProtoCodec.walk(buf)) {
            val v = f.value
            if (v is ProtoCodec.FieldValue.LenDelim && v.bytes.size in 16..256) {
                val s = String(v.bytes, Charsets.US_ASCII)
                if (s.matches(Regex("[A-Za-z0-9_\\-]{16,}"))) return s
            }
        }
        // Fallback: regex scan over raw bytes (matches the Python's behavior).
        val asAscii = buf.toString(Charsets.ISO_8859_1)
        val m = Regex("([A-Za-z0-9_\\-]{20,})").find(asAscii) ?: return null
        return m.groupValues[1]
    }
}
