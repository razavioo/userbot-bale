package com.baleobala.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.baleobala.vpn.bale.AuthStore
import com.baleobala.vpn.carrier.BaleCarrier
import com.baleobala.vpn.carrier.Carrier
import com.baleobala.vpn.carrier.LocalNatCarrier
import com.baleobala.vpn.tunnel.LoopbackTransport
import com.baleobala.vpn.tunnel.Transport
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.DatagramSocket
import java.util.concurrent.atomic.AtomicLong

/**
 * Top-level VpnService. Brings up the TUN, hands packets to the configured
 * [Carrier], and writes carrier replies back to the TUN.
 *
 * Carrier choice:
 *   - If `intent` extra `carrier=bale` and a JWT is stored, attempts to
 *     wire a [BaleCarrier]. Currently the Bale [Transport] is a
 *     loopback placeholder (so the wiring is exercised end-to-end in
 *     tests/dev) — the real LiveKitDataChannelTransport lives in the
 *     follow-up commits that bring the LiveKit Android SDK on board.
 *   - Otherwise [LocalNatCarrier] (verified in v0).
 */
class BaleVpnService : VpnService() {

    @Volatile private var running = false
    private var tunFd: ParcelFileDescriptor? = null
    private var readerThread: Thread? = null
    private var writerThread: Thread? = null
    private var carrier: Carrier? = null
    private val outQueue = java.util.concurrent.LinkedBlockingQueue<ByteArray>(1024)

