package com.baleobala.vpn

import android.content.Context

class AppSettings(ctx: Context) {
    private val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    var carrierMode: String
        get() = prefs.getString(KEY_CARRIER, BaleVpnService.CARRIER_AUTO) ?: BaleVpnService.CARRIER_AUTO
        set(value) { prefs.edit().putString(KEY_CARRIER, value).apply() }

    var reconnectOnLaunch: Boolean
        get() = prefs.getBoolean(KEY_RECONNECT, true)
        set(value) { prefs.edit().putBoolean(KEY_RECONNECT, value).apply() }

    var showLogs: Boolean
        get() = prefs.getBoolean(KEY_LOGS, true)
        set(value) { prefs.edit().putBoolean(KEY_LOGS, value).apply() }

    var autoReconnectAfterDrop: Boolean
        get() = prefs.getBoolean(KEY_AUTO_RECONNECT, true)
        set(value) { prefs.edit().putBoolean(KEY_AUTO_RECONNECT, value).apply() }

    var primaryDns: String
        get() = prefs.getString(KEY_DNS, "1.1.1.1") ?: "1.1.1.1"
        set(value) { prefs.edit().putString(KEY_DNS, value).apply() }

    companion object {
        private const val PREFS = "baleobala_settings"
        private const val KEY_CARRIER = "carrier"
        private const val KEY_RECONNECT = "reconnect_on_launch"
        private const val KEY_LOGS = "show_logs"
        private const val KEY_AUTO_RECONNECT = "auto_reconnect"
        private const val KEY_DNS = "primary_dns"
    }
}
