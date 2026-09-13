package com.userbot_bale.vpn.tunnel

import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * In-memory pair of transports — bytes written to A appear on B's recvBytes
 * and vice-versa. Used by the unit tests for the Tunnel and by the v0
 * "loopback" carrier when no real Bale carrier is available.
 */
class LoopbackTransport private constructor(
    override val mtu: Int,
    private val outbound: LinkedBlockingQueue<ByteArray>,
    private val inbound: LinkedBlockingQueue<ByteArray>,
) : Transport {

    override fun sendBytes(data: ByteArray) {
        outbound.put(data)
    }

    override fun recvBytes(timeoutMs: Long): ByteArray? =
        inbound.poll(timeoutMs, TimeUnit.MILLISECONDS)

    override fun close() {
        // queues stay drainable; no socket to close
    }

    companion object {
        fun pair(mtu: Int = 4096): Pair<Transport, Transport> {
            val a = LinkedBlockingQueue<ByteArray>()
            val b = LinkedBlockingQueue<ByteArray>()
            return Pair(
                LoopbackTransport(mtu, outbound = a, inbound = b),
                LoopbackTransport(mtu, outbound = b, inbound = a),
            )
        }
    }
}
