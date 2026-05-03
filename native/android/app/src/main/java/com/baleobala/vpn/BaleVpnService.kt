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
import com.baleobala.vpn.tunnel.LiveKitDataChannelTransport
import com.baleobala.vpn.tunnel.Transport
import org.json.JSONObject
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.DatagramSocket
import java.net.Inet4Address
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * Top-level VpnService. Brings up the TUN, hands packets to the configured
 * [Carrier], and writes carrier replies back to the TUN.
 *
 * The Android app only exposes the real Bale carrier: it dials the configured
 * exit peer, joins the returned LiveKit room, and moves tunnel frames over
 * topic `vpn`. Local NAT remains test/dev code and is not a user-visible VPN.
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
    private var startupHeartbeatThread: Thread? = null
    private var carrier: Carrier? = null
    private var exitPeerId: Long = 0L
    @Volatile private var inflightWs: com.baleobala.vpn.bale.BaleWsClient? = null
    @Volatile private var inflightTransport: LiveKitDataChannelTransport? = null
    private val outQueue = java.util.concurrent.LinkedBlockingQueue<ByteArray>(1024)

    private val pktsOut = AtomicLong(0)
    private val pktsIn = AtomicLong(0)
    private val bytesOut = AtomicLong(0)
    private val bytesIn = AtomicLong(0)
    private val startedAt = AtomicLong(0)
    private val lastError = AtomicReference("")
    private val reconnecting = AtomicBoolean(false)
    private val reconnectAttempt = AtomicInteger(0)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_DISCONNECT) {
            reconnectAttempt.set(0)
            reconnecting.set(false)
            stopTunnel()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIF_ID, buildNotification("connecting"))
        carrierKind = intent?.getStringExtra(EXTRA_CARRIER) ?: AppSettings(this).carrierMode
        if (carrierKind == CARRIER_AUTO) carrierKind = detectCarrier()
        startTunnelAsync(carrierKind)
        return START_STICKY
    }

    private fun detectCarrier(): String {
        return CARRIER_BALE
    }

    private fun startTunnelAsync(carrierKind: String) {
        if (running || starting) return
        starting = true
        broadcast("status", "connecting")
        startupHeartbeatThread = Thread({
            // Re-broadcast "connecting" every second so freshly recreated
            // Activities (e.g. after a rotation) can pick up the in-progress
            // state immediately instead of seeing a stale "disconnected".
            while (starting && !running) {
                try { Thread.sleep(1000) } catch (_: InterruptedException) { return@Thread }
                if (starting && !running) broadcast("status", "connecting")
            }
        }, "vpn-startup-heartbeat").apply { isDaemon = true; start() }
        startupThread = Thread({
            try {
                startTunnel(carrierKind)
            } catch (_: InterruptedException) {
                broadcast("log", "startup canceled")
                stopTunnel()
                stopSelf()
            } catch (t: Throwable) {
                broadcast("error", "startup failed: ${t.message}")
                updateNotification("failed", t.message)
                stopTunnel()
                stopSelf()
            } finally {
                starting = false
            }
        }, "vpn-startup").apply { isDaemon = true; start() }
    }

    private fun startTunnel(carrierKind: String) {
        if (running) return
        resetStats()
        outQueue.clear()
        if (carrierKind != CARRIER_BALE) {
            broadcast("error", "Local NAT is disabled. Baleobala must use a real Bale tunnel.")
            updateNotification("failed", "Local NAT is disabled")
            stopSelf()
            return
        }
        if (AuthStore(this).jwt() == null) {
            broadcast("error", "Sign in to Bale before connecting the system VPN.")
            updateNotification("failed", "Sign in required")
            stopSelf()
            return
        }
        exitPeerId = AppSettings(this).exitPeerId
        if (exitPeerId <= 0L) {
            broadcast("error", "Set a Bale relay peer ID before connecting.")
            updateNotification("failed", "Relay peer required")
            stopSelf()
            return
        }
        val builder = Builder()
            .setSession("Baleobala VPN")
            .setMtu(1400)
            .addAddress("10.77.0.2", 24)
            .addRoute("0.0.0.0", 0)
        dnsServers().forEach { builder.addDnsServer(it) }
        try { builder.addDisallowedApplication(packageName) } catch (_: Throwable) {}

        val fd = builder.establish()
        if (fd == null) {
            broadcast("error", "VpnService.establish() returned null")
            updateNotification("failed", "VpnService.establish() returned null"); stopSelf(); return
        }
        tunFd = fd
        running = true

        carrier = BaleCarrier(
            transportFactory = ::makeBaleTransport,
            sessId = DEFAULT_TUNNEL_SESS_ID,
            onLog = { broadcast("log", it) },
            onCarrierDead = { reason -> handleTunnelDrop("tunnel dead: $reason") },
        )
        broadcast("log", "carrier=bale exitPeer=$exitPeerId")
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
        updateNotification("connected", connectedDetail())
        broadcastSnapshot("connected")
        broadcast("log", "tun up: 10.77.0.2/24 mtu 1400")
    }

    private fun makeBaleTransport(): Transport {
        val jwt = AuthStore(this).jwt() ?: throw IllegalStateException("no Bale JWT stored")
        val creds = BaleCallClient(
            jwt = jwt,
            onLog = { broadcast("log", it) },
            onWsCreated = { inflightWs = it },
        ).startCall(peerId = exitPeerId, timeoutMs = STARTCALL_TIMEOUT_MS)
        // Bale call done; release the WS reference so a later cancel only
        // touches the LiveKit transport.
        inflightWs = null
        broadcast("log", "joining LiveKit room=${creds.room.ifEmpty { "(unknown)" }}")
        val tx = LiveKitDataChannelTransport(
            appContext = applicationContext,
            url = creds.url,
            token = creds.token,
            topic = "vpn",
            reliable = true,
            onLog = { broadcast("log", it) },
            // LiveKit's data channel can go silent without throwing on
            // any TUN-side I/O. Without this hook the carrier keeps
            // racking up max-retry drops forever and the user sees a
            // session that's "connected" but moves no traffic. Bouncing
            // the tunnel triggers the same reconnect path as a TUN
            // read/write error.
            onDisconnected = { handleTunnelDrop("LiveKit DataChannel disconnected") },
        )
        inflightTransport = tx
        try {
            return tx.connect(timeoutMs = LIVEKIT_TIMEOUT_MS)
        } finally {
            inflightTransport = null
        }
    }

    private fun readerLoop(fd: ParcelFileDescriptor) {
        FileInputStream(fd.fileDescriptor).use { input ->
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
                    if (running) handleTunnelDrop("reader stopped: ${t.message}")
                    break
                }
            }
        }
    }

    private fun writerLoop(fd: ParcelFileDescriptor) {
        FileOutputStream(fd.fileDescriptor).use { output ->
            while (running) {
                try {
                    val pkt = outQueue.take()
                    output.write(pkt)
                    pktsOut.incrementAndGet()
                    bytesOut.addAndGet(pkt.size.toLong())
                } catch (_: InterruptedException) { break }
                catch (t: Throwable) {
                    if (running) Log.w(TAG, "writer: ${t.message}")
                    if (running) handleTunnelDrop("writer stopped: ${t.message}")
                    break
                }
            }
        }
    }

    private fun stopTunnel() {
        starting = false
        running = false
        // Force-close any in-flight startup resources so a blocked
        // BaleCallClient.startCall / LiveKit.connect unblocks immediately
        // and the user can actually cancel a stuck "Connecting" state.
        try { inflightWs?.close() } catch (_: Throwable) {}
        try { inflightTransport?.close() } catch (_: Throwable) {}
        inflightWs = null
        inflightTransport = null
        try { carrier?.stop() } catch (_: Throwable) {}
        try { startupThread?.interrupt() } catch (_: Throwable) {}
        try { startupHeartbeatThread?.interrupt() } catch (_: Throwable) {}
        try { readerThread?.interrupt() } catch (_: Throwable) {}
        try { writerThread?.interrupt() } catch (_: Throwable) {}
        try { statsThread?.interrupt() } catch (_: Throwable) {}
        // Close tunFd before joining so blocking reads/writes unblock.
        try { tunFd?.close() } catch (_: Throwable) {}
        joinQuietly(readerThread)
        joinQuietly(writerThread)
        joinQuietly(statsThread)
        joinQuietly(startupHeartbeatThread)
        joinQuietly(startupThread)
        outQueue.clear()
        tunFd = null
        startupThread = null
        startupHeartbeatThread = null
        readerThread = null
        writerThread = null
        statsThread = null
        carrier = null
        sharedSnapshotJson = null
        broadcastSnapshot("disconnected")
        broadcast("log", "tun down. out=${pktsOut.get()}")
    }

    private fun handleTunnelDrop(reason: String) {
        lastError.set(reason)
        broadcast("error", reason)
        if (!AppSettings(this).autoReconnectAfterDrop) {
            stopTunnel()
            stopSelf()
            return
        }
        if (!reconnecting.compareAndSet(false, true)) return
        val mode = carrierKind
        val uptimeMs = System.currentTimeMillis() - startedAt.get()
        if (uptimeMs > RECONNECT_STABLE_RESET_MS) reconnectAttempt.set(0)
        val attempt = reconnectAttempt.incrementAndGet()
        val delayMs = minOf(RECONNECT_MAX_DELAY_MS, RECONNECT_BASE_DELAY_MS * (1L shl minOf(attempt - 1, 4)))
        Thread({
            stopTunnel()
            try { Thread.sleep(delayMs) } catch (_: InterruptedException) {}
            reconnecting.set(false)
            broadcast("log", "reconnect attempt=$attempt delay=${delayMs}ms")
            startTunnelAsync(mode)
        }, "vpn-reconnect").apply { isDaemon = true; start() }
    }

    private fun resetStats() {
        pktsOut.set(0)
        pktsIn.set(0)
        bytesOut.set(0)
        bytesIn.set(0)
        startedAt.set(0)
        lastError.set("")
    }

    private fun dnsServers(): List<String> {
        val primary = AppSettings(this).primaryDns.trim()
        val candidates = buildList {
            addAll(underlyingDnsServers())
            if (isIpv4Literal(primary)) add(primary)
            addAll(FALLBACK_DNS)
        }
        return candidates.distinct()
    }

    @Suppress("DEPRECATION")
    private fun underlyingDnsServers(): List<String> {
        val cm = getSystemService(ConnectivityManager::class.java)
        return cm.allNetworks
            .asSequence()
            .filter { network ->
                val caps = cm.getNetworkCapabilities(network) ?: return@filter false
                !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
                    caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            }
            .flatMap { network ->
                cm.getLinkProperties(network)?.dnsServers.orEmpty().asSequence()
            }
            .filterIsInstance<Inet4Address>()
            .mapNotNull { it.hostAddress }
            .filter { it.isNotBlank() }
            .toList()
    }

    private fun isIpv4Literal(value: String): Boolean = Companion.isIpv4Literal(value)

    private fun statsLoop() {
        var tick = 0
        while (running) {
            try {
                Thread.sleep(2000)
                if (!running) break
                broadcastSnapshot("connected")
                // Refresh notification every ~6s to keep traffic counters fresh
                // without spamming the notification manager.
                if (tick++ % 3 == 0) updateNotification("connected", connectedDetail())
            } catch (_: InterruptedException) {
                break
            } catch (t: Throwable) {
                lastError.set(t.message ?: t.javaClass.simpleName)
            }
        }
    }

    private fun joinQuietly(t: Thread?, timeoutMs: Long = 500) {
        if (t == null || t === Thread.currentThread() || !t.isAlive) return
        try { t.join(timeoutMs) } catch (_: InterruptedException) { Thread.currentThread().interrupt() }
    }

    override fun onDestroy() {
        stopTunnel()
        super.onDestroy()
    }

    @Suppress("DEPRECATION")
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
            for (server in dnsServers()) {
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
        if (kind == "status") sharedStatus = value
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
        val json = payload.toString()
        sharedStatus = status
        sharedSnapshotJson = json
        val i = Intent(ACTION_STATE)
            .putExtra("kind", "snapshot")
            .putExtra("value", json)
        LocalBroadcastManager.getInstance(this).sendBroadcast(i)
        broadcast("status", status)
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(CHANNEL_ID, getString(R.string.vpn_channel_name), NotificationManager.IMPORTANCE_LOW)
                )
            }
        }
    }

    private fun buildNotification(state: String, detail: String? = null): Notification {
        ensureChannel()
        val openIntent = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val disconnectIntent = PendingIntent.getService(
            this, 1,
            Intent(this, BaleVpnService::class.java).apply { action = ACTION_DISCONNECT },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val title = when (state) {
            "connected" -> getString(R.string.status_connected)
            "connecting" -> getString(R.string.status_connecting)
            "failed" -> getString(R.string.state_failed)
            else -> getString(R.string.status_disconnected)
        }
        val text = detail ?: when (state) {
            "connected" -> getString(R.string.notif_text_idle)
            "connecting" -> getString(R.string.notif_text_connecting)
            "failed" -> getString(R.string.notif_text_failed)
            else -> getString(R.string.notif_text_idle)
        }
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_shield)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(state == "connected" || state == "connecting")
            .setOnlyAlertOnce(true)
            .setContentIntent(openIntent)
        if (state == "connected" || state == "connecting") {
            builder.addAction(0, getString(R.string.notif_action_disconnect), disconnectIntent)
        }
        return builder.build()
    }

    private fun updateNotification(state: String, detail: String? = null) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(state, detail))
    }

    private fun connectedDetail(): String {
        val uptimeSec = if (startedAt.get() > 0) (System.currentTimeMillis() - startedAt.get()) / 1000 else 0L
        return getString(
            R.string.notif_text_connected_fmt,
            exitPeerId,
            formatDuration(uptimeSec),
            humanBytes(bytesIn.get()),
            humanBytes(bytesOut.get()),
        )
    }

    private fun humanBytes(n: Long): String {
        if (n < 1024) return "${n}B"
        val units = listOf("KB", "MB", "GB", "TB")
        var v = n.toDouble() / 1024.0
        var idx = 0
        while (v >= 1024.0 && idx < units.size - 1) { v /= 1024.0; idx++ }
        return String.format("%.1f%s", v, units[idx])
    }

    private fun formatDuration(seconds: Long): String {
        val s = seconds % 60
        val m = (seconds / 60) % 60
        val h = seconds / 3600
        return if (h > 0) String.format("%d:%02d:%02d", h, m, s)
        else String.format("%02d:%02d", m, s)
    }

    companion object {
        private const val TAG = "BaleVpnService"
        private const val CHANNEL_ID = "baleobala_vpn"
        private const val NOTIF_ID = 1
        const val ACTION_DISCONNECT = "com.baleobala.vpn.DISCONNECT"
        const val ACTION_STATE = "com.baleobala.vpn.STATE"

        /** Cached state for fresh Activity instances (rotation, return-from-background). */
        @Volatile var sharedStatus: String = "disconnected"
        @Volatile var sharedSnapshotJson: String? = null
        const val EXTRA_CARRIER = "carrier"
        const val CARRIER_LOCAL = "local-nat"
        const val CARRIER_BALE = "bale"
        const val CARRIER_AUTO = "auto"
        const val DEFAULT_TUNNEL_SESS_ID = 0x1111
        // Tighter than the underlying defaults so a stuck startup surfaces
        // as a clear error instead of an ~2-minute spinner.
        private const val STARTCALL_TIMEOUT_MS = 30_000L
        private const val LIVEKIT_TIMEOUT_MS = 20_000L
        private const val RECONNECT_BASE_DELAY_MS = 3_000L
        private const val RECONNECT_MAX_DELAY_MS = 30_000L
        private const val RECONNECT_STABLE_RESET_MS = 60_000L
        internal val FALLBACK_DNS = listOf("185.51.200.2", "1.1.1.1", "8.8.8.8")

        internal fun isIpv4Literal(value: String): Boolean {
            val parts = value.split(".")
            return parts.size == 4 && parts.all { part ->
                val n = part.toIntOrNull()
                part.isNotEmpty() && part.length <= 3 && part.all(Char::isDigit) && n != null && n in 0..255
            }
        }
    }
}
