package com.baleobala.vpn.bale

import android.content.Context

/**
 * Tiny persistence for the JWT and the phone number that obtained it.
 * Plain SharedPreferences for v0; production should migrate to
 * EncryptedSharedPreferences from androidx.security.
 */
class AuthStore(ctx: Context) {
    private val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun saveJwt(jwt: String, phoneNumber: Long? = null) {
        prefs.edit()
            .putString(KEY_JWT, jwt)
            .apply { if (phoneNumber != null) putLong(KEY_PHONE, phoneNumber) }
            .apply()
    }

    fun jwt(): String? = prefs.getString(KEY_JWT, null)

    fun phoneNumber(): Long? = if (prefs.contains(KEY_PHONE)) prefs.getLong(KEY_PHONE, 0) else null

    fun clear() {
        prefs.edit().remove(KEY_JWT).remove(KEY_PHONE).apply()
    }

    companion object {
        private const val PREFS = "baleobala_auth"
        private const val KEY_JWT = "jwt"
        private const val KEY_PHONE = "phone"
    }
}