    private val pktsOut = AtomicLong(0)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_DISCONNECT) {
            stopTunnel()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIF_ID, buildNotification("Connecting…"))
        val carrierKind = intent?.getStringExtra(EXTRA_CARRIER) ?: detectCarrier()
        startTunnel(carrierKind)
        return START_STICKY
    }

    private fun detectCarrier(): String {
        // If we have a JWT, prefer Bale (when its transport is wired).
        // Until LiveKit is integrated this falls through to local-nat.
        val have = AuthStore(this).jwt() != null
        return if (have) CARRIER_BALE else CARRIER_LOCAL
    }

    private fun startTunnel(carrierKind: String) {
        if (running) return
        val builder = Builder()
            .setSession("Baleobala VPN")
            .setMtu(1400)
            .addAddress("10.77.0.2", 24)
            .addRoute("0.0.0.0", 0)
            .addDnsServer("1.1.1.1")
            .addDnsServer("9.9.9.9")
        try { builder.addDisallowedApplication(packageName) } catch (_: Throwable) {}

        val fd = builder.establish()
        if (fd == null) {
            broadcast("error", "VpnService.establish() returned null")
            updateNotification("Failed")
            stopSelf()
            return
        }
        tunFd = fd
        running = true

        val protector = object : Carrier.Protector {
            override fun protect(socket: DatagramSocket): Boolean = this@BaleVpnService.protect(socket)
            override fun bindToUnderlying(socket: DatagramSocket): Boolean = bindToUnderlyingNetwork(socket)
        }

        carrier = when (carrierKind) {
            CARRIER_BALE -> {
                broadcast("log", "carrier=bale (data path uses Tunnel/ARQ; live LiveKit transport pending)")
                BaleCarrier(
                    transportFactory = ::makeBalePlaceholderTransport,
                    sessId = (System.currentTimeMillis() and 0xFFFF).toInt(),
                    onLog = { broadcast("log", it) },
                )
            }
            else -> {
                broadcast("log", "carrier=local-nat")
                LocalNatCarrier(protector = protector, onLog = { broadcast("log", it) })
            }
        }
        carrier?.onPacketReceived = { ip -> outQueue.offer(ip) }

        try { carrier?.start() } catch (e: Throwable) {
            broadcast("error", "carrier start failed: ${e.message}")
            stopTunnel()
            stopSelf()
            return
        }

        readerThread = Thread({ readerLoop(fd) }, "tun-reader").apply { isDaemon = true; start() }
        writerThread = Thread({ writerLoop(fd) }, "tun-writer").apply { isDaemon = true; start() }

        Thread({ selfTestProtectedDns() }, "self-test").apply { isDaemon = true; start() }

        updateNotification("Connected")
        broadcast("status", "connected")
        broadcast("log", "tun up: 10.77.0.2/24 mtu 1400")
    }

    /**
     * Placeholder Bale Transport — pairs a loopback so [Tunnel] can be
     * wired end-to-end without crashing. Production must replace with
     * `LiveKitDataChannelTransport(...)` once that class lands.
     * Packets sent through this loopback come right back, which is
     * intentional: it makes the data-plane wiring testable on-device.
     */
    private fun makeBalePlaceholderTransport(): Transport {
        val (a, _b) = LoopbackTransport.pair(mtu = 4096)
        // A returns to itself for now; replace with real LiveKit DC.
        return a
    }

    private fun readerLoop(fd: ParcelFileDescriptor) {
        val input = FileInputStream(fd.fileDescriptor)
        val buf = ByteArray(32767)
        while (running) {
            try {
                val n = input.read(buf)
                if (n <= 0) { Thread.sleep(5); continue }
                carrier?.submitPacket(buf, n)
            } catch (t: Throwable) {
                if (running) Log.w(TAG, "reader: ${t.message}")
                break
            }
        }
    }

    private fun writerLoop(fd: ParcelFileDescriptor) {
        val output = FileOutputStream(fd.fileDescriptor)
        while (running) {
            try {
                val pkt = outQueue.take()
                output.write(pkt)
                output.flush()
                pktsOut.incrementAndGet()
            } catch (_: InterruptedException) { break }
            catch (t: Throwable) {
                if (running) Log.w(TAG, "writer: ${t.message}")
                break
            }
        }
    }

    private fun stopTunnel() {
        running = false
        try { carrier?.stop() } catch (_: Throwable) {}
        try { readerThread?.interrupt() } catch (_: Throwable) {}
        try { writerThread?.interrupt() } catch (_: Throwable) {}
        try { tunFd?.close() } catch (_: Throwable) {}
        tunFd = null
        carrier = null
        broadcast("status", "disconnected")
        broadcast("log", "tun down. out=${pktsOut.get()}")
    }

    override fun onDestroy() {
        stopTunnel()
        super.onDestroy()
    }

    private fun bindToUnderlyingNetwork(sock: DatagramSocket): Boolean {
        return try {
            val cm = getSystemService(ConnectivityManager::class.java)
            val net = cm.allNetworks.firstOrNull { n ->
                val caps = cm.getNetworkCapabilities(n) ?: return@firstOrNull false
                !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                    caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            } ?: return false
            net.bindSocket(sock); true
        } catch (t: Throwable) {
            Log.w(TAG, "bindSocket failed: ${t.message}"); false
        }
    }

    private fun selfTestProtectedDns() {
        val query = byteArrayOf(
            0x12, 0x34, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            7, 'e'.code.toByte(), 'x'.code.toByte(), 'a'.code.toByte(), 'm'.code.toByte(),
            'p'.code.toByte(), 'l'.code.toByte(), 'e'.code.toByte(),
            3, 'c'.code.toByte(), 'o'.code.toByte(), 'm'.code.toByte(),
            0, 0, 1, 0, 1,
        )
        val sock = DatagramSocket()
        try {
            val protected_ = protect(sock)
            bindToUnderlyingNetwork(sock)
            broadcast("log", "selftest: protect=$protected_ localPort=${sock.localPort}")
            sock.soTimeout = 4000
            for (server in listOf("192.168.100.1", "1.1.1.1", "8.8.8.8")) {
                try {
                    val target = java.net.InetAddress.getByName(server)
                    sock.send(java.net.DatagramPacket(query, query.size, target, 53))
                    val replyBuf = ByteArray(2048)
                    val reply = java.net.DatagramPacket(replyBuf, replyBuf.size)
                    sock.receive(reply)
                    broadcast("log", "selftest: got DNS reply ${reply.length}B from ${reply.address.hostAddress}")
                    return
                } catch (_: java.net.SocketTimeoutException) {
                    broadcast("log", "selftest: timeout from $server")
                }
            }
            broadcast("log", "selftest: ALL DNS targets timed out (offline?)")
        } catch (t: Throwable) {
            broadcast("log", "selftest: FAILED ${t.javaClass.simpleName}: ${t.message}")
        } finally {
            try { sock.close() } catch (_: Throwable) {}
        }
    }

    private fun broadcast(kind: String, value: String) {
        Log.i(TAG, "[$kind] $value")
        val i = Intent(ACTION_STATE).putExtra("kind", kind).putExtra("value", value)
        LocalBroadcastManager.getInstance(this).sendBroadcast(i)
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, "Baleobala VPN", NotificationManager.IMPORTANCE_LOW))
            }
        }
    }

    private fun buildNotification(text: String): Notification {
        ensureChannel()
        val openIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("Baleobala VPN")
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(openIntent)
            .build()
    }

    private fun updateNotification(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(text))
    }

    companion object {
        private const val TAG = "BaleVpnService"
        private const val CHANNEL_ID = "baleobala_vpn"
        private const val NOTIF_ID = 1
        const val ACTION_DISCONNECT = "com.baleobala.vpn.DISCONNECT"
        const val ACTION_STATE = "com.baleobala.vpn.STATE"
        const val EXTRA_CARRIER = "carrier"
        const val CARRIER_LOCAL = "local-nat"
        const val CARRIER_BALE = "bale"
    }
}
