package com.baleobala.vpn

import android.content.Context
import com.baleobala.vpn.BuildConfig

class AppSettings(ctx: Context) {
    private val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    var carrierMode: String
        get() = when (prefs.getString(KEY_CARRIER, BaleVpnService.CARRIER_BALE)) {
            BaleVpnService.CARRIER_BALE -> BaleVpnService.CARRIER_BALE
            else -> BaleVpnService.CARRIER_BALE
        }
        set(value) { prefs.edit().putString(KEY_CARRIER, value).apply() }

    var reconnectOnLaunch: Boolean
        get() = prefs.getBoolean(KEY_RECONNECT, false)
        set(value) { prefs.edit().putBoolean(KEY_RECONNECT, value).apply() }

    var showLogs: Boolean
        get() = prefs.getBoolean(KEY_LOGS, false)
        set(value) { prefs.edit().putBoolean(KEY_LOGS, value).apply() }

    var autoReconnectAfterDrop: Boolean
        get() = prefs.getBoolean(KEY_AUTO_RECONNECT, true)
        set(value) { prefs.edit().putBoolean(KEY_AUTO_RECONNECT, value).apply() }

    var primaryDns: String
        get() = prefs.getString(KEY_DNS, "1.1.1.1") ?: "1.1.1.1"
        set(value) { prefs.edit().putString(KEY_DNS, value).apply() }

    /**
     * The coordinator peer_id this client should call to get a relay assignment.
     * Read-only in production: value comes from [BuildConfig.COORDINATOR_PEER_ID].
     * In debug builds it can be overridden via SharedPreferences (dev/staging).
     */
    val coordinatorPeerId: Long
        get() {
            val override = prefs.getLong(KEY_COORDINATOR_PEER_ID, 0L)
            return if (override > 0L) override else BuildConfig.COORDINATOR_PEER_ID
        }

    companion object {
        private const val PREFS = "baleobala_settings"
        private const val KEY_CARRIER = "carrier"
        private const val KEY_RECONNECT = "reconnect_on_launch"
        private const val KEY_LOGS = "show_logs"
        private const val KEY_AUTO_RECONNECT = "auto_reconnect"
        private const val KEY_DNS = "primary_dns"
        private const val KEY_COORDINATOR_PEER_ID = "coordinator_peer_id"
    }
}
