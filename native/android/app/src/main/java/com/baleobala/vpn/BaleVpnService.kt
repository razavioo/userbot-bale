package com.baleobala.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.InetAddress
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicLong

class BaleVpnService : VpnService() {

    @Volatile private var running = false
    private var tunFd: ParcelFileDescriptor? = null
    private var readerThread: Thread? = null
    private var writerThread: Thread? = null
    private var forwarder: UdpForwarder? = null
    private val outQueue = LinkedBlockingQueue<ByteArray>(1024)

    private val pktsIn = AtomicLong(0)
    private val pktsOut = AtomicLong(0)
    private val pktsTcp = AtomicLong(0)
    private val pktsIcmp = AtomicLong(0)
    private val pktsUdp = AtomicLong(0)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_DISCONNECT) {
            stopTunnel()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIF_ID, buildNotification("Connecting…"))
        startTunnel()
        return START_STICKY
    }

    private fun startTunnel() {
        if (running) return
        val builder = Builder()
            .setSession("Baleobala VPN")
            .setMtu(1400)
            .addAddress("10.77.0.2", 24)
            .addRoute("0.0.0.0", 0)
            .addDnsServer("1.1.1.1")
            .addDnsServer("9.9.9.9")
        try {
            builder.addDisallowedApplication(packageName)
        } catch (_: Throwable) { /* never thrown for our own pkg, but be safe */ }

        val fd = builder.establish()
        if (fd == null) {
            broadcast("error", "VpnService.establish() returned null")
            updateNotification("Failed")
            stopSelf()
            return
        }
        tunFd = fd
        running = true

        forwarder = UdpForwarder(
            protector = { sock ->
                val ok = protect(sock)
                if (ok) bindToUnderlyingNetwork(sock)
                ok
            },
            outQueue = outQueue,
            onLog = { line -> broadcast("log", line) },
        )

        Thread({ selfTestProtectedDns() }, "self-test").apply { isDaemon = true; start() }
        readerThread = Thread({ readerLoop(fd) }, "tun-reader").apply { isDaemon = true; start() }
        writerThread = Thread({ writerLoop(fd) }, "tun-writer").apply { isDaemon = true; start() }

        updateNotification("Connected")
        broadcast("status", "connected")
        broadcast("log", "tun up: 10.77.0.2/24 mtu 1400")
    }

    private fun readerLoop(fd: ParcelFileDescriptor) {
        val input = FileInputStream(fd.fileDescriptor)
        val buf = ByteArray(32767)
        val clientAddr: InetAddress = InetAddress.getByName("10.77.0.2")
        while (running) {
            try {
                val n = input.read(buf)
                if (n <= 0) {
                    Thread.sleep(5)
                    continue
                }
                pktsIn.incrementAndGet()
                val parsed = Ipv4Packet.parse(buf, n) ?: continue
                when (parsed.protocol) {
                    Ipv4Packet.PROTO_UDP -> {
                        pktsUdp.incrementAndGet()
                        val udp = Ipv4Packet.parseUdp(buf, parsed.payloadOffset, parsed.payloadLength) ?: continue
                        val srcPort = udp[0]; val dstPort = udp[1]
                        val dataOff = udp[2]; val dataLen = udp[3]
                        val dst = InetAddress.getByAddress(parsed.dstAddr)
                        try {
                            forwarder?.forward(clientAddr, srcPort, dst, dstPort, buf, dataOff, dataLen)
                        } catch (t: Throwable) {
                            Log.w(TAG, "udp forward error: ${t.message}")
                        }
                    }
                    Ipv4Packet.PROTO_TCP -> {
                        if (pktsTcp.incrementAndGet() % 50 == 1L) {
                            broadcast("log", "tcp dropped (v0 udp-only) → ${addrStr(parsed.dstAddr)}")
                        }
                    }
                    Ipv4Packet.PROTO_ICMP -> {
                        if (pktsIcmp.incrementAndGet() % 20 == 1L) {
                            broadcast("log", "icmp dropped → ${addrStr(parsed.dstAddr)}")
                        }
                    }
                }
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
                val n = pktsOut.incrementAndGet()
                if (n <= 5L) {
                    Log.i(TAG, "writer: wrote ${pkt.size}B to TUN (total=$n)")
                    Log.i(TAG, "writer hex: " + pkt.joinToString("") { String.format("%02x", it) })
                }
            } catch (ie: InterruptedException) {
                break
            } catch (t: Throwable) {
                if (running) Log.w(TAG, "writer: ${t.message}")
                break
            }
        }
    }

    private fun stopTunnel() {
        running = false
        try { forwarder?.shutdown() } catch (_: Throwable) {}
        try { readerThread?.interrupt() } catch (_: Throwable) {}
        try { writerThread?.interrupt() } catch (_: Throwable) {}
        try { tunFd?.close() } catch (_: Throwable) {}
        tunFd = null
        broadcast("status", "disconnected")
        broadcast("log", "tun down. in=${pktsIn.get()} udp=${pktsUdp.get()} out=${pktsOut.get()} tcp_drop=${pktsTcp.get()} icmp_drop=${pktsIcmp.get()}")
    }

    private fun bindToUnderlyingNetwork(sock: java.net.DatagramSocket) {
        try {
            val cm = getSystemService(android.net.ConnectivityManager::class.java)
            val net = cm.allNetworks.firstOrNull { n ->
                val caps = cm.getNetworkCapabilities(n) ?: return@firstOrNull false
                !caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN) &&
                    caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET)
            }
            if (net != null) {
                net.bindSocket(sock)
                Log.i(TAG, "bound socket to underlying network $net")
            } else {
                Log.w(TAG, "no underlying non-VPN network found")
            }
        } catch (t: Throwable) {
            Log.w(TAG, "bindSocket failed: ${t.message}")
        }
    }

    private fun selfTestProtectedDns() {
        // Sends a DNS query for example.com to 1.1.1.1 from a protect()ed socket.
        // If this works, protect() grants real network. If this times out, the device
        // itself has no path to the internet (or protect failed) and the forwarder
        // can never get replies either.
        val query = byteArrayOf(
            0x12, 0x34, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            7, 'e'.code.toByte(), 'x'.code.toByte(), 'a'.code.toByte(), 'm'.code.toByte(),
            'p'.code.toByte(), 'l'.code.toByte(), 'e'.code.toByte(),
            3, 'c'.code.toByte(), 'o'.code.toByte(), 'm'.code.toByte(),
            0, 0, 1, 0, 1,
        )
        val sock = java.net.DatagramSocket()
        try {
            val protected = protect(sock)
            bindToUnderlyingNetwork(sock)
            broadcast("log", "selftest: protect=$protected localPort=${sock.localPort}")
            sock.soTimeout = 4000
            for (server in listOf("192.168.100.1", "1.1.1.1", "8.8.8.8")) {
                try {
                    val target = InetAddress.getByName(server)
                    sock.send(java.net.DatagramPacket(query, query.size, target, 53))
                    val replyBuf = ByteArray(2048)
                    val reply = java.net.DatagramPacket(replyBuf, replyBuf.size)
                    sock.receive(reply)
                    broadcast("log", "selftest: got DNS reply ${reply.length}B from ${reply.address.hostAddress} ✅")
                    return
                } catch (st: java.net.SocketTimeoutException) {
                    broadcast("log", "selftest: timeout from $server")
                }
            }
            broadcast("log", "selftest: ALL DNS targets timed out")
        } catch (t: Throwable) {
            broadcast("log", "selftest: FAILED ${t.javaClass.simpleName}: ${t.message}")
        } finally {
            try { sock.close() } catch (_: Throwable) {}
        }
    }

    override fun onDestroy() {
        stopTunnel()
        super.onDestroy()
    }

    private fun broadcast(kind: String, value: String) {
        Log.i(TAG, "[$kind] $value")
        val i = Intent(ACTION_STATE).putExtra("kind", kind).putExtra("value", value)
        LocalBroadcastManager.getInstance(this).sendBroadcast(i)
    }

    private fun addrStr(b: ByteArray): String = "${b[0].toInt() and 0xff}.${b[1].toInt() and 0xff}.${b[2].toInt() and 0xff}.${b[3].toInt() and 0xff}"

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
            this, 0,
            Intent(this, MainActivity::class.java),
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
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIF_ID, buildNotification(text))
    }

    companion object {
        private const val TAG = "BaleVpnService"
        private const val CHANNEL_ID = "baleobala_vpn"
        private const val NOTIF_ID = 1
        const val ACTION_DISCONNECT = "com.baleobala.vpn.DISCONNECT"
        const val ACTION_STATE = "com.baleobala.vpn.STATE"
    }
}
