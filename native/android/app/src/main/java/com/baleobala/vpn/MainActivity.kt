package com.baleobala.vpn

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.VpnService
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.baleobala.vpn.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val logBuffer = ArrayDeque<String>()
    private var connected: Boolean = false

    private val vpnPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            startVpnService()
        } else {
            appendLog("permission denied")
        }
    }

    private val stateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val kind = intent.getStringExtra("kind") ?: return
            val value = intent.getStringExtra("value") ?: return
            when (kind) {
                "status" -> {
                    connected = value == "connected"
                    binding.statusText.text = if (connected) getString(R.string.status_connected) else getString(R.string.status_disconnected)
                    binding.toggleButton.text = if (connected) getString(R.string.disconnect) else getString(R.string.connect)
                }
                "log", "error" -> appendLog(value)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.toggleButton.setOnClickListener {
            if (connected) {
                val i = Intent(this, BaleVpnService::class.java).apply { action = BaleVpnService.ACTION_DISCONNECT }
                startService(i)
            } else {
                val prep = VpnService.prepare(this)
                if (prep != null) {
                    vpnPermissionLauncher.launch(prep)
                } else {
                    startVpnService()
                }
            }
        }
    }

    private fun startVpnService() {
        binding.statusText.text = getString(R.string.status_connecting)
        startService(Intent(this, BaleVpnService::class.java))
    }

    override fun onResume() {
        super.onResume()
        LocalBroadcastManager.getInstance(this).registerReceiver(
            stateReceiver, IntentFilter(BaleVpnService.ACTION_STATE)
        )
    }

    override fun onPause() {
        super.onPause()
        LocalBroadcastManager.getInstance(this).unregisterReceiver(stateReceiver)
    }

    private fun appendLog(line: String) {
        logBuffer.addLast(line)
        while (logBuffer.size > 200) logBuffer.removeFirst()
        binding.logText.text = logBuffer.joinToString("\n")
    }
}
