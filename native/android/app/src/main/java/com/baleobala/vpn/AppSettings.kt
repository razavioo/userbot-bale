package com.baleobala.vpn

import android.content.Context

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

    var exitPeerId: Long
        get() = prefs.getLong(KEY_EXIT_PEER_ID, 0L)
        set(value) { prefs.edit().putLong(KEY_EXIT_PEER_ID, value).commit() }

    companion object {
        private const val PREFS = "baleobala_settings"
        private const val KEY_CARRIER = "carrier"
        private const val KEY_RECONNECT = "reconnect_on_launch"
        private const val KEY_LOGS = "show_logs"
        private const val KEY_AUTO_RECONNECT = "auto_reconnect"
        private const val KEY_DNS = "primary_dns"
        private const val KEY_EXIT_PEER_ID = "exit_peer_id"
    }
}
