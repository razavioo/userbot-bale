package com.baleobala.vpn

import com.baleobala.vpn.tunnel.LoopbackTransport
import com.baleobala.vpn.tunnel.Tunnel
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class TunnelTest {

    @Test fun smallPacketRoundTripsBetweenTwoTunnels() {
        val (a, b) = LoopbackTransport.pair(mtu = 4096)
        val tA = Tunnel(a, sessId = 7)
        val tB = Tunnel(b, sessId = 7)
        val rxA = LinkedBlockingQueue<ByteArray>()
        val rxB = LinkedBlockingQueue<ByteArray>()
        tA.start { rxA.offer(it) }
        tB.start { rxB.offer(it) }
        try {
            val msg = byteArrayOf(1, 2, 3, 4, 5)
            tA.sendPacket(msg)
            val got = rxB.poll(2, TimeUnit.SECONDS)
            assertTrue("no packet on B side", got != null)
            assertArrayEquals(msg, got)
        } finally {
            tA.stop()
            tB.stop()
        }
    }

    @Test fun fragmentedPacketReassembles() {
        val (a, b) = LoopbackTransport.pair(mtu = 64)
        val tA = Tunnel(a, sessId = 1, mtuOverride = 64)
        val tB = Tunnel(b, sessId = 1, mtuOverride = 64)
        val rxB = LinkedBlockingQueue<ByteArray>()
        tA.start { /* no inbound expected */ }
        tB.start { rxB.offer(it) }
        try {
            val msg = ByteArray(500) { (it and 0xFF).toByte() }
            tA.sendPacket(msg)
            val got = rxB.poll(3, TimeUnit.SECONDS)
            assertTrue(got != null)
            assertArrayEquals(msg, got)
        } finally {
            tA.stop()
            tB.stop()
        }
    }

    @Test fun mismatchedSessIdIsDropped() {
        val (a, b) = LoopbackTransport.pair()
        val tA = Tunnel(a, sessId = 1)
        val tB = Tunnel(b, sessId = 99) // different session
        val rxB = LinkedBlockingQueue<ByteArray>()
        tA.start { /* no-op */ }
        tB.start { rxB.offer(it) }
        try {
            tA.sendPacket(byteArrayOf(7, 8, 9))
            val got = rxB.poll(500, TimeUnit.MILLISECONDS)
            assertTrue("packet leaked across sessions: $got", got == null)
        } finally {
            tA.stop()
            tB.stop()
        }
    }
}
