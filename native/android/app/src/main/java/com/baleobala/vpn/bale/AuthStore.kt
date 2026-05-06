package com.baleobala.vpn.bale

import android.content.Context
import android.content.SharedPreferences
import android.util.Log

/**
 * JWT/phone persistence backed by plain SharedPreferences. Older builds
 * used EncryptedSharedPreferences; that dependency is currently removed
 * to keep the offline build green. JWT is stored unencrypted.
 *
 * One-shot migration: if a legacy plain-prefs JWT exists from older
 * builds (v1 prefs file), copy it into the v2 store on first read.
 */
class AuthStore(ctx: Context) {
    private val prefs: SharedPreferences = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    init {
        migrateLegacyPrefsIfPresent(ctx)
    }

    fun saveJwt(jwt: String, phoneNumber: Long? = null, userId: Long? = null) {
        prefs.edit()
            .putString(KEY_JWT, jwt)
            .apply { if (phoneNumber != null) putLong(KEY_PHONE, phoneNumber) }
            .apply { if (userId != null && userId > 0) putLong(KEY_USER_ID, userId) }
            .apply()
    }

    fun saveUserId(userId: Long) {
        if (userId <= 0) return
        prefs.edit().putLong(KEY_USER_ID, userId).apply()
    }

    fun jwt(): String? = prefs.getString(KEY_JWT, null)

    fun phoneNumber(): Long? = if (prefs.contains(KEY_PHONE)) prefs.getLong(KEY_PHONE, 0) else null

    fun userId(): Long? = if (prefs.contains(KEY_USER_ID)) prefs.getLong(KEY_USER_ID, 0) else null

    fun clear() {
        prefs.edit().remove(KEY_JWT).remove(KEY_PHONE).remove(KEY_USER_ID).apply()
    }

    private fun migrateLegacyPrefsIfPresent(ctx: Context) {
        val legacy = ctx.getSharedPreferences(LEGACY_PREFS, Context.MODE_PRIVATE)
        val legacyJwt = legacy.getString(KEY_JWT, null) ?: return
        if (prefs.getString(KEY_JWT, null) == null) {
            val phone = if (legacy.contains(KEY_PHONE)) legacy.getLong(KEY_PHONE, 0) else null
            saveJwt(legacyJwt, phone)
            Log.i(TAG, "migrated JWT from legacy prefs into v2 store")
        }
        legacy.edit().clear().apply()
    }

    companion object {
        private const val TAG = "AuthStore"
        private const val PREFS = "baleobala_auth_v2"
        private const val LEGACY_PREFS = "baleobala_auth"
        private const val KEY_JWT = "jwt"
        private const val KEY_PHONE = "phone"
        private const val KEY_USER_ID = "user_id"
    }
}
