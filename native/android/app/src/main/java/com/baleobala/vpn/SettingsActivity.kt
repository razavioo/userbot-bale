package com.baleobala.vpn

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
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
