package com.baleobala.vpn.bale

import android.util.Log
import java.util.UUID

/**
 * Bale phone/SMS auth flow — port of [src/baleobala/bale/auth.py](../../../../../../../../src/baleobala/bale/auth.py).
 *
 * Two-step flow:
 *   1. startPhoneAuth(phone) → SMS sent → returns transaction_hash
 *   2. validateCode(code)    → JWT (via Set-Cookie, body, or follow-up GetJWTToken)
 *
 * One BaleAuth instance covers one end-to-end flow; reuse the same
 * GrpcWebClient so the gateway-side session_id stays consistent.
 */
class BaleAuth(
    private val deviceTitle: String = "baleobala-android",
    deviceHash: ByteArray? = null,
    sessionId: String? = null,
) {
    private val client = GrpcWebClient(sessionId = sessionId)
    private val deviceHash: ByteArray = deviceHash ?: UUID.randomUUID().toString().toByteArray(Charsets.US_ASCII)
    private var lastTx: String? = null

    /** Trigger SMS to [phoneNumber] (digits only, no '+'). Returns transaction_hash. */
    @Throws(GrpcWebError::class)
    fun startPhoneAuth(phoneNumber: Long, sendCodeType: Int = SEND_CODE_BALEONLY): String {
        val req = BaleProtos.encodeStartPhoneAuth(
            phoneNumber = phoneNumber,
            deviceHash = deviceHash,
            deviceTitle = deviceTitle,
            sendCodeType = sendCodeType,
        )
        val resp = client.unary(BaleProtos.AUTH_SERVICE, "StartPhoneAuth", req)
        val tx = BaleProtos.parseTransactionHash(resp.body)
            ?: throw RuntimeException("StartPhoneAuth returned no transaction_hash; body=${resp.body.size}B")
        lastTx = tx
        Log.i(TAG, "StartPhoneAuth OK; transaction_hash=$tx")
        return tx
    }

    /** Submit the SMS code. Returns AuthSession with the JWT on success. */
    @Throws(GrpcWebError::class)
    fun validateCode(code: String, transactionHash: String? = null): AuthSession {
        val tx = transactionHash ?: lastTx
            ?: throw RuntimeException("no transaction_hash — call startPhoneAuth() first")
        val req = BaleProtos.encodeValidateCode(transactionHash = tx, code = code, isJwt = true)
        val resp = client.unary(BaleProtos.AUTH_SERVICE, "ValidateCode", req)
        var jwt = GrpcWebClient.extractAccessToken(resp.setCookies)
        if (jwt == null) jwt = BaleProtos.parseJwt(resp.body)
        if (jwt == null) {
            // Fallback: server gives JWT only via a follow-up GetJWTToken call.
            val userId = BaleProtos.parseUserIdFromAuth(resp.body)
            Log.w(TAG, "No JWT in ValidateCode response; calling GetJWTToken (user_id=$userId)")
            try {
                if (userId != null) client.setUserId(userId)
                val jwtResp = client.unary(BaleProtos.AUTH_SERVICE, "GetJWTToken", BaleProtos.encodeGetJWTToken())
                jwt = BaleProtos.parseJwt(jwtResp.body)
            } catch (e: Throwable) {
                Log.w(TAG, "GetJWTToken failed: ${e.message}")
            }
        }
        if (jwt == null) {
            throw RuntimeException("Could not obtain JWT after ValidateCode + GetJWTToken")
        }
        Log.i(TAG, "Auth OK; JWT length=${jwt.length}")
        return AuthSession(jwt = jwt, responseBody = resp.body)
    }

    companion object {
        private const val TAG = "BaleAuth"
        // Values mined from web.bale.ai (captures/web-js/index.52867891.js):
        //   UNKNOWN=0  DEFAULT=1  BALEONLY=2  SMS=3  CALL=4  EMAIL=5
        //   MISSCALL=6  SETUP_EMAIL_REQUIRED=7  WHATSAPP=8  TELEGRAM=9
        //   USSD=10  FUTURE_AUTH_TOKEN=11  TELEGRAM_GATEWAY=12
        // The server's UNKNOWN default tries TELEGRAM_GATEWAY for Iranian
        // numbers and fails for accounts not registered with Telegram.
        const val SEND_CODE_DEFAULT = 1
        const val SEND_CODE_BALEONLY = 2
        const val SEND_CODE_SMS = 3
    }
}

data class AuthSession(val jwt: String, val responseBody: ByteArray) {
    override fun equals(other: Any?): Boolean = other is AuthSession &&
        jwt == other.jwt && responseBody.contentEquals(other.responseBody)
    override fun hashCode(): Int = 31 * jwt.hashCode() + responseBody.contentHashCode()
}
