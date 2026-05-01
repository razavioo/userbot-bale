package com.baleobala.vpn

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.baleobala.vpn.bale.AuthStore
import com.baleobala.vpn.databinding.ActivityMainBinding
import com.baleobala.vpn.ui.LoginActivity
import org.json.JSONObject

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var store: AuthStore
    private lateinit var settings: AppSettings
    private val logBuffer = ArrayDeque<String>()
    private var connected: Boolean = false
    private var currentStatusText: String = "Disconnected"
    private var currentCarrierText: String = "Auto"
    private var detailsVisible: Boolean = true
    private var currentStats: JSONObject = JSONObject()

    private val vpnPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) startVpnService()
        else appendLog("permission denied")
    }

    private val settingsLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        refreshSettingsUi()
    }

    private val stateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val kind = intent.getStringExtra("kind") ?: return
            val value = intent.getStringExtra("value") ?: return
            when (kind) {
                "status" -> {
                    connected = value == "connected"
                    currentStatusText = when (value) {
                        "connected" -> getString(R.string.status_connected)
                        "connecting" -> getString(R.string.status_connecting)
                        else -> getString(R.string.status_disconnected)
                    }
                    syncStateUi()
                }
                "snapshot" -> {
                    currentStats = JSONObject(value)
                    connected = currentStats.optString("status") == "connected"
                    currentStatusText = when (currentStats.optString("status")) {
                        "connected" -> getString(R.string.status_connected)
                        "connecting" -> getString(R.string.status_connecting)
                        else -> getString(R.string.status_disconnected)
                    }
                    updateStatsUi()
                    syncStateUi()
                }
                "log", "error" -> appendLog(value)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        store = AuthStore(this)
        settings = AppSettings(this)

        maybeRequestNotificationPermission()
        refreshSettingsUi()
        syncStateUi()
        appendLog(getString(R.string.log_ready))

        binding.settingsButton.setOnClickListener {
            settingsLauncher.launch(Intent(this, SettingsActivity::class.java))
        }

        binding.toggleButton.setOnClickListener {
            if (connected) {
                val i = Intent(this, BaleVpnService::class.java).apply { action = BaleVpnService.ACTION_DISCONNECT }
                startService(i)
            } else {
                startVpnOrLogin()
            }
        }

        binding.signOutButton.setOnClickListener {
            store.clear()
            refreshAuthUi()
        }

        if (settings.reconnectOnLaunch && store.jwt() != null) {
            binding.toggleButton.post { if (!connected) startVpnOrLogin() }
        }
    }

    override fun onResume() {
        super.onResume()
        LocalBroadcastManager.getInstance(this).registerReceiver(
            stateReceiver, IntentFilter(BaleVpnService.ACTION_STATE)
        )
        refreshAuthUi()
    }

    override fun onPause() {
        super.onPause()
        LocalBroadcastManager.getInstance(this).unregisterReceiver(stateReceiver)
    }

    private fun refreshAuthUi() {
        val phone = store.phoneNumber()
        if (store.jwt() != null) {
            binding.authStatus.text = if (phone != null)
                getString(R.string.login_logged_in, phone.toString())
            else
                "Signed in"
            binding.signOutButton.visibility = View.VISIBLE
        } else {
            binding.authStatus.text = "Not signed in — Connect will open Bale sign-in"
            binding.signOutButton.visibility = View.GONE
        }
    }

    private fun refreshSettingsUi() {
        settings = AppSettings(this)
        currentCarrierText = when (settings.carrierMode) {
            BaleVpnService.CARRIER_BALE -> getString(R.string.settings_carrier_bale)
            BaleVpnService.CARRIER_LOCAL -> getString(R.string.settings_carrier_local)
            else -> getString(R.string.dashboard_carrier_auto)
        }
        detailsVisible = settings.showLogs
        binding.quickActionSecondary.text = if (connected) getString(R.string.dashboard_disconnect_hint) else getString(R.string.dashboard_login_hint)
        binding.detailsGroup.visibility = if (detailsVisible) View.VISIBLE else View.GONE
        syncStateUi()
    }

    private fun startVpnService() {
        binding.statusText.text = getString(R.string.status_connecting)
        val intent = Intent(this, BaleVpnService::class.java).apply {
            putExtra(BaleVpnService.EXTRA_CARRIER, settings.carrierMode)
        }
        startService(intent)
    }

    private fun startVpnOrLogin() {
        if (store.jwt() == null) {
            startActivity(Intent(this, LoginActivity::class.java))
            return
        }
        val prep = VpnService.prepare(this)
        if (prep != null) vpnPermissionLauncher.launch(prep) else startVpnService()
    }

    private fun appendLog(line: String) {
        logBuffer.addLast(line)
        while (logBuffer.size > 200) logBuffer.removeFirst()
        updateStatsUi()
        if (detailsVisible) binding.logScroll.post { binding.logScroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun syncStateUi() {
        binding.statusText.text = currentStatusText
        binding.toggleButton.text = if (connected) getString(R.string.disconnect) else getString(R.string.connect)
        binding.connectionPill.text = if (connected) getString(R.string.dashboard_pill_connected) else getString(R.string.dashboard_pill_disconnected)
        binding.connectionPill.isSelected = connected
        binding.carrierText.text = currentCarrierText
        binding.quickActionSecondary.text = if (connected) getString(R.string.dashboard_disconnect_hint) else getString(R.string.dashboard_login_hint)
        refreshAuthUi()
        binding.detailsGroup.visibility = if (detailsVisible) View.VISIBLE else View.GONE
        updateStatsUi()
    }

    private fun updateStatsUi() {
        if (!detailsVisible) {
            binding.logText.text = ""
            return
        }
        val stats = buildString {
            appendLine(logBuffer.joinToString("\n"))
            if (logBuffer.isNotEmpty()) appendLine("")
            appendLine("status=${currentStats.optString("status", currentStatusText)}")
            appendLine("carrier=${currentStats.optString("carrier", currentCarrierText)}")
            appendLine("uptime_sec=${currentStats.optLong("uptime_sec", 0)}")
            appendLine("pkts_in=${currentStats.optLong("pkts_in", 0)} pkts_out=${currentStats.optLong("pkts_out", 0)}")
            appendLine("bytes_in=${currentStats.optLong("bytes_in", 0)} bytes_out=${currentStats.optLong("bytes_out", 0)}")
            appendLine("queue_depth=${currentStats.optInt("queue_depth", 0)}")
            val lastError = currentStats.optString("last_error", "")
            if (lastError.isNotBlank()) appendLine("last_error=$lastError")
        }
        binding.logText.text = stats.trimEnd()
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33) {
            val granted = ActivityCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
            if (!granted) {
                ActivityCompat.requestPermissions(this, arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 901)
            }
        }
    }
}
