package com.baleobala.vpn

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.baleobala.vpn.BuildConfig
import com.baleobala.vpn.databinding.ActivitySettingsBinding

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var settings: AppSettings

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        settings = AppSettings(this)

        binding.toolbar.setNavigationOnClickListener { finish() }

        binding.reconnectSwitch.isChecked = settings.reconnectOnLaunch
        binding.logsSwitch.isChecked = settings.showLogs
        binding.autoReconnectSwitch.isChecked = settings.autoReconnectAfterDrop
        binding.dnsInput.setText(settings.primaryDns)

        // coordinatorPeerId is read-only (baked into BuildConfig); only show in debug.
        if (BuildConfig.DEBUG) {
            val coordId = settings.coordinatorPeerId
            binding.exitPeerInput.setText(if (coordId > 0L) coordId.toString() else "")
            binding.exitPeerInput.isEnabled = false
            binding.exitPeerInput.hint = "coordinator peer id (read-only)"
        } else {
            // In release builds, hide the peer-id field entirely.
            binding.exitPeerInput.visibility = android.view.View.GONE
            binding.exitPeerInput.parent?.let {
                if (it is android.view.View) it.visibility = android.view.View.GONE
            }
        }

        binding.saveButton.setOnClickListener {
            settings.carrierMode = BaleVpnService.CARRIER_BALE
            settings.reconnectOnLaunch = binding.reconnectSwitch.isChecked
            settings.showLogs = binding.logsSwitch.isChecked
            settings.autoReconnectAfterDrop = binding.autoReconnectSwitch.isChecked
            settings.primaryDns = binding.dnsInput.text?.toString()?.trim().orEmpty().ifBlank { "1.1.1.1" }
            setResult(RESULT_OK)
            finish()
        }
    }
}
