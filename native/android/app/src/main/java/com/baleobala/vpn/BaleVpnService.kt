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
import com.baleobala.vpn.bale.BaleCallClient
import com.baleobala.vpn.carrier.BaleCarrier
import com.baleobala.vpn.carrier.Carrier
import com.baleobala.vpn.carrier.LocalNatCarrier
import com.baleobala.vpn.tunnel.LiveKitDataChannelTransport
import com.baleobala.vpn.tunnel.Transport
import org.json.JSONObject
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.DatagramSocket
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * Top-level VpnService. Brings up the TUN, hands packets to the configured
 * [Carrier], and writes carrier replies back to the TUN.
 *
 * Carrier choice:
 *   - If a JWT is stored, [BaleCarrier] dials the configured Bale exit peer,
 *     joins the returned LiveKit room, and moves tunnel frames over topic `vpn`.
 *   - Otherwise [LocalNatCarrier] (verified in v0).
 */
class BaleVpnService : VpnService() {

    @Volatile private var running = false
    @Volatile private var starting = false
    @Volatile private var carrierKind = CARRIER_AUTO
    private var tunFd: ParcelFileDescriptor? = null
    private var startupThread: Thread? = null
    private var readerThread: Thread? = null
    private var writerThread: Thread? = null
    private var statsThread: Thread? = null
    private var carrier: Carrier? = null
    private val outQueue = java.util.concurrent.LinkedBlockingQueue<ByteArray>(1024)

