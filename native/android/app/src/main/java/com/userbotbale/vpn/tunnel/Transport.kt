package com.userbot_bale.vpn.tunnel

/**
 * Byte-pipe transport interface — Kotlin equivalent of
 * [src/userbot-bale/vpn/transports/__init__.py](../../../../../../../../src/userbot-bale/vpn/transports/__init__.py).
 *
 * Implementations:
 *  - [LoopbackTransport]: pairs two halves for unit tests.
 *  - DataChannelTransport: TODO; rides on a LiveKit DataChannel.
 *  - AudioTransport: TODO; rides on a Bale voice call (very low rate).
 */
interface Transport {
    /** Maximum payload size per send_bytes call (frame cap, including VPN header). */
    val mtu: Int

    /** Optional indicative throughput hint for QoS; ignored if unknown. */
    val rateHint: Int get() = 0

    fun sendBytes(data: ByteArray)

    /** Returns null on timeout. */
    fun recvBytes(timeoutMs: Long): ByteArray?

    fun close()
}
