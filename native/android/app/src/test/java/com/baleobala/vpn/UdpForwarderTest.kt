package com.baleobala.vpn

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class UdpForwarderTest {

    private lateinit var echo: DatagramSocket
    private lateinit var echoThread: Thread
    @Volatile private var echoRunning = true

    @Before fun startEcho() {
        echo = DatagramSocket(0, InetAddress.getByName("127.0.0.1"))
        echo.soTimeout = 500
        echoRunning = true
        echoThread = Thread {
            val buf = ByteArray(2048)
            while (echoRunning) {
                try {
                    val pkt = DatagramPacket(buf, buf.size)
                    echo.receive(pkt)
                    val reply = DatagramPacket(buf, pkt.length, pkt.address, pkt.port)
                    echo.send(reply)
                } catch (_: java.net.SocketTimeoutException) { /* loop */ }
                catch (_: Throwable) { break }
            }
        }.apply { isDaemon = true; start() }
    }

    @After fun stopEcho() {
        echoRunning = false
        try { echo.close() } catch (_: Throwable) {}
        echoThread.join(1000)
    }

    @Test fun forwardsAndReceivesReply() {
        val out = LinkedBlockingQueue<ByteArray>()
        val fwd = UdpForwarder(
            protector = { true },
            outQueue = out,
            onLog = {},
        )
        try {
            val client = InetAddress.getByName("10.77.0.2")
            val dst = InetAddress.getByName("127.0.0.1")
            val payload = "hello".toByteArray()
            fwd.forward(client, 41000, dst, echo.localPort, payload, 0, payload.size)

            val ipPkt = out.poll(2, TimeUnit.SECONDS)
            assertNotNull("expected reply IP packet on outQueue", ipPkt)
            val parsed = Ipv4Packet.parse(ipPkt!!, ipPkt.size)
            assertNotNull(parsed)
            assertEquals(Ipv4Packet.PROTO_UDP, parsed!!.protocol)
            // Reply payload begins after UDP header (8 bytes) at payloadOffset.
            val udpPayload = ipPkt.copyOfRange(parsed.payloadOffset + 8, parsed.payloadOffset + parsed.payloadLength)
            assertArrayEqualsString(payload, udpPayload)
        } finally {
            fwd.shutdown()
        }
    }

    @Test fun protectorFailureDoesNotPoisonMap() {
        val out = LinkedBlockingQueue<ByteArray>()
        val fwd = UdpForwarder(
            protector = { false },
            outQueue = out,
            onLog = {},
        )
        try {
            val client = InetAddress.getByName("10.77.0.2")
            val dst = InetAddress.getByName("127.0.0.1")
            val payload = byteArrayOf(1, 2, 3)
            // Should not throw or block
            fwd.forward(client, 41001, dst, echo.localPort, payload, 0, payload.size)
            // No reply expected.
            assertTrue(out.poll(300, TimeUnit.MILLISECONDS) == null)
        } finally {
            fwd.shutdown()
        }
    }

    private fun assertArrayEqualsString(expected: ByteArray, actual: ByteArray) {
        assertEquals(String(expected), String(actual))
    }
}
