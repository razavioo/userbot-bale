package com.userbot_bale.vpn.bale

import com.userbot_bale.vpn.proto.ProtoCodec
import java.io.ByteArrayOutputStream

/**
 * Hand-rolled subset of Bale's protobuf messages — port of the
 * relevant sections of [src/userbot-bale/bale/protos.py](../../../../../../../../src/userbot-bale/bale/protos.py).
 *
 * Same approach as the Python: write the wire format by hand for the
 * small handful of messages we actually need (StartPhoneAuth,
 * ValidateCode, GetJWTToken). Avoids pulling in protoc/protobuf-kotlin.
 */
object BaleProtos {
    const val AUTH_SERVICE = "bale.auth.v1.Auth"
    const val MEET_SERVICE = "bale.meet.v1.Meet"
    const val MESSAGING_SERVICE = "bale.messaging.v2.Messaging"
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
        deviceTitle: String = "userbot-bale",
        timeZone: String = "",
        preferredLanguages: List<String> = emptyList(),
        sendCodeType: Int = 3,            // SMS — explicit request, avoids server's USSD default
        // [SIX_DIGIT_OTP] only — declaring SUPPORT_TELEGRAM_GATEWAY (0) lets the
        // server pick the Telegram-bot delivery path, which fails for accounts
        // not registered with Telegram. Dropping 0 forces the SMS/BALEONLY fallback.
        options: IntArray = intArrayOf(1),
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
     * RequestValidateCode — fields (decoded from web.bale.ai bundle's
     * `v.encode()` for ValidateCode and verified against
     * captures/rpcs/01_bale.auth.v1.Auth__ValidateCode.req.bin):
     *   1: transaction_hash (string)
     *   2: code (string)
     *   3: is_jwt (google.protobuf.BoolValue — sub-msg with bool at field 1)
     *   4: future_auth_tokens (repeated string, packed in encoder)
     *
     * Encoding `is_jwt` as a scalar varint instead of a BoolValue is
     * silently accepted by the server, but the field is dropped (wire-
     * type mismatch) and the server then withholds the JWT from
     * ResponseAuth. That manifests as "no JWT in body, no Set-Cookie"
     * and forces the caller down the GetJWTToken / SignUp fallback
     * paths, which 401 / "user already registered" for many accounts.
     */
    fun encodeValidateCode(transactionHash: String, code: String, isJwt: Boolean = true): ByteArray {
        val out = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(out, 1, transactionHash.toByteArray(Charsets.UTF_8))
        ProtoCodec.encLenDelim(out, 2, code.toByteArray(Charsets.UTF_8))
        // BoolValue { value: bool=1 at field 1 }
        val boolValue = ByteArrayOutputStream()
        ProtoCodec.encVarintField(boolValue, 1, if (isJwt) 1L else 0L)
        ProtoCodec.encLenDelim(out, 3, boolValue.toByteArray())
        return out.toByteArray()
    }

    /** RequestGetJWTToken: empty body — server identifies session via headers. */
    fun encodeGetJWTToken(): ByteArray = ByteArray(0)

    /**
     * RequestSignUp — fields (decoded directly from web.bale.ai bundle's
     * `k.encode()` for SignUp request):
     *   1: transaction_hash (string)
     *   2: name (string)
     *   3: sex (int32 enum; 0=UNKNOWN)
     *   4: password (google.protobuf.StringValue — sub-msg with field 1 string)
     *
     * Returned by Bale on a fresh phone-number that completed ValidateCode
     * but has not yet finished signup (no JWT in ResponseAuth).
     */
    fun encodeSignUp(
        transactionHash: String,
        name: String,
        sex: Int = 0,
        password: String? = null,
    ): ByteArray {
        val out = ByteArrayOutputStream()
        if (transactionHash.isNotEmpty())
            ProtoCodec.encLenDelim(out, 1, transactionHash.toByteArray(Charsets.UTF_8))
        if (name.isNotEmpty())
            ProtoCodec.encLenDelim(out, 2, name.toByteArray(Charsets.UTF_8))
        if (sex != 0)
            ProtoCodec.encVarintField(out, 3, sex.toLong())
        if (password != null) {
            // google.protobuf.StringValue: { value: string at field 1 }
            val inner = ByteArrayOutputStream()
            ProtoCodec.encLenDelim(inner, 1, password.toByteArray(Charsets.UTF_8))
            ProtoCodec.encLenDelim(out, 4, inner.toByteArray())
        }
        return out.toByteArray()
    }

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

