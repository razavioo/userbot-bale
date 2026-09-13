package com.userbot_bale.vpn.carrier

import java.net.DatagramSocket

/**
 * Pluggable VPN carrier — abstracts how IP packets leaving the TUN
 * actually reach the internet.
 *
 * Implementations:
 *  - LocalNatCarrier: protect()ed sockets on the device's underlying
 *    network. Useful where the phone has direct internet (e.g. dev/test).
 *  - BaleCarrier: routes packets over a Bale call (LiveKit DataChannel)
 *    to a remote exit-node account. The actual carrier path used in
 *    censored networks where the phone cannot reach the open internet
 *    directly. Equivalent to the Linux `tunnel up` flow.
 */
interface Carrier {
    /** Bring the carrier up; throws on permanent failure. */
    fun start()

    /** Submit one IPv4 packet (raw, as read from TUN) for delivery. */
    fun submitPacket(packet: ByteArray, length: Int)

    /** Tear down. */
    fun stop()

    /** The callback the service installs so the carrier can hand
     *  inbound IP packets back for writing into the TUN. */
    var onPacketReceived: ((ByteArray) -> Unit)?

    /** A mechanism to socket-protect outgoing sockets if this carrier
     *  needs them. Set by the VpnService at construction time. */
    interface Protector {
        fun protect(socket: DatagramSocket): Boolean
        fun protect(socket: java.net.Socket): Boolean
        fun bindToUnderlying(socket: DatagramSocket): Boolean
        fun bindToUnderlying(socket: java.net.Socket): Boolean
    }
}
