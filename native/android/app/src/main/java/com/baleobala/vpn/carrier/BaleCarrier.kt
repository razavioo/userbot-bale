package com.baleobala.vpn.carrier

import android.util.Log
import com.baleobala.vpn.tunnel.Transport
import com.baleobala.vpn.tunnel.Tunnel
import com.baleobala.vpn.tunnel.VpnFraming
import kotlin.concurrent.thread

/**
 * Bale carrier — moves IP packets over a [Tunnel] backed by a Bale
 * Transport (LiveKit DataChannel in production, see Linux flow at
 * [src/baleobala/vpn/transports/datachannel_transport.py](../../../../../../../../src/baleobala/vpn/transports/datachannel_transport.py)).
 *
 * The carrier stays agnostic to which [Transport] it receives so tests can
 * still pair it with [com.baleobala.vpn.tunnel.LoopbackTransport], while
 * production injects LiveKitDataChannelTransport after Bale StartCall.
 */
class BaleCarrier(
    private val transportFactory: () -> Transport,
    private val sessId: Int,
    private val onLog: (String) -> Unit,
    /** Invoked once when the underlying tunnel emits `tunnel_dead`
     *  (N consecutive max-retry drops with no intervening ACK). The
     *  owner is expected to tear down + reconnect the carrier. */
    private val onCarrierDead: (String) -> Unit = {},
) : Carrier {
    override var onPacketReceived: ((ByteArray) -> Unit)? = null

    private var transport: Transport? = null
    private var tunnel: Tunnel? = null
    private val deadFired = java.util.concurrent.atomic.AtomicBoolean(false)

    override fun start() {
        val tx = transportFactory()
        transport = tx
        val mtu = tx.mtu
        val t = Tunnel(tx, sessId = sessId)
        t.addEventHandler { event, payload ->
            onLog("[$event] $payload")
            if (event == "tunnel_dead" && deadFired.compareAndSet(false, true)) {
                try { onCarrierDead("tunnel_dead $payload") }
                catch (e: Throwable) { Log.e(TAG, "onCarrierDead threw", e) }
            }
        }
        t.start { ip ->
            try { onPacketReceived?.invoke(ip) }
            catch (e: Throwable) { Log.e(TAG, "onPacketReceived threw", e) }
        }
        tunnel = t
        onLog("bale carrier up; sess=$sessId mtu=$mtu")
        maybeRunTunnelSaturation(t, mtu)
    }

    /**
     * TUN-level saturation: when `setprop debug.baleobala.tunsat 1`, push
     * synthetic 1400-byte packets through Tunnel.sendPacket() at max rate
     * for 15s and log the achieved throughput. This exercises the full
     * ARQ/window/ACK path (unlike satlab which bypasses the tunnel) — so
     * it actually measures whether window-size changes move the needle.
     * Packets carry a non-IP first byte so the relay's TUN kernel drops
     * them on arrival but still ACKs them, keeping the round-trip cycle.
     */
    private fun maybeRunTunnelSaturation(t: Tunnel, mtu: Int) {
        val enabled = try {
            val cls = Class.forName("android.os.SystemProperties")
            val get = cls.getMethod("get", String::class.java, String::class.java)
            (get.invoke(null, "debug.baleobala.tunsat", "") as? String) == "1"
        } catch (_: Throwable) { false }
        if (!enabled) return
        thread(name = "vpn-tunsat", isDaemon = true) {
            try { Thread.sleep(3_000) } catch (_: InterruptedException) { return@thread }
            // 1400 byte payload, first byte 0x00 so the remote kernel
            // drops it as malformed IP (version field = 0).
            val pkt = ByteArray(1400) { if (it == 0) 0x00 else 0xC3.toByte() }
            val durationMs = try {
                val cls = Class.forName("android.os.SystemProperties")
                val get = cls.getMethod("get", String::class.java, String::class.java)
                ((get.invoke(null, "debug.baleobala.tunsat_secs", "") as? String) ?: "")
                    .toLongOrNull()?.times(1000L) ?: 15_000L
            } catch (_: Throwable) { 15_000L }
            onLog("TUNSAT: starting tunnel saturation pkt=${pkt.size}B duration=${durationMs}ms")
            val tStart = System.currentTimeMillis()
            var sent = 0L
            var bytes = 0L
            var errors = 0L
            while (System.currentTimeMillis() - tStart < durationMs) {
                try {
                    t.sendPacket(pkt)
                    sent++; bytes += pkt.size
                } catch (e: Throwable) {
                    errors++
                    if (errors % 25L == 1L) onLog("TUNSAT: sendPacket err #$errors: ${e.message}")
                }
            }
            val elapsed = (System.currentTimeMillis() - tStart) / 1000.0
            val kbs = bytes / 1024.0 / elapsed
            val pps = sent / elapsed
            onLog(
                "TUNSAT: done elapsed=${"%.1f".format(elapsed)}s sent=$sent pkts " +
                "(${"%.0f".format(pps)} pkt/s) bytes=$bytes (${"%.1f".format(kbs)} KB/s) " +
                "errors=$errors pending=${t.pendingCount()}"
            )
        }
    }

    override fun submitPacket(packet: ByteArray, length: Int) {
        val payload = if (length == packet.size) packet else packet.copyOf(length)
        val t = tunnel ?: return
        try {
            t.sendPacket(payload)
        } catch (e: Throwable) {
            Log.w(TAG, "sendPacket failed: ${e.message}")
        }
    }

    override fun stop() {
        try { tunnel?.stop() } catch (_: Throwable) {}
        try { transport?.close() } catch (_: Throwable) {}
        tunnel = null
        transport = null
        onLog("bale carrier stopped")
    }

    companion object { private const val TAG = "BaleCarrier" }
}
