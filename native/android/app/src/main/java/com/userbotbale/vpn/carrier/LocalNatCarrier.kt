package com.userbot_bale.vpn.carrier

import android.util.Log
import com.userbot_bale.vpn.Ipv4Packet
import com.userbot_bale.vpn.TcpForwarder
import com.userbot_bale.vpn.UdpForwarder
import java.net.InetAddress
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicLong

/**
 * v0 carrier: userspace UDP NAT directly via [protect()]+bindSocket on
 * the device's underlying network. Re-uses the [UdpForwarder] that has
 * been verified end-to-end earlier in the project. TCP/ICMP currently
 * dropped — see BaleCarrier for the path that actually crosses
 * censorship boundaries.
 */
class LocalNatCarrier(
    private val protector: Carrier.Protector,
    private val onLog: (String) -> Unit,
) : Carrier {
    override var onPacketReceived: ((ByteArray) -> Unit)? = null

    private val outQueue = LinkedBlockingQueue<ByteArray>(1024)
    @Volatile private var running = false
    private var pumpThread: Thread? = null
    private var forwarder: UdpForwarder? = null
    private var tcpForwarder: TcpForwarder? = null
    private val clientAddr: InetAddress = InetAddress.getByName("10.77.0.2")

    val pktsIn = AtomicLong(0)
    val pktsTcp = AtomicLong(0)
    val pktsIcmp = AtomicLong(0)
    val pktsUdp = AtomicLong(0)

    override fun start() {
        if (running) return
        running = true
        forwarder = UdpForwarder(
            protector = { sock ->
                val ok = protector.protect(sock)
                if (ok) protector.bindToUnderlying(sock)
                ok
            },
            outQueue = outQueue,
            onLog = onLog,
        )
        tcpForwarder = TcpForwarder(
            protectAndBind = { sock ->
                val ok = protector.protect(sock)
                if (ok) protector.bindToUnderlying(sock) else false
            },
            outQueue = outQueue,
            onLog = onLog,
        )
        pumpThread = Thread({ pumpLoop() }, "carrier-rx").apply { isDaemon = true; start() }
    }

    override fun submitPacket(packet: ByteArray, length: Int) {
        pktsIn.incrementAndGet()
        val parsed = Ipv4Packet.parse(packet, length) ?: return
        when (parsed.protocol) {
            Ipv4Packet.PROTO_UDP -> {
                pktsUdp.incrementAndGet()
                val udp = Ipv4Packet.parseUdp(packet, parsed.payloadOffset, parsed.payloadLength) ?: return
                val srcPort = udp[0]; val dstPort = udp[1]
                val dataOff = udp[2]; val dataLen = udp[3]
                val dst = InetAddress.getByAddress(parsed.dstAddr)
                try {
                    forwarder?.forward(clientAddr, srcPort, dst, dstPort, packet, dataOff, dataLen)
                } catch (t: Throwable) {
                    Log.w(TAG, "udp forward error: ${t.message}")
                }
            }
            Ipv4Packet.PROTO_TCP -> {
                pktsTcp.incrementAndGet()
                tcpForwarder?.submit(packet, length)
            }
            Ipv4Packet.PROTO_ICMP -> {
                if (pktsIcmp.incrementAndGet() % 20 == 1L)
                    onLog("icmp dropped → ${addrStr(parsed.dstAddr)}")
            }
        }
    }

    override fun stop() {
        running = false
        try { forwarder?.shutdown() } catch (_: Throwable) {}
        try { tcpForwarder?.shutdown() } catch (_: Throwable) {}
        try { pumpThread?.interrupt() } catch (_: Throwable) {}
        forwarder = null
        tcpForwarder = null
        pumpThread = null
        onLog("local-nat carrier stopped: in=${pktsIn.get()} udp=${pktsUdp.get()} tcp=${pktsTcp.get()} icmp_drop=${pktsIcmp.get()}")
    }

    private fun pumpLoop() {
        while (running) {
            try {
                val pkt = outQueue.take()
                onPacketReceived?.invoke(pkt)
            } catch (_: InterruptedException) {
                break
            } catch (t: Throwable) {
                if (running) Log.w(TAG, "pump: ${t.message}")
            }
        }
    }

    private fun addrStr(b: ByteArray): String =
        "${b[0].toInt() and 0xff}.${b[1].toInt() and 0xff}.${b[2].toInt() and 0xff}.${b[3].toInt() and 0xff}"

    companion object { private const val TAG = "LocalNatCarrier" }
}
