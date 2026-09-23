package com.userbot_bale.vpn.tunnel

/**
 * Byte-pipe transport interface — Kotlin equivalent of
 * [src/userbot-bale/vpn/transports/__init__.py](../../../../../../../../src/userbot-bale/vpn/transports/__init__.py).
 *
 * Implementations:
 *  - [LoopbackTransport]: pairs two halves for unit tests / local carrier.
 *  - [LiveKitDataChannelTransport]: preferred path over a LiveKit DataChannel.
 *  - [RpcTransport]: VPN frames as base64 Bale chat messages (store-and-forward fallback).
 *  - AudioTransport: Python-only (`audio_transport.py`); Android needs a GGWave NDK binding first.
 *  - VideoQrTransport: Python-only (`video_qr_transport.py`); not yet ported to Android.
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