    data class StartPhoneAuthResponse(
        val transactionHash: String,
        val sendCodeTypeChosen: Int?,    // SendCodeType the server picked (e.g. 3=SMS, 10=USSD)
        val ussdInstruction: String?,    // e.g. "*737*69#" if server wants caller to dial it
        val codeLength: Int?,
        val waitTimeSec: Int?,
        val raw: ByteArray,
    )

    /**
     * Walk a captured ResponseStartPhoneAuth and extract the parts the user
     * needs to act on. Field numbers were inferred from
     * captures/rpcs/00_bale.auth.v1.Auth__StartPhoneAuth.res.bin and the
     * SendCodeType enum (UNKNOWN=0..TELEGRAM_GATEWAY=12) — field 10 holds
     * a sub-message with `{ kind: SendCodeType, data: <ussd-string> }` when
     * the server picked USSD or similar interactive delivery.
     */
    fun parseStartPhoneAuth(buf: ByteArray): StartPhoneAuthResponse {
        var tx = ""
        var sendCodeType: Int? = null
        var ussd: String? = null
        var codeLength: Int? = null
        var waitTime: Int? = null
        for (f in ProtoCodec.walk(buf)) {
            when (f.number) {
                1 -> if (f.value is ProtoCodec.FieldValue.LenDelim)
                    tx = String(f.value.bytes, Charsets.US_ASCII)
                7 -> if (f.value is ProtoCodec.FieldValue.VarInt)
                    sendCodeType = f.value.v.toInt()
                8 -> if (f.value is ProtoCodec.FieldValue.LenDelim) {
                    // typically { wait_time }
                    val sub = ProtoCodec.walk(f.value.bytes)
                    val first = sub.firstOrNull()?.value
                    if (first is ProtoCodec.FieldValue.VarInt) waitTime = first.v.toInt()
                }
                9 -> if (f.value is ProtoCodec.FieldValue.LenDelim) {
                    val sub = ProtoCodec.walk(f.value.bytes)
                    val first = sub.firstOrNull()?.value
                    if (first is ProtoCodec.FieldValue.VarInt) codeLength = first.v.toInt()
                }
                10 -> if (f.value is ProtoCodec.FieldValue.LenDelim) {
                    // sub-message: field 1 varint = SendCodeType, field 2 = string payload (USSD code etc.)
                    for (sub in ProtoCodec.walk(f.value.bytes)) {
                        val v = sub.value
                        if (sub.number == 1 && v is ProtoCodec.FieldValue.VarInt && sendCodeType == null)
                            sendCodeType = v.v.toInt()
                        if (sub.number == 2 && v is ProtoCodec.FieldValue.LenDelim)
                            ussd = String(v.bytes, Charsets.UTF_8).takeIf { it.isNotBlank() }
                    }
                }
            }
        }
        return StartPhoneAuthResponse(tx, sendCodeType, ussd, codeLength, waitTime, buf)
    }

