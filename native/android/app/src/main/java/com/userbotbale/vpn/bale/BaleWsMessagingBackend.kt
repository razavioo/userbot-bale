package com.userbot_bale.vpn.bale

import com.userbot_bale.vpn.tunnel.MessagingBackend
import java.util.concurrent.ConcurrentHashMap

/**
 * [MessagingBackend] over the live Bale WebSocket — used by
 * [com.userbot_bale.vpn.tunnel.RpcTransport] for chat-carried VPN frames.
 *
 * Send path uses `bale.messaging.v2.Messaging/SendMessage` (live-verified
 * wire shape). Inbound path scans pushed update payloads with
 * [BaleProtos.findInboundTextMessages] and fans out to per-peer listeners.
 *
 * Access-hash: 0 until a LoadDialogs cache is wired; the server accepts
 * SendMessage without a non-zero hash for private peers (same as Python
 * when the dialog cache is cold).
 */
class BaleWsMessagingBackend(
    private val ws: BaleWsClient,
    private val startWs: Boolean = false,
) : MessagingBackend {
    private val listeners = ConcurrentHashMap<Long, (ByteArray) -> Unit>()
    private val seenRids = LinkedHashSet<Long>()
    private val seenLock = Any()

    @Volatile
    private var started = false

    override fun start(timeoutMs: Long) {
        if (started) return
        if (startWs) ws.start(timeoutMs)
        started = true
    }

    override fun stop() {
        if (!started) return
        listeners.clear()
        started = false
        if (startWs) ws.close()
    }

    override fun sendMessage(peerId: Long, body: ByteArray, peerType: Int) {
        val text = try {
            body.toString(Charsets.UTF_8)
        } catch (_: Exception) {
            throw IllegalArgumentException("send_message body must be valid UTF-8")
        }
        val payload = BaleProtos.encodeSendMessage(peerId, text, peerType = peerType)
        ws.rpc(BaleProtos.MESSAGING_SERVICE, "SendMessage", payload, timeoutMs = 15_000)
    }

    override fun listenMessages(peerId: Long, callback: (ByteArray) -> Unit) {
        listeners[peerId] = callback
    }

    /**
     * Feed a raw update payload (from BaleWsClient.onUpdate) into the
     * inbound message dispatcher. Call this from the update hook.
     */
    fun dispatchUpdate(payload: ByteArray) {
        for (msg in BaleProtos.findInboundTextMessages(payload)) {
            val isNew = synchronized(seenLock) {
                if (msg.rid != 0L && seenRids.contains(msg.rid)) {
                    false
                } else {
                    if (msg.rid != 0L) {
                        seenRids.add(msg.rid)
                        while (seenRids.size > 1024) {
                            val it = seenRids.iterator()
                            if (it.hasNext()) {
                                it.next()
                                it.remove()
                            } else break
                        }
                    }
                    true
                }
            }
            if (!isNew) continue
            val cb = listeners[msg.senderUid] ?: listeners[msg.peerUserId] ?: continue
            cb(msg.text.toByteArray(Charsets.UTF_8))
        }
    }
}
