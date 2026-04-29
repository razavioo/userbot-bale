package com.baleobala.vpn

import android.util.Log
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue

/**
 * Userspace TCP NAT for the LocalNat carrier. For each new SYN read
 * from the TUN we open a real [java.net.Socket] to the destination
 * (after [protectAndBind]), drive the TCP state machine on the TUN
 * side enough to keep the app's connection alive, and shovel bytes
 * between the two.
 *
 * Bookkeeping per flow (5-tuple keyed by (clientPort, dstIp, dstPort)):
 *  - clientNextSeq: the next seq we ACK on the client side
 *  - ourSeq: the next seq we'll emit toward the client
 *  - socket and reader/writer threads
 *
 * Limitations (acceptable for v0; can be tightened later):
 *  - No TCP options / window-scale (advertise window=65535 fixed).
 *  - No retransmission from our side; we rely on the remote socket's
 *    own TCP stack and the client retrying when packets are lost.
 *  - No URG, no SACK.
 */
class TcpForwarder(
    private val protectAndBind: (Socket) -> Boolean,
    private val outQueue: LinkedBlockingQueue<ByteArray>,
    private val onLog: (String) -> Unit,
) {
    private data class FlowKey(val clientPort: Int, val dstIp: String, val dstPort: Int)

    private class Flow(
        val key: FlowKey,
        val clientAddr: InetAddress,
        val clientPort: Int,
        val dstAddr: InetAddress,
        val dstPort: Int,
        val socket: Socket,
        @Volatile var ourSeq: Long,
        @Volatile var clientNextSeq: Long,
        @Volatile var halfClosed: Boolean = false,
    )

    private val flows = ConcurrentHashMap<FlowKey, Flow>()
    @Volatile private var running = true

    fun submit(packet: ByteArray, length: Int) {
        if (!running) return
        val tcp = Ipv4Tcp.parse(packet, length) ?: return
        val key = FlowKey(tcp.srcPort, addrStr(tcp.dstAddr), tcp.dstPort)
        val flag = tcp.flags
        val isSyn = flag and Ipv4Tcp.Flag.SYN != 0
        val isFin = flag and Ipv4Tcp.Flag.FIN != 0
        val isRst = flag and Ipv4Tcp.Flag.RST != 0

        val existing = flows[key]
        if (isSyn && existing == null) {
            openFlow(packet, tcp, key)
            return
        }
        if (existing == null) {
            // Stray ACK / FIN with no flow — RST it.
            sendRst(tcp)
            return
        }
        // Update client seq tracking.
        existing.clientNextSeq = (tcp.seq + tcp.payloadLength + (if (isFin) 1L else 0L)) and 0xFFFFFFFFL

        if (isRst) { closeFlow(key); return }
        // Forward payload to the socket.
        if (tcp.payloadLength > 0) {
            try {
                existing.socket.getOutputStream().write(packet, tcp.payloadOffset, tcp.payloadLength)
                existing.socket.getOutputStream().flush()
                // ACK the bytes we accepted.
                emitAck(existing)
            } catch (e: IOException) {
                Log.w(TAG, "tcp write failed: ${e.message}")
                closeFlow(key)
            }
        }
        if (isFin) {
            // Half-close on the socket side.
            try { existing.socket.shutdownOutput() } catch (_: Throwable) {}
            existing.halfClosed = true
            // Send FIN-ACK so app considers the close acknowledged.
            emitAck(existing, extraFlags = Ipv4Tcp.Flag.FIN or Ipv4Tcp.Flag.ACK)
            existing.ourSeq = (existing.ourSeq + 1L) and 0xFFFFFFFFL
        }
    }

    private fun openFlow(packet: ByteArray, syn: Ipv4Tcp.Parsed, key: FlowKey) {
        val clientAddr = InetAddress.getByAddress(syn.srcAddr)
        val dstAddr = InetAddress.getByAddress(syn.dstAddr)
        val sock = Socket()
        try {
            if (!protectAndBind(sock)) {
                Log.w(TAG, "protect failed for $key")
                sock.close()
                sendRst(syn); return
            }
            sock.connect(InetSocketAddress(dstAddr, syn.dstPort), 5000)
        } catch (e: Throwable) {
            Log.w(TAG, "tcp connect failed ${syn.dstAddr.contentToString()}:${syn.dstPort}: ${e.message}")
            try { sock.close() } catch (_: Throwable) {}
            sendRst(syn)
            return
        }
        val ourIsn = (System.nanoTime() and 0xFFFFFFFFL)
        val flow = Flow(
            key = key,
            clientAddr = clientAddr,
            clientPort = syn.srcPort,
            dstAddr = dstAddr,
            dstPort = syn.dstPort,
            socket = sock,
            ourSeq = ourIsn,
            clientNextSeq = (syn.seq + 1L) and 0xFFFFFFFFL,
        )
        flows[key] = flow
        onLog("tcp open ${syn.srcPort} → ${addrStr(syn.dstAddr)}:${syn.dstPort}")

        // Send SYN-ACK to client.
        val synAck = Ipv4Tcp.build(
            srcAddr = dstAddr, srcPort = syn.dstPort,
            dstAddr = clientAddr, dstPort = syn.srcPort,
            seq = ourIsn, ack = (syn.seq + 1L) and 0xFFFFFFFFL,
            flags = Ipv4Tcp.Flag.SYN or Ipv4Tcp.Flag.ACK,
        )
        outQueue.offer(synAck)
        flow.ourSeq = (ourIsn + 1L) and 0xFFFFFFFFL

        // Spawn reader thread that pumps socket bytes back to TUN as data segments.
        Thread({ readerLoop(flow) }, "tcp-r-$key").apply { isDaemon = true; start() }
    }

    private fun readerLoop(flow: Flow) {
        val buf = ByteArray(16 * 1024)
        try {
            val ins = flow.socket.getInputStream()
            while (running && !flow.socket.isClosed) {
                val n = ins.read(buf)
                if (n <= 0) break
                val seg = Ipv4Tcp.build(
                    srcAddr = flow.dstAddr, srcPort = flow.dstPort,
                    dstAddr = flow.clientAddr, dstPort = flow.clientPort,
                    seq = flow.ourSeq, ack = flow.clientNextSeq,
                    flags = Ipv4Tcp.Flag.ACK or Ipv4Tcp.Flag.PSH,
                    payload = buf, payloadOffset = 0, payloadLength = n,
                )
                outQueue.offer(seg)
                flow.ourSeq = (flow.ourSeq + n.toLong()) and 0xFFFFFFFFL
            }
            // Remote closed. Send FIN-ACK to client.
            val fin = Ipv4Tcp.build(
                srcAddr = flow.dstAddr, srcPort = flow.dstPort,
                dstAddr = flow.clientAddr, dstPort = flow.clientPort,
                seq = flow.ourSeq, ack = flow.clientNextSeq,
                flags = Ipv4Tcp.Flag.FIN or Ipv4Tcp.Flag.ACK,
            )
            outQueue.offer(fin)
            flow.ourSeq = (flow.ourSeq + 1L) and 0xFFFFFFFFL
        } catch (e: IOException) {
            // socket dropped; tell client RST
            sendRstFromFlow(flow)
        } finally {
            closeFlow(flow.key)
        }
    }

    private fun emitAck(flow: Flow, extraFlags: Int = Ipv4Tcp.Flag.ACK) {
        val ack = Ipv4Tcp.build(
            srcAddr = flow.dstAddr, srcPort = flow.dstPort,
            dstAddr = flow.clientAddr, dstPort = flow.clientPort,
            seq = flow.ourSeq, ack = flow.clientNextSeq,
            flags = extraFlags,
        )
        outQueue.offer(ack)
    }

    private fun sendRst(tcp: Ipv4Tcp.Parsed) {
        val rst = Ipv4Tcp.build(
            srcAddr = InetAddress.getByAddress(tcp.dstAddr), srcPort = tcp.dstPort,
            dstAddr = InetAddress.getByAddress(tcp.srcAddr), dstPort = tcp.srcPort,
            seq = if (tcp.flags and Ipv4Tcp.Flag.ACK != 0) tcp.ack else 0,
            ack = (tcp.seq + 1L) and 0xFFFFFFFFL,
            flags = Ipv4Tcp.Flag.RST or Ipv4Tcp.Flag.ACK,
        )
        outQueue.offer(rst)
    }

    private fun sendRstFromFlow(flow: Flow) {
        val rst = Ipv4Tcp.build(
            srcAddr = flow.dstAddr, srcPort = flow.dstPort,
            dstAddr = flow.clientAddr, dstPort = flow.clientPort,
            seq = flow.ourSeq, ack = flow.clientNextSeq,
            flags = Ipv4Tcp.Flag.RST,
        )
        outQueue.offer(rst)
    }

    private fun closeFlow(key: FlowKey) {
        val f = flows.remove(key) ?: return
        try { f.socket.close() } catch (_: Throwable) {}
    }

    fun shutdown() {
        running = false
        for (f in flows.values) {
            try { f.socket.close() } catch (_: Throwable) {}
        }
        flows.clear()
    }

    private fun addrStr(b: ByteArray): String =
        "${b[0].toInt() and 0xff}.${b[1].toInt() and 0xff}.${b[2].toInt() and 0xff}.${b[3].toInt() and 0xff}"

    companion object { private const val TAG = "TcpForwarder" }
}