    fun sendCodeTypeName(v: Int?): String = when (v) {
        null -> "unknown"
        0 -> "UNKNOWN"; 1 -> "DEFAULT"; 2 -> "BALEONLY"; 3 -> "SMS"
        4 -> "CALL"; 5 -> "EMAIL"; 6 -> "MISSCALL"; 7 -> "SETUP_EMAIL_REQUIRED"
        8 -> "WHATSAPP"; 9 -> "TELEGRAM"; 10 -> "USSD"
        11 -> "FUTURE_AUTH_TOKEN"; 12 -> "TELEGRAM_GATEWAY"
        else -> "type=$v"
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

    fun encodeStartLiveKitCall(
        peerId: Long,
        peerType: Int = 1,
        rid: Long = randomRid(),
        video: Boolean = false,
        inviteEnable: Boolean = true,
    ): ByteArray {
        val peer = ByteArrayOutputStream()
        ProtoCodec.encVarintField(peer, 1, peerType.toLong())
        ProtoCodec.encVarintField(peer, 2, peerId)

        val inner = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(inner, 1, peer.toByteArray())
        ProtoCodec.encVarintField(inner, 2, rid)
        if (video) ProtoCodec.encVarintField(inner, 3, 1)
        if (inviteEnable) {
            val boolValue = ByteArrayOutputStream()
            ProtoCodec.encVarintField(boolValue, 1, 1)
            ProtoCodec.encLenDelim(inner, 4, boolValue.toByteArray())
        }

        val out = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(out, 6, inner.toByteArray())
        return out.toByteArray()
    }

    data class CallCredentials(
        val url: String,
        val token: String,
        val room: String,
        val peerId: Long?,
        val raw: ByteArray,
    ) {
        override fun equals(other: Any?): Boolean = other is CallCredentials &&
            url == other.url && token == other.token && room == other.room &&
            peerId == other.peerId && raw.contentEquals(other.raw)
        override fun hashCode(): Int =
            31 * (31 * (31 * (31 * url.hashCode() + token.hashCode()) + room.hashCode()) + (peerId?.hashCode() ?: 0)) + raw.contentHashCode()
    }

    fun parseCallCredentials(buf: ByteArray): CallCredentials? {
        val ascii = buf.toString(Charsets.ISO_8859_1)
        val url = Regex("(wss://[A-Za-z0-9./\\-]+\\.(?:ir|ai))").find(ascii)?.groupValues?.get(1)
        val token = Regex("(eyJhbGciOi[A-Za-z0-9_\\-.]{100,})").find(ascii)?.groupValues?.get(1)
        if (url == null || token == null) return null
        return CallCredentials(url, token, extractRoomFromBuf(buf, ascii), parseCallPeerId(buf), buf)
    }

    /**
     * Extract the LiveKit room UUID from a Bale response/push payload.
     *
     * Strategy 1 (structure-aware): scan for tag 0x1A (field 3, wiretype 2)
     * followed by length 0x24 (36 = UUID string length). The 36 bytes that
     * follow are the room name. This anchor is confirmed live-probed and avoids
     * picking the wrong UUID when multiple appear in the same payload (e.g. call
     * IDs in StartCall vs AcceptCall responses differ in field order).
     *
     * Strategy 2 (fallback): first UUID regex match anywhere in the buffer.
     */
    private fun extractRoomFromBuf(buf: ByteArray, ascii: String): String {
        val uuidPat = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        // Strategy 1: field-3 anchor (0x1a 0x24) — structure-aware UUID extraction.
        // 0x1a = field 3 wiretype 2 (len-delim); 0x24 = 36 (exact UUID string length).
        for (i in 0 until buf.size - 38) {
            if (buf[i] == 0x1a.toByte() && buf[i + 1] == 0x24.toByte()) {
                val candidate = ascii.substring(i + 2, i + 38)
                if (uuidPat.matches(candidate)) return candidate
            }
        }
        // Strategy 2: first UUID anywhere
        return uuidPat.find(ascii)?.value ?: ""
    }

    private fun parseCallPeerId(buf: ByteArray): Long? {
        val candidates = ArrayList<Long>()
        fun scan(bytes: ByteArray, depth: Int) {
            if (depth > 3) return
            for (f in ProtoCodec.walk(bytes)) {
                when (val v = f.value) {
                    is ProtoCodec.FieldValue.VarInt -> if (v.v in 10_000L..5_000_000_000L) candidates.add(v.v)
                    is ProtoCodec.FieldValue.LenDelim -> scan(v.bytes, depth + 1)
                }
            }
        }
        scan(buf, 0)
        return candidates.firstOrNull()
    }

    private fun randomRid(): Long {
        val b = ByteArray(8)
        java.security.SecureRandom().nextBytes(b)
        val v = java.nio.ByteBuffer.wrap(b).long and ((1L shl 55) - 1)
        return if (v == 0L) 1L else v
    }

    fun encodeSendMessage(
        peerId: Long,
        text: String,
        peerType: Int = 1,
        accessHash: Long = 0,
        rid: Long = randomRid(),
    ): ByteArray {
        val peer = ByteArrayOutputStream()
        ProtoCodec.encVarintField(peer, 1, peerType.toLong())
        ProtoCodec.encVarintField(peer, 2, peerId)
        if (accessHash != 0L) ProtoCodec.encVarintField(peer, 3, accessHash)

        val textMessage = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(textMessage, 1, text.toByteArray(Charsets.UTF_8))

        val message = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(message, 15, textMessage.toByteArray())

        val out = ByteArrayOutputStream()
        ProtoCodec.encLenDelim(out, 1, peer.toByteArray())
        ProtoCodec.encVarintField(out, 2, rid)
        ProtoCodec.encLenDelim(out, 3, message.toByteArray())
        ProtoCodec.encLenDelim(out, 6, peer.toByteArray())
        return out.toByteArray()
    }

    data class InboundTextMessage(
        val peerUserId: Long,
        val senderUid: Long,
        val rid: Long,
        val text: String,
        val date: Long = 0,
        val peerType: Int = 1,
    )

    /**
     * Recursively scan an update payload for UpdateMessage-shaped sub-trees.
     * Mirrors Python `find_inbound_messages` in protos.py.
     */
    fun findInboundTextMessages(buf: ByteArray, maxDepth: Int = 6): List<InboundTextMessage> {
        val found = ArrayList<InboundTextMessage>()

        fun parseOutPeer(b: ByteArray): Pair<Int, Long> {
            var type = 1
            var id = 0L
            for (f in ProtoCodec.walk(b)) {
                when (val v = f.value) {
                    is ProtoCodec.FieldValue.VarInt -> {
                        if (f.number == 1) type = v.v.toInt()
                        if (f.number == 2) id = v.v
                    }
                    else -> {}
                }
            }
            return type to id
        }

        fun parseTextFromMessage(b: ByteArray): String? {
            for (f in ProtoCodec.walk(b)) {
                if (f.number == 15) {
                    val inner = (f.value as? ProtoCodec.FieldValue.LenDelim)?.bytes ?: continue
                    for (tf in ProtoCodec.walk(inner)) {
                        if (tf.number == 1) {
                            val s = (tf.value as? ProtoCodec.FieldValue.LenDelim)?.bytes ?: continue
                            return s.toString(Charsets.UTF_8)
                        }
                    }
                }
            }
            return null
        }

        fun parseUpdate(b: ByteArray): InboundTextMessage? {
            var peerType = 1
            var peerId = 0L
            var sender = 0L
            var date = 0L
            var rid = 0L
            var text: String? = null
            for (f in ProtoCodec.walk(b)) {
                when (f.value) {
                    is ProtoCodec.FieldValue.VarInt -> {
                        val v = (f.value as ProtoCodec.FieldValue.VarInt).v
                        when (f.number) {
                            2 -> sender = v
                            3 -> date = v
                            4 -> rid = v
                        }
                    }
                    is ProtoCodec.FieldValue.LenDelim -> {
                        val bytes = (f.value as ProtoCodec.FieldValue.LenDelim).bytes
                        when (f.number) {
                            1 -> {
                                val (t, id) = parseOutPeer(bytes)
                                peerType = t
                                peerId = id
                            }
                            5 -> text = parseTextFromMessage(bytes)
                        }
                    }
                    else -> {}
                }
            }
            if (text == null) return null
            return InboundTextMessage(peerId, sender, rid, text, date, peerType)
        }

        fun visit(b: ByteArray, depth: Int) {
            if (depth > maxDepth || b.isEmpty()) return
            parseUpdate(b)?.let { found.add(it) }
            for (f in ProtoCodec.walk(b)) {
                if (f.number >= 1) {
                    val bytes = (f.value as? ProtoCodec.FieldValue.LenDelim)?.bytes
                    if (bytes != null && bytes.isNotEmpty()) visit(bytes, depth + 1)
                }
            }
        }

        visit(buf, 0)
        val seen = HashSet<Triple<Long, Long, String>>()
        val out = ArrayList<InboundTextMessage>()
        for (m in found) {
            val key = Triple(m.rid, m.peerUserId, m.text)
            if (seen.add(key)) out.add(m)
        }
        return out
    }
}
