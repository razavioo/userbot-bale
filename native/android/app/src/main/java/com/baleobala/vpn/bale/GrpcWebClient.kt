package com.baleobala.vpn.bale

import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

/**
 * Minimal gRPC-Web-over-HTTP/1.1 client for Bale's auth RPCs — port of
 * [src/baleobala/bale/grpc_web.py](../../../../../../../../src/baleobala/bale/grpc_web.py).
 *
 * Wire format (per gRPC-Web spec):
 *   request  : 0x00 || u32-be-len || protobuf-body
 *   response : [0x00||u32-len||data-frame] [0x80||u32-len||trailer]
 *      trailer contains `grpc-status: 0\r\ngrpc-message: ...`
 *
 * Bale's gateway requires the exact set of `app_version`, `browser_type`,
 * `mt_*`, etc. headers from the web client. The Python source notes
 * that HTTP/2 fingerprinting causes WAF blocks, so we use HTTP/1.1.
 */
private fun parseHttpUrl(s: String): HttpUrl? = s.toHttpUrlOrNull()

class GrpcWebClient(
    private val host: String = DEFAULT_HOST,
    sessionId: String? = null,
    private val timeoutSec: Long = 30,
    private val extraHeaders: Map<String, String> = emptyMap(),
) {
    private val sessionId: String = sessionId ?: System.currentTimeMillis().toString()
    private var userId: Long? = null
    private val cookieJar = SimpleCookieJar()
    private val http: OkHttpClient = OkHttpClient.Builder()
        // Force HTTP/1.1 — HTTP/2 fingerprint differs from Chrome and gets WAF-blocked.
        .protocols(listOf(okhttp3.Protocol.HTTP_1_1))
        .cookieJar(cookieJar)
        .connectTimeout(timeoutSec, TimeUnit.SECONDS)
        .readTimeout(timeoutSec, TimeUnit.SECONDS)
        .writeTimeout(timeoutSec, TimeUnit.SECONDS)
        .build()

    fun setUserId(id: Long) { userId = id }
    fun setJwtCookie(jwt: String) {
        val url = parseHttpUrl("$host/")!!
        cookieJar.set(url, Cookie.Builder()
            .domain(url.host)
            .name("access_token")
            .value(jwt)
            .path("/")
            .build())
    }

    /** Synchronous unary RPC. Returns the protobuf body of the data frame. */
    fun unary(service: String, method: String, payload: ByteArray, jwt: String? = null): GrpcWebResponse {
        val url = "$host/$service/$method"
        val body = packFrame(payload).toRequestBody("application/grpc-web+proto".toMediaType())
        val rb = Request.Builder().url(url).post(body)
        for ((k, v) in defaultHeaders()) rb.addHeader(k, v)
        if (jwt != null) setJwtCookie(jwt)
        val resp = http.newCall(rb.build()).execute()
        val raw = resp.body?.bytes() ?: ByteArray(0)
        var grpcStatus: String? = resp.header("grpc-status")
        var grpcMessage: String = resp.header("grpc-message") ?: ""
        val setCookies = resp.headers.values("set-cookie").toMutableList()
        // Force-store ALL Set-Cookie values (incl. Max-Age=0 deletes — Bale uses
        // them as flow-continuity markers between StartPhoneAuth and ValidateCode).
        for (sc in setCookies) {
            val head = sc.substringBefore(';').trim()
            val eq = head.indexOf('=')
            if (eq > 0) {
                val name = head.substring(0, eq).trim()
                val value = head.substring(eq + 1).trim()
                val httpUrl = parseHttpUrl(url) ?: continue
                cookieJar.set(httpUrl, Cookie.Builder()
                    .domain(httpUrl.host)
                    .name(name).value(value).path("/").build())
            }
        }
        var dataBody = ByteArray(0)
        var off = 0
        while (off + 5 <= raw.size) {
            val flags = raw[off].toInt() and 0xFF
            val len = ByteBuffer.wrap(raw, off + 1, 4).order(ByteOrder.BIG_ENDIAN).int
            if (off + 5 + len > raw.size || len < 0) break
            val frame = raw.copyOfRange(off + 5, off + 5 + len)
            if (flags and 0x80 != 0) {
                val text = frame.toString(Charsets.ISO_8859_1)
                for (line in text.split("\r\n")) {
                    val l = line.trim()
                    if (l.startsWith("grpc-status:", ignoreCase = true))
                        grpcStatus = l.substringAfter(':').trim()
                    else if (l.startsWith("grpc-message:", ignoreCase = true))
                        grpcMessage = l.substringAfter(':').trim()
                }
            } else {
                dataBody = frame
            }
            off += 5 + len
        }
        val statusInt = grpcStatus?.toIntOrNull()
        if (resp.code >= 400 || (statusInt != null && statusInt != 0)) {
            throw GrpcWebError(statusInt ?: -1, grpcMessage.ifEmpty { "http ${resp.code}" }, resp.code)
        }
        return GrpcWebResponse(dataBody, setCookies, resp.code)
    }

    private fun defaultHeaders(): Map<String, String> {
        val m = LinkedHashMap<String, String>(DEFAULT_HEADERS)
        m["session_id"] = sessionId
        m["mt_session_id"] = sessionId
        userId?.let { m["user_id"] = it.toString() }
        m.putAll(extraHeaders)
        return m
    }

    private fun packFrame(body: ByteArray, trailer: Boolean = false): ByteArray {
        val flags = if (trailer) 0x80 else 0x00
        val out = ByteArray(5 + body.size)
        out[0] = flags.toByte()
        ByteBuffer.wrap(out, 1, 4).order(ByteOrder.BIG_ENDIAN).putInt(body.size)
        System.arraycopy(body, 0, out, 5, body.size)
        return out
    }

    private class SimpleCookieJar : CookieJar {
        private val store = ConcurrentHashMap<String, MutableMap<String, Cookie>>()

        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            val host = url.host
            val byName = store.getOrPut(host) { ConcurrentHashMap() }
            for (c in cookies) byName[c.name] = c
        }

        override fun loadForRequest(url: HttpUrl): List<Cookie> {
            val byName = store[url.host] ?: return emptyList()
            return byName.values.toList()
        }

        fun set(url: HttpUrl, cookie: Cookie) {
            val byName = store.getOrPut(url.host) { ConcurrentHashMap() }
            byName[cookie.name] = cookie
        }
    }

    companion object {
        const val DEFAULT_HOST = "https://next-ws.bale.ai"

        val DEFAULT_HEADERS = mapOf(
            "x-grpc-web" to "1",
            "app_version" to "151668",
            "browser_type" to "1",
            "browser_version" to "147.0.0.0",
            "os_type" to "4",
            "mt_app_version" to "151668",
            "mt_browser_type" to "1",
            "mt_browser_version" to "147.0.0.0",
            "mt_os_type" to "4",
            "origin" to "https://web.bale.ai",
            "referer" to "https://web.bale.ai/",
            "accept" to "*/*",
            "accept-language" to "en-US,en;q=0.9,fa;q=0.8",
            "accept-encoding" to "gzip, deflate, br",
            "sec-ch-ua" to "\"Chromium\";v=\"147\", \"Not/A)Brand\";v=\"24\", \"Google Chrome\";v=\"147\"",
            "sec-ch-ua-mobile" to "?0",
            "sec-ch-ua-platform" to "\"Linux\"",
            "sec-fetch-site" to "same-site",
            "sec-fetch-mode" to "cors",
            "sec-fetch-dest" to "empty",
            "user-agent" to "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        )

        fun extractAccessToken(setCookies: List<String>): String? {
            for (sc in setCookies) {
                val head = sc.substringBefore(';').trim()
                if (head.startsWith("access_token=")) {
                    val v = head.substringAfter('=')
                    if (v.isNotEmpty()) return v
                }
            }
            return null
        }
    }
}

data class GrpcWebResponse(val body: ByteArray, val setCookies: List<String>, val httpStatus: Int) {
    override fun equals(other: Any?): Boolean = other is GrpcWebResponse &&
        body.contentEquals(other.body) && setCookies == other.setCookies && httpStatus == other.httpStatus
    override fun hashCode(): Int =
        31 * (31 * body.contentHashCode() + setCookies.hashCode()) + httpStatus
}

class GrpcWebError(val grpcStatus: Int, val grpcMessage: String, val httpStatus: Int) :
    RuntimeException("grpc-status=$grpcStatus grpc-message=\"$grpcMessage\" http=$httpStatus")
