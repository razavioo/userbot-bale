package com.userbot_bale.vpn

import android.util.Log
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue

/**
 * Maintains one DatagramSocket per (clientPort, dstIp, dstPort) flow.
 * Sockets are protect()ed via [protector] so traffic doesn't loop back into the VPN.
 * Replies are reassembled into IPv4+UDP packets and pushed onto [outQueue] for the
 * VpnService writer thread to forward back into the TUN.
 */
class UdpForwarder(
    private val protector: (DatagramSocket) -> Boolean,
    private val outQueue: LinkedBlockingQueue<ByteArray>,
    private val onLog: (String) -> Unit,
) {
    private data class FlowKey(val clientPort: Int, val dstIp: String, val dstPort: Int)

    private class Flow(
        val socket: DatagramSocket,
        val clientAddr: InetAddress,
        val clientPort: Int,
        val dstAddr: InetAddress,
        val dstPort: Int,
        @Volatile var sawReply: Boolean = false,
    )

    private val flows = ConcurrentHashMap<FlowKey, Flow>()
    @Volatile private var running = true

    fun forward(
        clientAddr: InetAddress,
        clientPort: Int,
        dstAddr: InetAddress,
        dstPort: Int,
        payload: ByteArray,
        offset: Int,
        length: Int,
    ) {
        if (!running) return
        val key = FlowKey(clientPort, dstAddr.hostAddress ?: "", dstPort)
        val flow = try {
            flows[key] ?: createAndRegisterFlow(clientAddr, clientPort, dstAddr, dstPort, key) ?: return
        } catch (e: IOException) {
            Log.w(TAG, "createFlow failed for $key: ${e.message}")
            return
        }
        try {
            val pkt = DatagramPacket(payload, offset, length, InetSocketAddress(dstAddr, dstPort))
            flow.socket.send(pkt)
        } catch (e: IOException) {
            Log.w(TAG, "send failed for $key: ${e.message}")
            closeFlow(key)
        }
    }

    @Synchronized
    private fun createAndRegisterFlow(
        clientAddr: InetAddress,
        clientPort: Int,
        dstAddr: InetAddress,
        dstPort: Int,
        key: FlowKey,
    ): Flow? {
        flows[key]?.let { return it }
        val sock = DatagramSocket()
        try {
            if (!protector(sock)) {
                Log.w(TAG, "protect() failed; closing socket")
                throw IOException("protect failed")
            }
            sock.soTimeout = 8000
            val flow = Flow(sock, clientAddr, clientPort, dstAddr, dstPort)
            flows[key] = flow
            Thread({ readerLoop(flow, key) }, "udpfwd-$key").apply { isDaemon = true }.start()
            onLog("udp open $clientPort → ${dstAddr.hostAddress}:$dstPort")
            return flow
        } catch (t: Throwable) {
            try { sock.close() } catch (_: Throwable) {}
            if (t is IOException) throw t
            throw IOException(t)
        }
    }

    private fun readerLoop(
        flow: Flow,
        key: FlowKey,
    ) {
        val sock = flow.socket
        val buf = ByteArray(64 * 1024)
        Log.i(TAG, "reader loop start ${flow.dstAddr.hostAddress}:${flow.dstPort} local=${sock.localPort}")
        try {
            while (running && !sock.isClosed) {
                val pkt = DatagramPacket(buf, buf.size)
                try {
                    sock.receive(pkt)
                } catch (st: java.net.SocketTimeoutException) {
                    if (!flow.sawReply) {
                        Log.w(TAG, "no reply within 8s for ${flow.dstAddr.hostAddress}:${flow.dstPort} (local=${sock.localPort})")
                    }
                    break
                }
                flow.sawReply = true
                val payload = ByteArray(pkt.length)
                System.arraycopy(buf, 0, payload, 0, pkt.length)
                val ipPkt = Ipv4Packet.buildUdp(
                    srcAddr = flow.dstAddr,
                    srcPort = flow.dstPort,
                    dstAddr = flow.clientAddr,
                    dstPort = flow.clientPort,
                    payload = payload,
                    payloadLen = pkt.length,
                )
                outQueue.offer(ipPkt)
                Log.d(TAG, "udp reply ${flow.dstAddr.hostAddress}:${flow.dstPort} → :${flow.clientPort} len=${pkt.length}")
            }
        } catch (e: IOException) {
            Log.w(TAG, "reader io: ${e.message}")
        } catch (t: Throwable) {
            Log.e(TAG, "reader fatal", t)
        } finally {
            flows.remove(key)
            try { sock.close() } catch (_: Throwable) {}
        }
    }

    private fun closeFlow(key: FlowKey) {
        val flow = flows.remove(key) ?: return
        try { flow.socket.close() } catch (_: Throwable) {}
    }

    fun shutdown() {
        running = false
        for (flow in flows.values) {
            try { flow.socket.close() } catch (_: Throwable) {}
        }
        flows.clear()
    }

    companion object { private const val TAG = "UdpForwarder" }
}
