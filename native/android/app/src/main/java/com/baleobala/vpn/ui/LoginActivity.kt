package com.baleobala.vpn.ui

import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.baleobala.vpn.bale.AuthStore
import com.baleobala.vpn.bale.BaleAuth
import com.baleobala.vpn.bale.BaleProtos
import com.baleobala.vpn.bale.NeedsSignUpException
import com.baleobala.vpn.bale.GrpcWebError
import com.baleobala.vpn.databinding.ActivityLoginBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Phone + SMS sign-in flow that drives the same Bale gRPC-Web auth
 * RPCs as the Linux client. On success, persists the JWT via [AuthStore]
 * and finishes — MainActivity then knows to enable the Bale carrier.
 */
class LoginActivity : AppCompatActivity() {

    private lateinit var binding: ActivityLoginBinding
    private val auth = BaleAuth(deviceTitle = "baleobala-android")
    private lateinit var store: AuthStore
    private var phoneNumber: Long? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)
        store = AuthStore(this)

        binding.sendCodeButton.setOnClickListener {
            val raw = binding.phoneInput.text?.toString()?.trim().orEmpty()
            var digits = raw.filter { it.isDigit() }
            // Normalize Iran-local "09xxxxxxxxx" to "989xxxxxxxxx" so Bale's
            // gateway recognises the country code; pass through other formats.
            if (digits.startsWith("0")) digits = digits.trimStart('0')
            if (digits.length == 10 && digits.startsWith("9")) digits = "98$digits"
            if (digits.length < 11) {
                setStatus("Phone too short — include country code (e.g. 989121234567)")
                return@setOnClickListener
            }
            val phone = digits.toLong()
            phoneNumber = phone
            setStatus("Sending code to $phone…")
            binding.sendCodeButton.isEnabled = false
            lifecycleScope.launch {
                try {
                    val r = withContext(Dispatchers.IO) { auth.startPhoneAuth(phone) }
                    val channel = BaleProtos.sendCodeTypeName(r.sendCodeTypeChosen)
                    val sb = StringBuilder()
                    sb.append("Server accepted (tx=${r.transactionHash.take(12)}…).\n")
                    sb.append("Channel server picked: $channel")
                    if (r.codeLength != null) sb.append("  code-length=${r.codeLength}")
                    if (r.waitTimeSec != null) sb.append("  wait=${r.waitTimeSec}s")
                    sb.append("\n")
                    if (r.ussdInstruction != null) {
                        sb.append("⚠ Server wants you to dial USSD: ${r.ussdInstruction}\n")
                        sb.append("Open your phone dialer, dial the code above, copy the OTP it returns, paste below.")
                    } else when (r.sendCodeTypeChosen) {
                        9, 12 -> sb.append("Server is sending via Telegram. Open your Telegram app for the code.")
                        3, 2 -> sb.append("Server says SMS dispatched. If nothing arrives, the account may not exist with this number.")
                        4, 6 -> sb.append("Server is calling/missed-calling you. Look at incoming calls; the number / last digits is the code.")
                        else -> sb.append("Enter the code when it arrives.")
                    }
                    sb.append("\n\nResponse hex (first 128B):\n")
                    sb.append(r.raw.take(128).joinToString("") { String.format("%02x", it) })
                    setStatus(sb.toString())
                    binding.codeLayout.visibility = View.VISIBLE
                    binding.validateButton.visibility = View.VISIBLE
                } catch (e: GrpcWebError) {
                    setStatus("StartPhoneAuth failed: grpc=${e.grpcStatus} msg=\"${e.grpcMessage}\" http=${e.httpStatus}")
                } catch (e: Throwable) {
                    setStatus("StartPhoneAuth failed: ${e.javaClass.simpleName}: ${e.message}")
                } finally {
                    binding.sendCodeButton.isEnabled = true
                }
            }
        }

        binding.validateButton.setOnClickListener {
            val code = binding.codeInput.text?.toString()?.trim().orEmpty().filter { it.isDigit() }
            if (code.length !in 4..8) {
                setStatus("Enter the 5-6 digit code from the SMS")
                return@setOnClickListener
            }
            binding.validateButton.isEnabled = false
            setStatus("Verifying…")
            lifecycleScope.launch {
                try {
                    val session = withContext(Dispatchers.IO) { auth.validateCode(code) }
                    store.saveJwt(session.jwt, phoneNumber)
                    setStatus("Signed in. JWT length=${session.jwt.length}")
                    finish()
                } catch (ns: NeedsSignUpException) {
                    setStatus("This phone has no Bale profile yet. Enter your name and tap Complete sign-up.")
                    binding.nameLayout.visibility = View.VISIBLE
                    binding.signUpButton.visibility = View.VISIBLE
                } catch (e: GrpcWebError) {
                    setStatus("ValidateCode failed: grpc=${e.grpcStatus} msg=\"${e.grpcMessage}\" http=${e.httpStatus}")
                } catch (e: Throwable) {
                    setStatus("ValidateCode failed: ${e.javaClass.simpleName}: ${e.message}")
                } finally {
                    binding.validateButton.isEnabled = true
                }
            }
        }

        binding.signUpButton.setOnClickListener {
            val name = binding.nameInput.text?.toString()?.trim().orEmpty()
            if (name.length < 2) { setStatus("Enter your name (min 2 chars)"); return@setOnClickListener }
            binding.signUpButton.isEnabled = false
            setStatus("Signing up…")
            lifecycleScope.launch {
                try {
                    val session = withContext(Dispatchers.IO) { auth.signUp(name) }
                    store.saveJwt(session.jwt, phoneNumber)
                    setStatus("Signed up. JWT length=${session.jwt.length}")
                    finish()
                } catch (e: GrpcWebError) {
                    setStatus("SignUp failed: grpc=${e.grpcStatus} msg=\"${e.grpcMessage}\" http=${e.httpStatus}")
                } catch (e: Throwable) {
                    setStatus("SignUp failed: ${e.javaClass.simpleName}: ${e.message}")
                } finally {
                    binding.signUpButton.isEnabled = true
                }
            }
        }
    }

    private fun setStatus(s: String) { binding.loginStatus.text = s }
}
