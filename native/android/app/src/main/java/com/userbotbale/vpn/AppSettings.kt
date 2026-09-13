package com.userbot_bale.vpn

import android.content.Context

/**
 * App-level settings persisted in SharedPreferences.
 *
 * Relay peer_ids are user-managed: the user enters them in the Settings
 * screen and they live in `KEY_RELAY_PEER_IDS` as a comma-separated list
 * of decimal Bale user_ids. The client tries each in order, with a
 * deterministic per-device shuffle for load distribution, falling back
 * to the next when one is busy or unreachable. There is no coordinator
 * and no bundled relay list.
 */
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
     * Ordered list of relay peer_ids the user has added. The client
     * dials these in order (with a per-device shuffle for fairness)
     * and falls back when one is busy or unreachable.
     */
    var relayPeerIds: List<Long>
        get() {
            val raw = prefs.getString(KEY_RELAY_PEER_IDS, "") ?: ""
            if (raw.isBlank()) return emptyList()
            return raw.split(",").mapNotNull { it.trim().toLongOrNull() }
                .filter { it > 0L }
        }
        set(value) {
            val cleaned = value.filter { it > 0L }.distinct().joinToString(",")
            prefs.edit().putString(KEY_RELAY_PEER_IDS, cleaned).apply()
        }

    fun addRelayPeerId(peerId: Long) {
        if (peerId <= 0L) return
        relayPeerIds = relayPeerIds + peerId
    }

    fun removeRelayPeerId(peerId: Long) {
        relayPeerIds = relayPeerIds.filterNot { it == peerId }
    }

    companion object {
        private const val PREFS = "userbot_bale_settings"
        private const val KEY_CARRIER = "carrier"
        private const val KEY_RECONNECT = "reconnect_on_launch"
        private const val KEY_LOGS = "show_logs"
        private const val KEY_AUTO_RECONNECT = "auto_reconnect"
        private const val KEY_DNS = "primary_dns"
        private const val KEY_RELAY_PEER_IDS = "relay_peer_ids"
    }
}
