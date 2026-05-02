package com.baleobala.vpn

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class TcpForwarderTest {

    private lateinit var server: ServerSocket
    private lateinit var serverThread: Thread
    @Volatile private var serverRunning = true

    @Before fun startEchoServer() {
        server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        serverRunning = true
        serverThread = Thread {
            while (serverRunning) {
                try {
                    val sock = server.accept()
                    Thread {
                        try {
                            val ins = sock.getInputStream()
                            val out = sock.getOutputStream()
                            val buf = ByteArray(4096)
                            while (true) {
                                val n = ins.read(buf)
                                if (n <= 0) break
                                out.write(buf, 0, n); out.flush()
                            }
                        } catch (_: Throwable) {} finally { try { sock.close() } catch (_: Throwable) {} }
                    }.apply { isDaemon = true }.start()
                } catch (_: Throwable) { break }
            }
        }.apply { isDaemon = true; start() }
    }

    @After fun stopServer() {
        serverRunning = false
        try { server.close() } catch (_: Throwable) {}
        serverThread.join(1000)
    }

    @Test fun handshakeDataAndCloseProducesExpectedSegments() {
        val outQueue = LinkedBlockingQueue<ByteArray>()
        val fwd = TcpForwarder(
            protectAndBind = { true },
            outQueue = outQueue,
            onLog = {},
        )
        try {
            val client = InetAddress.getByName("10.77.0.2")
            val dst = InetAddress.getByName("127.0.0.1")
            val clientPort = 51234
            val dstPort = server.localPort

            // SYN from client.
            val syn = Ipv4Tcp.build(
                srcAddr = client, srcPort = clientPort,
                dstAddr = dst, dstPort = dstPort,
                seq = 1000L, ack = 0L, flags = Ipv4Tcp.Flag.SYN,
            )
            fwd.submit(syn, syn.size)

            // Expect SYN-ACK on outQueue.
            val synAckPkt = outQueue.poll(2, TimeUnit.SECONDS)
            assertNotNull("expected SYN-ACK", synAckPkt)
            val synAck = Ipv4Tcp.parse(synAckPkt!!, synAckPkt.size)!!
            assertTrue("expected SYN flag set", synAck.flags and Ipv4Tcp.Flag.SYN != 0)
            assertTrue("expected ACK flag set", synAck.flags and Ipv4Tcp.Flag.ACK != 0)
            assertEquals(1001L, synAck.ack)
            assertEquals(dstPort, synAck.srcPort)
            assertEquals(clientPort, synAck.dstPort)

            // Send data segment client → server: "ping".
            val payload = "ping".toByteArray()
            val data = Ipv4Tcp.build(
                srcAddr = client, srcPort = clientPort,
                dstAddr = dst, dstPort = dstPort,
                seq = 1001L, ack = (synAck.seq + 1L) and 0xFFFFFFFFL,
                flags = Ipv4Tcp.Flag.ACK or Ipv4Tcp.Flag.PSH,
                payload = payload,
            )
            fwd.submit(data, data.size)

            // Collect outbound packets until we see a data segment carrying "ping" back.
            val deadline = System.currentTimeMillis() + 3000
            var sawEcho = false
            while (System.currentTimeMillis() < deadline) {
                val pkt = outQueue.poll(500, TimeUnit.MILLISECONDS) ?: continue
                val p = Ipv4Tcp.parse(pkt, pkt.size) ?: continue
                if (p.payloadLength > 0) {
                    val body = pkt.copyOfRange(p.payloadOffset, p.payloadOffset + p.payloadLength)
                    if (body.contentEquals(payload)) { sawEcho = true; break }
                }
            }
            assertTrue("expected echoed data segment from server", sawEcho)
        } finally {
            fwd.shutdown()
        }
    }

    @Test fun strayAckGetsRst() {
        val outQueue = LinkedBlockingQueue<ByteArray>()
        val fwd = TcpForwarder(
            protectAndBind = { true },
            outQueue = outQueue,
            onLog = {},
        )
        try {
            val client = InetAddress.getByName("10.77.0.2")
            val dst = InetAddress.getByName("127.0.0.1")
            // ACK with no preceding SYN — should yield a RST.
            val ack = Ipv4Tcp.build(
                srcAddr = client, srcPort = 33333,
                dstAddr = dst, dstPort = 9, // discard port
                seq = 0L, ack = 0L, flags = Ipv4Tcp.Flag.ACK,
            )
            fwd.submit(ack, ack.size)
            val rstPkt = outQueue.poll(1, TimeUnit.SECONDS)
            assertNotNull("expected RST for stray ACK", rstPkt)
            val rst = Ipv4Tcp.parse(rstPkt!!, rstPkt.size)!!
            assertTrue("expected RST flag", rst.flags and Ipv4Tcp.Flag.RST != 0)
        } finally {
            fwd.shutdown()
        }
    }
}
