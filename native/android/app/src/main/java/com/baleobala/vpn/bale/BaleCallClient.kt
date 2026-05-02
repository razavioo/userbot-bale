package com.baleobala.vpn.bale

class BaleCallClient(
    private val jwt: String,
    private val onLog: (String) -> Unit = {},
    private val onWsCreated: (BaleWsClient) -> Unit = {},
) {
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
