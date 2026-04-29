package com.baleobala.vpn.carrier

import android.util.Log
import com.baleobala.vpn.tunnel.Transport
import com.baleobala.vpn.tunnel.Tunnel
import com.baleobala.vpn.tunnel.VpnFraming

/**
 * Bale carrier — moves IP packets over a [Tunnel] backed by a Bale
 * Transport (LiveKit DataChannel in production, see Linux flow at
 * [src/baleobala/vpn/transports/datachannel_transport.py](../../../../../../../../src/baleobala/vpn/transports/datachannel_transport.py)).
 *
 * For this Android cut the carrier is wired up but the Bale Transport
 * concrete class (LiveKitDataChannelTransport) requires the LiveKit
 * Android SDK to be on the classpath plus the Bale ws_client / call-
 * setup RPCs. Those parts are coming in subsequent commits; the wiring
 * here is intentionally agnostic to which Transport you hand it so that
 * tests can pair it with [com.baleobala.vpn.tunnel.LoopbackTransport].
 */
class BaleCarrier(
    private val transportFactory: () -> Transport,
    private val sessId: Int,
    private val onLog: (String) -> Unit,
) : Carrier {
    override var onPacketReceived: ((ByteArray) -> Unit)? = null

    private var transport: Transport? = null
    private var tunnel: Tunnel? = null

    override fun start() {
        val tx = transportFactory()
        transport = tx
        val mtu = tx.mtu
        val t = Tunnel(tx, sessId = sessId)
        t.addEventHandler { event, payload -> onLog("[$event] $payload") }
        t.start { ip ->
            try { onPacketReceived?.invoke(ip) }
            catch (e: Throwable) { Log.e(TAG, "onPacketReceived threw", e) }
        }
        tunnel = t
        onLog("bale carrier up; sess=$sessId mtu=$mtu")
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
