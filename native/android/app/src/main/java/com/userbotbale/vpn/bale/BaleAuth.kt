package com.userbot_bale.vpn.bale

import android.util.Log
import java.util.UUID

/**
 * Bale phone/SMS auth flow — port of [src/userbot-bale/bale/auth.py](../../../../../../../../src/userbot-bale/bale/auth.py).
 *
 * Two-step flow:
 *   1. startPhoneAuth(phone) → SMS sent → returns transaction_hash
 *   2. validateCode(code)    → JWT (via Set-Cookie, body, or follow-up GetJWTToken)
 *
 * One BaleAuth instance covers one end-to-end flow; reuse the same
 * GrpcWebClient so the gateway-side session_id stays consistent.
 */
class BaleAuth(
    private val deviceTitle: String = "userbot-bale-android",
    deviceHash: ByteArray? = null,
    sessionId: String? = null,
) {
    private val client = GrpcWebClient(sessionId = sessionId)
    private val deviceHash: ByteArray = deviceHash ?: UUID.randomUUID().toString().toByteArray(Charsets.US_ASCII)
    private var lastTx: String? = null

    /** Trigger SMS to [phoneNumber] (digits only, no '+'). Returns transaction_hash. */
    @Throws(GrpcWebError::class)
    fun startPhoneAuth(phoneNumber: Long): BaleProtos.StartPhoneAuthResponse {
        val req = BaleProtos.encodeStartPhoneAuth(
            phoneNumber = phoneNumber,
            deviceHash = deviceHash,
            deviceTitle = deviceTitle,
        )
        val resp = client.unary(BaleProtos.AUTH_SERVICE, "StartPhoneAuth", req)
        val parsed = BaleProtos.parseStartPhoneAuth(resp.body)
        if (parsed.transactionHash.isEmpty())
            throw RuntimeException("StartPhoneAuth returned no transaction_hash; body=${resp.body.size}B")
        lastTx = parsed.transactionHash
        Log.i(TAG, "StartPhoneAuth OK; tx=${parsed.transactionHash} channel=${BaleProtos.sendCodeTypeName(parsed.sendCodeTypeChosen)} ussd=${parsed.ussdInstruction}")
        return parsed
    }

    /** Submit the SMS code. Returns AuthSession with the JWT on success. */
    /**
     * Complete SignUp for a new account. Required when ValidateCode succeeded
     * but the response does not include a JWT — Bale's signal for "phone is
     * recognised but no profile exists yet".
     */
    @Throws(GrpcWebError::class)
    fun signUp(name: String, transactionHash: String? = null): AuthSession {
        val tx = transactionHash ?: lastTx
            ?: throw RuntimeException("no transaction_hash — call startPhoneAuth() first")
        val req = BaleProtos.encodeSignUp(transactionHash = tx, name = name)
        val resp = client.unary(BaleProtos.AUTH_SERVICE, "SignUp", req)
        var jwt = GrpcWebClient.extractAccessToken(resp.setCookies)
        var path = "cookie"
        if (jwt == null) {
            jwt = BaleProtos.parseJwt(resp.body)
            if (jwt != null) path = "body-regex"
        }
        // Bale's SignUp may not include a JWT directly — fall back to GetJWTToken
        // (same pattern as ValidateCode, confirmed from web.bale.ai source).
        if (jwt == null) {
            val userId = BaleProtos.parseUserIdFromAuth(resp.body)
            Log.w(TAG, "No JWT in SignUp response; calling GetJWTToken (user_id=$userId)")
            try {
                if (userId != null) client.setUserId(userId)
                val jwtResp = client.unary(BaleProtos.AUTH_SERVICE, "GetJWTToken", BaleProtos.encodeGetJWTToken())
                jwt = BaleProtos.parseJwt(jwtResp.body)
                if (jwt != null) path = "GetJWTToken"
            } catch (e: Throwable) {
                Log.w(TAG, "GetJWTToken after SignUp failed: ${e.message}")
            }
        }
        if (jwt == null) throw RuntimeException(
            "SignUp succeeded but no JWT in response (body=${resp.body.size}B). " +
            "Hex: " + resp.body.take(128).joinToString("") { String.format("%02x", it) }
        )
        Log.i(TAG, "SignUp OK via $path; JWT length=${jwt.length}")
        val userId = BaleProtos.parseUserIdFromAuth(resp.body)
        return AuthSession(jwt = jwt, responseBody = resp.body, userId = userId)
    }

    /** True when ValidateCode succeeded but no JWT was returned (typical of new accounts). */
    fun lastValidateNeedsSignUp(): Boolean = needsSignUp

    private var needsSignUp: Boolean = false

    @Throws(GrpcWebError::class)
    fun validateCode(code: String, transactionHash: String? = null): AuthSession {
        val tx = transactionHash ?: lastTx
            ?: throw RuntimeException("no transaction_hash — call startPhoneAuth() first")
        val req = BaleProtos.encodeValidateCode(transactionHash = tx, code = code, isJwt = true)
        val resp = try {
            client.unary(BaleProtos.AUTH_SERVICE, "ValidateCode", req)
        } catch (e: GrpcWebError) {
            // Bale returns PHONE_NUMBER_UNOCCUPIED when the code was accepted but
            // no account exists for this number yet — same as getting a valid tx
            // with no JWT: the caller must proceed to SignUp with a name.
            if (e.grpcMessage == "PHONE_NUMBER_UNOCCUPIED") {
                needsSignUp = true
                throw NeedsSignUpException(
                    transactionHash = tx,
                    detail = "PHONE_NUMBER_UNOCCUPIED — new account, sign-up required"
                )
            }
            throw e
        }
        var jwt = GrpcWebClient.extractAccessToken(resp.setCookies)
        var path = "cookie"
        if (jwt == null) {
            jwt = BaleProtos.parseJwt(resp.body)
            if (jwt != null) path = "body-regex"
        }
        var getJwtError: String? = null
        var getJwtBodyHex: String? = null
        if (jwt == null) {
            val userId = BaleProtos.parseUserIdFromAuth(resp.body)
            Log.w(TAG, "No JWT in ValidateCode response; calling GetJWTToken (user_id=$userId)")
            try {
                if (userId != null) client.setUserId(userId)
                val jwtResp = client.unary(BaleProtos.AUTH_SERVICE, "GetJWTToken", BaleProtos.encodeGetJWTToken())
                getJwtBodyHex = jwtResp.body.take(128).joinToString("") { String.format("%02x", it) }
                jwt = BaleProtos.parseJwt(jwtResp.body)
                if (jwt != null) path = "GetJWTToken"
            } catch (e: Throwable) {
                getJwtError = "${e.javaClass.simpleName}: ${e.message}"
                Log.w(TAG, "GetJWTToken failed: ${e.message}")
            }
        }
        if (jwt == null) {
            // Most likely cause: phone is recognised but the account does
            // not yet have a profile. Caller must follow up with signUp(name).
            needsSignUp = true
            throw NeedsSignUpException(
                transactionHash = tx,
                detail = "ValidateCode body=${resp.body.size}B, set-cookie=${resp.setCookies.size}, getJwt=${getJwtError ?: "ok"}"
            )
        }
        Log.i(TAG, "Auth OK via $path; JWT length=${jwt.length}")
        needsSignUp = false
        val userId = BaleProtos.parseUserIdFromAuth(resp.body)
        return AuthSession(jwt = jwt, responseBody = resp.body, userId = userId)
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

class NeedsSignUpException(
    val transactionHash: String,
    val detail: String,
) : RuntimeException("Bale account needs sign-up (provide your name).")

data class AuthSession(val jwt: String, val responseBody: ByteArray, val userId: Long? = null) {
    override fun equals(other: Any?): Boolean = other is AuthSession &&
        jwt == other.jwt && responseBody.contentEquals(other.responseBody) && userId == other.userId
    override fun hashCode(): Int =
        31 * (31 * jwt.hashCode() + responseBody.contentHashCode()) + (userId?.hashCode() ?: 0)
}
