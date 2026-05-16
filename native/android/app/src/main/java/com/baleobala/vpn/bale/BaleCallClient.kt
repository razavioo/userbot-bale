package com.baleobala.vpn.bale

import android.content.Context
import com.baleobala.vpn.coordinator.BaleControlMessages
import com.baleobala.vpn.tunnel.LiveKitDataChannelTransport

class BaleCallClient(
    private val jwt: String,
    private val onLog: (String) -> Unit = {},
    private val onWsCreated: (BaleWsClient) -> Unit = {},
) {
    /**
     * Coordinator-mode connection: call the coordinator, receive a relay
     * assignment, then call the relay directly.
     *
     * Flow:
     *   1. `startCall(coordinatorPeerId)` → LiveKit room with coordinator.
     *   2. Open data channel topic="control", send HELLO, recv ASSIGN/DENY.
     *   3. Close coordinator room.
     *   4. `startCall(relayPeerId)` → the actual VPN LiveKit credentials.
     *
     * The relay accepts because the coordinator pre-registered our peer_id
     * in its ExpectedClientSet via EXPECT_CLIENT.
     */
    fun requestRelayAssignment(
        coordinatorPeerId: Long,
        clientId: String,
        appContext: Context,
        clientPeerId: Long? = null,
        timeoutMs: Long = 60_000,
    ): BaleProtos.CallCredentials {
        onLog("coordinator: dialing coordinator peer=$coordinatorPeerId")
        val coordCreds = startCall(coordinatorPeerId, timeoutMs)
        onLog("coordinator: joined room=${coordCreds.room}; exchanging control messages")

        val transport = LiveKitDataChannelTransport(
            appContext = appContext,
            url = coordCreds.url,
            token = coordCreds.token,
            topic = BaleControlMessages.CONTROL_TOPIC,
            reliable = true,
            onLog = { onLog("coordinator-lk: $it") },
        ).connect(timeoutMs)

        val relayPeerId: Long
        try {
            transport.sendBytes(BaleControlMessages.makeHello(
                clientId = clientId,
                clientPeerId = clientPeerId,
            ))
            onLog("coordinator: HELLO sent; waiting for ASSIGN/DENY")
            // Coordinator's quick_exchange to the relay (StartCall + LiveKit
            // join + EXPECT_CLIENT exchange + EXPECT_ACK + room teardown +
            // post-stop Rust-runtime flush) commonly takes 7-12 s end-to-end.
            // With multi-client coordinator-side dispatch serialization, a
            // second client may wait behind another in-flight dispatch, so
            // bump the cap to 45 s to absorb that queueing without giving up.
            val payload = transport.recvBytes(45_000)
                ?: throw RuntimeException("coordinator did not respond to HELLO")
            val msg = BaleControlMessages.decode(payload)
                ?: throw RuntimeException("coordinator sent non-control payload")
            when (msg.kind) {
                BaleControlMessages.Kind.DENY -> {
                    val reason = msg.str("reason", BaleControlMessages.DenyReason.NO_CAPACITY)
                    throw RuntimeException("coordinator denied connection: $reason")
                }
                BaleControlMessages.Kind.ASSIGN -> {
                    val (peerId, sessionId) = BaleControlMessages.parseAssign(msg)
                        ?: throw RuntimeException("coordinator ASSIGN missing relay_peer_id or session_id")
                    relayPeerId = peerId
                    onLog("coordinator: ASSIGN relay=$relayPeerId session=$sessionId")
                }
                else -> throw RuntimeException("coordinator sent unexpected kind=${msg.kind}")
            }
        } finally {
            try { transport.close() } catch (_: Throwable) {}
        }

        onLog("coordinator: calling relay peer=$relayPeerId directly")
        return startCall(relayPeerId, timeoutMs)
    }

    fun startCall(peerId: Long, timeoutMs: Long = 120_000): BaleProtos.CallCredentials {
        var pushed: BaleProtos.CallCredentials? = null
        val lock = Object()
        val ws = BaleWsClient(
            jwt = jwt,
            onLog = onLog,
            onUpdate = { resp ->
                val creds = BaleProtos.parseCallCredentials(resp.raw)
                if (creds != null) {
                    onLog("push: parsed CallCredentials room=${creds.room} url=${creds.url.take(40)}…")
                    synchronized(lock) {
                        pushed = creds
                        lock.notifyAll()
                    }
                } else {
                    onLog(
                        "push: not creds; seq=${resp.seq} payload=${resp.payload.size}B " +
                        "raw=${resp.raw.size}B hex=${resp.raw.take(96).joinToString("") { "%02x".format(it) }}"
                    )
                }
            },
        )
        onWsCreated(ws)
        ws.start()
        try {
            val req = BaleProtos.encodeStartLiveKitCall(peerId = peerId)
            onLog("sending Bale StartCall peer=$peerId")
            val ack = ws.rpc(BaleProtos.MEET_SERVICE, "StartCall", req, timeoutMs = 10_000)
            val status = ack.payload.toString(Charsets.UTF_8)
            if (status == "CallNotApproved") {
                throw RuntimeException("Bale rejected StartCall: CallNotApproved")
            }
            if (status.isNotBlank() && !status.startsWith("eyJ")) {
                onLog("StartCall status=$status")
            }
            BaleProtos.parseCallCredentials(ack.raw)?.let {
                onLog("StartCall returned LiveKit creds room=${it.room}")
                return it
            }
            val deadline = System.currentTimeMillis() + timeoutMs
            synchronized(lock) {
                while (pushed == null) {
                    if (Thread.currentThread().isInterrupted) {
                        throw InterruptedException("StartCall wait interrupted")
                    }
                    val remaining = deadline - System.currentTimeMillis()
                    if (remaining <= 0) break
                    lock.wait(minOf(remaining, 250))
                }
            }
            return pushed ?: throw RuntimeException(
                "StartCall ACKed but no LiveKit credentials arrived (peer offline or busy?)"
            )
        } finally {
            ws.close()
        }
    }
}
