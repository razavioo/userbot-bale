package com.baleobala.vpn.ui

import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.baleobala.vpn.bale.AuthStore
import com.baleobala.vpn.bale.BaleAuth
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
            val digits = raw.filter { it.isDigit() }
            if (digits.length < 10) {
                setStatus("Phone too short — include country code (e.g. 989121234567)")
                return@setOnClickListener
            }
            val phone = digits.toLong()
            phoneNumber = phone
            setStatus("Sending code to $phone…")
            binding.sendCodeButton.isEnabled = false
            lifecycleScope.launch {
                try {
                    val tx = withContext(Dispatchers.IO) { auth.startPhoneAuth(phone) }
                    setStatus("SMS sent. transaction_hash=${tx.take(12)}…\nEnter the code below.")
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
                } catch (e: GrpcWebError) {
                    setStatus("ValidateCode failed: grpc=${e.grpcStatus} msg=\"${e.grpcMessage}\" http=${e.httpStatus}")
                } catch (e: Throwable) {
                    setStatus("ValidateCode failed: ${e.javaClass.simpleName}: ${e.message}")
                } finally {
                    binding.validateButton.isEnabled = true
                }
            }
        }
    }

    private fun setStatus(s: String) { binding.loginStatus.text = s }
}
