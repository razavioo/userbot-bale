package com.userbot_bale.vpn.bale

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class BaleWsClient(
    private val jwt: String,
    private val onLog: (String) -> Unit = {},
    private val onUpdate: (RpcEnvelope.Response) -> Unit = {},
    private val url: String = DEFAULT_URL,
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .build()
    private val seq = AtomicInteger(1)
    private val pending = ConcurrentHashMap<Int, LinkedBlockingQueue<RpcEnvelope.Response>>()
    private val connected = java.util.concurrent.CountDownLatch(1)
    @Volatile private var connectError: Throwable? = null
    @Volatile private var ws: WebSocket? = null

    fun start(timeoutMs: Long = 15_000) {
        val req = Request.Builder()
            .url(url)
            .header("Origin", "https://web.bale.ai")
            .header("User-Agent", GrpcWebClient.DEFAULT_HEADERS["user-agent"] ?: "Mozilla/5.0")
            .header("Cookie", "access_token=$jwt")
            .build()
        http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                ws = webSocket
                webSocket.send(byteArrayOf(0x1a, 0x04, 0x08, 0x01, 0x10, 0x01).toByteString())
                connected.countDown()
            }

            override fun onMessage(webSocket: WebSocket, bytes: okio.ByteString) {
                val arr = bytes.toByteArray()
                onLog("bale ws rx ${arr.size}B hex=${arr.take(64).joinToString("") { "%02x".format(it) }}")
                dispatch(arr)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                connectError = t
                connected.countDown()
                onLog("bale ws failure: ${t.message}")
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                onLog("bale ws closed: $code $reason")
            }
        })
        if (!connected.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            throw RuntimeException("Bale WS did not connect within ${timeoutMs}ms")
        }
        connectError?.let { throw RuntimeException("Bale WS failed to connect: ${it.message}", it) }
        // Subscribe to update stream — without this Bale never pushes
        // call credentials / message updates to this WS session (they go
        // to the phone session instead). Mirrors the Python client's
        // _subscribe_updates / web.bale.ai post-handshake GetDiff.
        try {
            sendOneway(
                "bale.ghasedak.v1.GhasedakService",
                "GetDiff",
                GET_DIFF_PAYLOAD,
            )
            onLog("bale ws subscribed to updates via GetDiff")
        } catch (t: Throwable) {
            onLog("bale ws GetDiff send failed: ${t.message}")
        }
    }

    /**
     * Send an RPC frame without waiting for the response. Used for
     * subscription RPCs (GetDiff) where we only care about the push
     * stream that follows.
     */
    fun sendOneway(service: String, method: String, payload: ByteArray) {
        val webSocket = ws ?: throw IllegalStateException("Bale WS not connected")
        val id = seq.getAndIncrement()
        val frame = RpcEnvelope.encodeRequest(service, method, payload, id)
        if (!webSocket.send(frame.toByteString())) {
            throw RuntimeException("Bale WS send returned false")
        }
        onLog("bale rpc oneway $service/$method seq=$id frame=${frame.size}B")
    }

    fun rpc(service: String, method: String, payload: ByteArray, timeoutMs: Long = 10_000): RpcEnvelope.Response {
        val webSocket = ws ?: throw IllegalStateException("Bale WS not connected")
        val id = seq.getAndIncrement()
        val q = LinkedBlockingQueue<RpcEnvelope.Response>(1)
        pending[id] = q
        try {
            onLog("bale rpc tx $service/$method seq=$id payload=${payload.size}B")
            val frame = RpcEnvelope.encodeRequest(service, method, payload, id)
            if (!webSocket.send(frame.toByteString())) throw RuntimeException("Bale WS send returned false")
            onLog("bale rpc sent $service/$method seq=$id frame=${frame.size}B")
            val deadline = System.currentTimeMillis() + timeoutMs
            while (true) {
                val remaining = deadline - System.currentTimeMillis()
                if (remaining <= 0) throw RuntimeException("Bale RPC $service/$method timed out")
                if (Thread.currentThread().isInterrupted) throw InterruptedException("interrupted waiting for Bale RPC")
                val resp = q.poll(minOf(remaining, 250), TimeUnit.MILLISECONDS)
                if (resp != null) {
                    onLog("bale rpc rx $service/$method seq=$id payload=${resp.payload.size}B raw=${resp.raw.size}B")
                    return resp
                }
            }
        } finally {
            pending.remove(id)
        }
    }

    fun close() {
        try { ws?.close(1000, "closing") } catch (_: Throwable) {}
        ws = null
        http.dispatcher.executorService.shutdown()
    }

    private fun dispatch(buf: ByteArray) {
        val resp = RpcEnvelope.decodeResponse(buf)
        val id = resp.seq
        if (id != null) {
            val q = pending[id]
            if (q != null && q.offer(resp)) return
        }
        onUpdate(resp)
    }

    companion object {
        const val DEFAULT_URL = "wss://next-ws.bale.ai/ws/"

        // Minimal GetDiff body (`optimizations` packed-repeated [8, 10, 12])
        // captured from the live web client. Empty bodies are dropped by
        // some Bale accounts, so this matches the web fingerprint.
        // Source: src/userbot-bale/bale/api.py::_GET_DIFF_PAYLOAD
        private val GET_DIFF_PAYLOAD = byteArrayOf(0x12, 0x03, 0x08, 0x0a, 0x0c)
    }
}