    private val pktsOut = AtomicLong(0)
    private val pktsIn = AtomicLong(0)
    private val bytesOut = AtomicLong(0)
    private val bytesIn = AtomicLong(0)
    private val startedAt = AtomicLong(0)
    private val lastError = AtomicReference("")

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_DISCONNECT) {
            stopTunnel()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIF_ID, buildNotification("Connecting…"))
        carrierKind = intent?.getStringExtra(EXTRA_CARRIER) ?: AppSettings(this).carrierMode
        if (carrierKind == CARRIER_AUTO) carrierKind = detectCarrier()
        startTunnelAsync(carrierKind)
        return START_STICKY
    }

    private fun detectCarrier(): String {
        return if (AuthStore(this).jwt() != null) CARRIER_BALE else CARRIER_LOCAL
    }

    private fun startTunnelAsync(carrierKind: String) {
        if (running || starting) return
        starting = true
        broadcast("status", "connecting")
        startupThread = Thread({
            try {
                startTunnel(carrierKind)
            } catch (t: Throwable) {
                broadcast("error", "startup failed: ${t.message}")
                updateNotification("Failed")
                stopTunnel()
                stopSelf()
            } finally {
                starting = false
            }
        }, "vpn-startup").apply { isDaemon = true; start() }
    }

    private fun startTunnel(carrierKind: String) {
        if (running) return
        val builder = Builder()
            .setSession("Baleobala VPN")
            .setMtu(1400)
            .addAddress("10.77.0.2", 24)
            .addRoute("0.0.0.0", 0)
            .addDnsServer("185.51.200.2")    // 403.online — verified working
            .addDnsServer("192.168.100.1")   // local router fallback
            .addDnsServer("178.22.122.100")  // Shecan (may be blocked)
            .addDnsServer("185.143.232.120") // ArvanCloud (may be blocked)
        try { builder.addDisallowedApplication(packageName) } catch (_: Throwable) {}

        val fd = builder.establish()
        if (fd == null) {
            broadcast("error", "VpnService.establish() returned null")
            updateNotification("Failed"); stopSelf(); return
        }
        tunFd = fd
        running = true

        val protector = object : Carrier.Protector {
            override fun protect(socket: DatagramSocket): Boolean = this@BaleVpnService.protect(socket)
            override fun protect(socket: java.net.Socket): Boolean = this@BaleVpnService.protect(socket)
            override fun bindToUnderlying(socket: DatagramSocket): Boolean = bindToUnderlyingNetwork(socket)
            override fun bindToUnderlying(socket: java.net.Socket): Boolean = bindToUnderlyingNetwork(socket)
        }

        carrier = when (carrierKind) {
            CARRIER_BALE -> {
                broadcast("log", "carrier=bale exitPeer=$DEFAULT_EXIT_PEER_ID")
                BaleCarrier(
                    transportFactory = ::makeBaleTransport,
                    sessId = DEFAULT_TUNNEL_SESS_ID,
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
            stopTunnel(); stopSelf(); return
        }

        readerThread = Thread({ readerLoop(fd) }, "tun-reader").apply { isDaemon = true; start() }
        writerThread = Thread({ writerLoop(fd) }, "tun-writer").apply { isDaemon = true; start() }
        statsThread = Thread({ statsLoop() }, "vpn-stats").apply { isDaemon = true; start() }
        Thread({ selfTestProtectedDns() }, "self-test").apply { isDaemon = true; start() }

        startedAt.set(System.currentTimeMillis())
        updateNotification("Connected")
        broadcastSnapshot("connected")
        broadcast("log", "tun up: 10.77.0.2/24 mtu 1400")
    }

    private fun makeBaleTransport(): Transport {
        val jwt = AuthStore(this).jwt() ?: throw IllegalStateException("no Bale JWT stored")
        val creds = BaleCallClient(jwt = jwt, onLog = { broadcast("log", it) })
            .startCall(peerId = DEFAULT_EXIT_PEER_ID)
        broadcast("log", "joining LiveKit room=${creds.room.ifEmpty { "(unknown)" }}")
        return LiveKitDataChannelTransport(
            appContext = applicationContext,
            url = creds.url,
            token = creds.token,
            topic = "vpn",
            reliable = true,
            onLog = { broadcast("log", it) },
        ).connect()
    }

    private fun readerLoop(fd: ParcelFileDescriptor) {
        val input = FileInputStream(fd.fileDescriptor)
        val buf = ByteArray(32767)
        while (running) {
            try {
                val n = input.read(buf)
                if (n <= 0) { Thread.sleep(5); continue }
                pktsIn.incrementAndGet()
                bytesIn.addAndGet(n.toLong())
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
                bytesOut.addAndGet(pkt.size.toLong())
            } catch (_: InterruptedException) { break }
            catch (t: Throwable) {
                if (running) Log.w(TAG, "writer: ${t.message}")
                break
            }
        }
    }

    private fun stopTunnel() {
        starting = false
        running = false
        try { carrier?.stop() } catch (_: Throwable) {}
        try { startupThread?.interrupt() } catch (_: Throwable) {}
        try { readerThread?.interrupt() } catch (_: Throwable) {}
        try { writerThread?.interrupt() } catch (_: Throwable) {}
        try { statsThread?.interrupt() } catch (_: Throwable) {}
        try { tunFd?.close() } catch (_: Throwable) {}
        tunFd = null
        startupThread = null
        carrier = null
        broadcastSnapshot("disconnected")
        broadcast("log", "tun down. out=${pktsOut.get()}")
    }

    private fun statsLoop() {
        while (running) {
            try {
                Thread.sleep(2000)
                if (running) broadcastSnapshot("connected")
            } catch (_: InterruptedException) {
                break
            } catch (t: Throwable) {
                lastError.set(t.message ?: t.javaClass.simpleName)
            }
        }
    }

    override fun onDestroy() {
        stopTunnel()
        super.onDestroy()
    }

    private fun underlyingNetwork(): android.net.Network? {
        val cm = getSystemService(ConnectivityManager::class.java)
        return cm.allNetworks.firstOrNull { n ->
            val caps = cm.getNetworkCapabilities(n) ?: return@firstOrNull false
            !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        }
    }

    private fun bindToUnderlyingNetwork(sock: DatagramSocket): Boolean = try {
        val net = underlyingNetwork() ?: return false
        net.bindSocket(sock); true
    } catch (t: Throwable) { Log.w(TAG, "bindSocket(udp) failed: ${t.message}"); false }

    private fun bindToUnderlyingNetwork(sock: java.net.Socket): Boolean = try {
        val net = underlyingNetwork() ?: return false
        net.bindSocket(sock); true
    } catch (t: Throwable) { Log.w(TAG, "bindSocket(tcp) failed: ${t.message}"); false }

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

    private fun broadcastSnapshot(status: String) {
        val payload = JSONObject().apply {
            put("status", status)
            put("carrier", carrierKind)
            put("pkts_in", pktsIn.get())
            put("pkts_out", pktsOut.get())
            put("bytes_in", bytesIn.get())
            put("bytes_out", bytesOut.get())
            put("uptime_sec", if (startedAt.get() > 0) (System.currentTimeMillis() - startedAt.get()) / 1000 else 0)
            put("queue_depth", outQueue.size)
            put("last_error", lastError.get())
        }
        val i = Intent(ACTION_STATE)
            .putExtra("kind", "snapshot")
            .putExtra("value", payload.toString())
        LocalBroadcastManager.getInstance(this).sendBroadcast(i)
        broadcast("status", status)
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
        const val CARRIER_AUTO = "auto"
        const val DEFAULT_EXIT_PEER_ID = 423217348L
        const val DEFAULT_TUNNEL_SESS_ID = 0x1111
    }
}
