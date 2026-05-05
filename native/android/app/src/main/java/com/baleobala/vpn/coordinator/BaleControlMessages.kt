package com.baleobala.vpn.coordinator

/**
 * Coordinator control-plane message codec — Kotlin port of
 * [src/baleobala/coordinator/protocol.py](../../../../../../../../../src/baleobala/coordinator/protocol.py).
 *
 * Wire format: `BBCOORD1:` magic prefix followed by compact JSON.
 * All coordinator messages travel over LiveKit data channel topic "control".
 */
object BaleControlMessages {

    const val CONTROL_TOPIC = "control"
    private val MAGIC = "BBCOORD1:".toByteArray(Charsets.UTF_8)

    object Kind {
        const val HELLO        = "HELLO"
        const val ASSIGN       = "ASSIGN"
        const val DENY         = "DENY"
        const val EXPECT_ACK   = "EXPECT_ACK"
        const val ONLINE       = "ONLINE"
        const val HEARTBEAT    = "HEARTBEAT"
        const val RELEASED     = "RELEASED"
        const val OFFLINE      = "OFFLINE"
    }

    object DenyReason {
        const val NO_CAPACITY    = "no_capacity"
        const val BLOCKED        = "blocked"
        const val CLIENT_UNKNOWN = "client_unknown"
        const val INTERNAL       = "internal"
    }

    /** Decoded control message. body holds string/Long values keyed by field name. */
    data class ControlMessage(val kind: String, val body: Map<String, Any>) {
        fun str(key: String, default: String = ""): String = body[key]?.toString() ?: default
        fun long(key: String, default: Long = 0L): Long =
            when (val v = body[key]) {
                is Long -> v
                is Int -> v.toLong()
                is Double -> v.toLong()
                is String -> v.toLongOrNull() ?: default
                else -> default
            }
    }

    fun decode(bytes: ByteArray): ControlMessage? {
        if (bytes.size <= MAGIC.size) return null
        val prefix = bytes.copyOfRange(0, MAGIC.size)
        if (!prefix.contentEquals(MAGIC)) return null
        return try {
            val text = bytes.copyOfRange(MAGIC.size, bytes.size).toString(Charsets.UTF_8)
            val fields = parseJsonFlat(text)
            val kind = fields["kind"]?.toString()?.takeIf { it.isNotEmpty() } ?: return null
            ControlMessage(kind, fields - "kind" - "v")
        } catch (_: Exception) {
            null
        }
    }

    /** Minimal flat JSON parser — handles string, long/int values and string arrays.
     *  Does not support nested objects; coordinator messages are always flat. */
    private fun parseJsonFlat(text: String): Map<String, Any> {
        val result = mutableMapOf<String, Any>()
        val s = text.trim().removePrefix("{").removeSuffix("}")
        var i = 0
        while (i < s.length) {
            // skip whitespace and commas
            while (i < s.length && (s[i] == ',' || s[i].isWhitespace())) i++
            if (i >= s.length) break
            if (s[i] != '"') { i++; continue }
            // read key
            i++
            val keyStart = i
            while (i < s.length && s[i] != '"') { if (s[i] == '\\') i++; i++ }
            val key = s.substring(keyStart, i)
            i++ // closing "
            while (i < s.length && (s[i] == ':' || s[i].isWhitespace())) i++
            if (i >= s.length) break
            // read value
            when {
                s[i] == '"' -> {
                    i++
                    val sb = StringBuilder()
                    while (i < s.length && s[i] != '"') {
                        if (s[i] == '\\' && i + 1 < s.length) { i++; sb.append(s[i]) } else sb.append(s[i])
                        i++
                    }
                    i++
                    result[key] = sb.toString()
                }
                s[i] == '[' -> {
                    val end = s.indexOf(']', i)
                    val inner = if (end > i) s.substring(i + 1, end) else ""
                    val list = inner.split(',').mapNotNull { el ->
                        val t = el.trim().removeSurrounding("\"")
                        t.toLongOrNull() ?: t.takeIf { it.isNotEmpty() }
                    }
                    result[key] = list
                    i = if (end >= 0) end + 1 else s.length
                }
                else -> {
                    val numStart = i
                    while (i < s.length && s[i] != ',' && s[i] != '}') i++
                    val numStr = s.substring(numStart, i).trim()
                    result[key] = numStr.toLongOrNull() ?: numStr.toDoubleOrNull() ?: numStr
                }
            }
        }
        return result
    }

    fun encode(kind: String, body: Map<String, Any> = emptyMap()): ByteArray {
        val jsonBytes = buildJsonBytes(mapOf("v" to 1, "kind" to kind) + body)
        return MAGIC + jsonBytes
    }

    private fun buildJsonBytes(pairs: Map<String, Any>): ByteArray {
        val sb = StringBuilder("{")
        pairs.entries.forEachIndexed { i, (k, v) ->
            if (i > 0) sb.append(',')
            sb.append('"').append(k.jsonEscape()).append("\":")
            when (v) {
                is String -> sb.append('"').append(v.jsonEscape()).append('"')
                is Long, is Int -> sb.append(v)
                is List<*> -> sb.append('[').append(
                    v.joinToString(",") { e ->
                        when (e) {
                            is Long, is Int -> e.toString()
                            is String -> "\"${e.jsonEscape()}\""
                            else -> "\"${e.toString().jsonEscape()}\""
                        }
                    }
                ).append(']')
                else -> sb.append('"').append(v.toString().jsonEscape()).append('"')
            }
        }
        sb.append('}')
        return sb.toString().toByteArray(Charsets.UTF_8)
    }

    private fun String.jsonEscape(): String =
        replace("\\", "\\\\").replace("\"", "\\\"")
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

    fun makeHello(clientId: String, appVersion: String = ""): ByteArray =
        encode(Kind.HELLO, mapOf("client_id" to clientId, "app_version" to appVersion))

    fun parseAssign(msg: ControlMessage): Pair<Long, String>? {
        if (msg.kind != Kind.ASSIGN) return null
        val relayPeerId = msg.long("relay_peer_id")
        val sessionId   = msg.str("session_id")
        if (relayPeerId <= 0L || sessionId.isEmpty()) return null
        return relayPeerId to sessionId
    }

    fun parseDenyReason(msg: ControlMessage): String =
        msg.str("reason", DenyReason.NO_CAPACITY)
}

// ByteArray concatenation helper (avoids reflection; keeps this file self-contained)
private operator fun ByteArray.plus(other: ByteArray): ByteArray {
    val result = ByteArray(size + other.size)
    copyInto(result, 0)
    other.copyInto(result, size)
    return result
}
