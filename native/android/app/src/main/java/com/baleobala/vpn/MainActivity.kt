package com.baleobala.vpn

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.baleobala.vpn.bale.AuthStore
import com.baleobala.vpn.databinding.ActivityMainBinding
import com.baleobala.vpn.ui.LoginActivity
import com.google.android.material.snackbar.Snackbar
import org.json.JSONObject

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var store: AuthStore
    private lateinit var settings: AppSettings

    private var receiverRegistered: Boolean = false
    private var uiState: UiState = UiState.Disconnected
    private var lastSnapshot: JSONObject? = null

    private enum class UiState { Disconnected, Connecting, Connected, Stopping, Failed }

    private val vpnPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            startVpnService()
        } else {
            setState(UiState.Disconnected)
            showSnackbar(
                getString(R.string.snackbar_vpn_permission_denied),
                actionLabel = getString(R.string.action_open_settings),
            ) { openAppSettings() }
        }
    }

    private val settingsLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { refreshSettingsUi() }

    private val loginLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        refreshAuthUi()
        if (it.resultCode == RESULT_OK && canStart()) {
            // User just signed in; offer to start the VPN with one tap.
            onTogglePressed()
        }
    }

    private val notifPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (!granted) {
            showSnackbar(
                getString(R.string.snackbar_notif_permission_denied),
                actionLabel = getString(R.string.action_open_settings),
            ) { openAppSettings() }
        }
    }

    private val stateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val kind = intent.getStringExtra("kind") ?: return
            val value = intent.getStringExtra("value") ?: return
            when (kind) {
                "status" -> applyStatus(value)
                "snapshot" -> {
                    val snap = runCatching { JSONObject(value) }.getOrNull() ?: return
                    lastSnapshot = snap
                    applyStatus(snap.optString("status"))
                    renderDetail()
                }
                "error" -> handleError(value)
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
        // Recover state from the running Service (survives Activity recreation
        // on rotation, returning from background, or process restart while the
        // foreground VPN service is still alive).
        BaleVpnService.sharedSnapshotJson?.let {
            lastSnapshot = runCatching { JSONObject(it) }.getOrNull()
        }
        applyStatus(BaleVpnService.sharedStatus)
        renderState()

        binding.settingsButton.setOnClickListener {
            settingsLauncher.launch(Intent(this, SettingsActivity::class.java))
        }

        binding.toggleCard.setOnClickListener { onTogglePressed() }

        binding.signOutButton.setOnClickListener {
            store.clear()
            refreshAuthUi()
        }

        if (settings.reconnectOnLaunch && canStart()) {
            binding.toggleCard.post { if (uiState == UiState.Disconnected) onTogglePressed() }
        }
        handleDebugAutostart(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleDebugAutostart(intent)
    }

    override fun onResume() {
        super.onResume()
        if (!receiverRegistered) {
            LocalBroadcastManager.getInstance(this).registerReceiver(
                stateReceiver, IntentFilter(BaleVpnService.ACTION_STATE)
            )
            receiverRegistered = true
        }
        refreshAuthUi()
    }

    override fun onPause() {
        super.onPause()
        if (receiverRegistered) {
            LocalBroadcastManager.getInstance(this).unregisterReceiver(stateReceiver)
            receiverRegistered = false
        }
    }

    override fun onDestroy() {
        if (receiverRegistered) {
            runCatching { LocalBroadcastManager.getInstance(this).unregisterReceiver(stateReceiver) }
            receiverRegistered = false
        }
        super.onDestroy()
    }

    // --- Toggle ---

    private fun onTogglePressed() {
        when (uiState) {
            UiState.Stopping -> return
            UiState.Connecting, UiState.Connected -> requestDisconnect()
            UiState.Disconnected, UiState.Failed -> requestConnect()
        }
    }

    private fun requestConnect() {
        if (store.jwt() == null) {
            showSnackbar(
                getString(R.string.snackbar_sign_in_required),
                actionLabel = getString(R.string.action_sign_in),
            ) { loginLauncher.launch(Intent(this, LoginActivity::class.java)) }
            return
        }
        if (settings.coordinatorPeerId <= 0L) {
            showSnackbar(
                getString(R.string.snackbar_relay_required),
                actionLabel = getString(R.string.action_choose),
            ) { settingsLauncher.launch(Intent(this, SettingsActivity::class.java)) }
            return
        }
        setState(UiState.Connecting)
        val prep = VpnService.prepare(this)
        if (prep != null) vpnPermissionLauncher.launch(prep) else startVpnService()
    }

    private fun requestDisconnect() {
        setState(UiState.Stopping)
        val i = Intent(this, BaleVpnService::class.java).apply { action = BaleVpnService.ACTION_DISCONNECT }
        ContextCompat.startForegroundService(this, i)
    }

    private fun startVpnService() {
        val intent = Intent(this, BaleVpnService::class.java).apply {
            putExtra(BaleVpnService.EXTRA_CARRIER, settings.carrierMode)
        }
        ContextCompat.startForegroundService(this, intent)
    }

    private fun canStart(): Boolean = store.jwt() != null && settings.coordinatorPeerId > 0L

    // --- State ---

    private fun applyStatus(value: String) {
        val newState = when (value) {
            "connected" -> UiState.Connected
            "connecting" -> UiState.Connecting
            "disconnected" -> UiState.Disconnected
            else -> uiState
        }
        if (newState != uiState) setState(newState) else renderState()
    }

    private fun setState(s: UiState) {
        uiState = s
        if (s == UiState.Disconnected) lastSnapshot = null
        renderState()
    }

    private fun renderState() {
        data class Render(val statusRes: Int, val bgColorRes: Int, val showProgress: Boolean, val clickable: Boolean, val cdRes: Int)
        val r = when (uiState) {
            UiState.Disconnected -> Render(R.string.status_disconnected, R.color.state_idle, false, true, R.string.cd_toggle_idle)
            UiState.Connecting -> Render(R.string.status_connecting, R.color.state_connecting, true, true, R.string.cd_toggle_connecting)
            UiState.Connected -> Render(R.string.status_connected, R.color.state_connected, false, true, R.string.cd_toggle_connected)
            UiState.Stopping -> Render(R.string.state_disconnecting, R.color.state_idle, true, false, R.string.cd_toggle_busy)
            UiState.Failed -> Render(R.string.state_failed, R.color.state_error, false, true, R.string.cd_toggle_idle)
        }
        binding.statusText.setText(r.statusRes)
        binding.toggleCard.setCardBackgroundColor(ContextCompat.getColor(this, r.bgColorRes))
        binding.toggleCard.contentDescription = getString(r.cdRes)
        binding.connectingProgress.visibility = if (r.showProgress) View.VISIBLE else View.GONE
        binding.toggleCard.isClickable = r.clickable
        renderDetail()
        refreshAuthUi()
    }

    private fun renderDetail() {
        val snap = lastSnapshot
        val text: String? = when (uiState) {
            UiState.Connected -> {
                val peer = settings.coordinatorPeerId
                val uptime = snap?.optLong("uptime_sec", 0L) ?: 0L
                val bIn = snap?.optLong("bytes_in", 0L) ?: 0L
                val bOut = snap?.optLong("bytes_out", 0L) ?: 0L
                getString(
                    R.string.notif_text_connected_fmt,
                    peer,
                    formatDuration(uptime),
                    humanBytes(bIn),
                    humanBytes(bOut),
                )
            }
            UiState.Failed -> snap?.optString("last_error", "")?.takeIf { it.isNotBlank() }
            else -> null
        }
        if (text.isNullOrBlank()) {
            binding.statusDetail.visibility = View.GONE
        } else {
            binding.statusDetail.text = text
            binding.statusDetail.visibility = View.VISIBLE
        }
    }

    private fun refreshAuthUi() {
        val phone = store.phoneNumber()
        if (store.jwt() != null) {
            binding.authStatus.text = if (settings.coordinatorPeerId <= 0L) {
                getString(R.string.relay_not_set)
            } else if (phone != null) {
                getString(R.string.login_logged_in, phone.toString())
            } else {
                getString(R.string.login_logged_in, "")
            }
            binding.signOutButton.visibility = View.VISIBLE
        } else {
            binding.authStatus.text = getString(R.string.login_not_signed_in_bale)
            binding.signOutButton.visibility = View.GONE
        }
    }

    private fun refreshSettingsUi() {
        settings = AppSettings(this)
        renderState()
    }

    // --- Errors ---

    private fun handleError(raw: String) {
        val lower = raw.lowercase()
        val (msg, action, onAction) = when {
            "no bale jwt" in lower || "sign in" in lower || "401" in lower -> {
                store.clear()
                Triple(getString(R.string.snackbar_auth_expired), getString(R.string.action_sign_in)) {
                    loginLauncher.launch(Intent(this, LoginActivity::class.java))
                }
            }
            "callnotapproved" in lower -> Triple(
                getString(R.string.snackbar_relay_rejected),
                getString(R.string.action_choose),
            ) { settingsLauncher.launch(Intent(this, SettingsActivity::class.java)) }
            "relay" in lower && "peer" in lower -> Triple(
                getString(R.string.snackbar_relay_required),
                getString(R.string.action_choose),
            ) { settingsLauncher.launch(Intent(this, SettingsActivity::class.java)) }
            "all dns targets timed out" in lower || "offline" in lower -> Triple(
                getString(R.string.snackbar_offline),
                getString(R.string.action_retry),
            ) { onTogglePressed() }
            else -> Triple(
                getString(R.string.snackbar_generic_error, raw.take(120)),
                getString(R.string.action_retry),
            ) { onTogglePressed() }
        }
        if (uiState == UiState.Connecting) setState(UiState.Failed)
        showSnackbar(msg, action, onAction)
    }

    // --- Notification permission ---

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33) return
        val granted = ActivityCompat.checkSelfPermission(
            this, android.Manifest.permission.POST_NOTIFICATIONS
        ) == android.content.pm.PackageManager.PERMISSION_GRANTED
        if (granted) return
        notifPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun openAppSettings() {
        val i = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = Uri.fromParts("package", packageName, null)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        startActivity(i)
    }

    // --- Helpers ---

    private fun showSnackbar(text: String, actionLabel: String? = null, onAction: (() -> Unit)? = null) {
        val sb = Snackbar.make(binding.rootCoordinator, text, Snackbar.LENGTH_LONG)
        if (actionLabel != null && onAction != null) {
            sb.setAction(actionLabel) { onAction() }
        }
        sb.show()
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

    private fun handleDebugAutostart(intent: Intent?) {
        if (!isDebuggable() || intent?.getBooleanExtra(EXTRA_DEBUG_AUTOSTART, false) != true) return
        val requestedCarrier = intent.getStringExtra(BaleVpnService.EXTRA_CARRIER)
        if (requestedCarrier == BaleVpnService.CARRIER_BALE) {
            settings.carrierMode = BaleVpnService.CARRIER_BALE
            refreshSettingsUi()
        }
        binding.toggleCard.post { if (uiState == UiState.Disconnected) onTogglePressed() }
    }

    private fun isDebuggable(): Boolean =
        (applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0

    companion object {
        private const val EXTRA_DEBUG_AUTOSTART = "debug_autostart"
    }
}
