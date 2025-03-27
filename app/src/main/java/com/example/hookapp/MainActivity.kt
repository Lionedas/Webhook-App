package com.example.hookapp

import android.Manifest
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException

class MainActivity : AppCompatActivity() {
    override fun onDestroy() {
        super.onDestroy()
        // Add cleanup code if needed
    }

    override fun onPause() {
        super.onPause()
        // Properly handle activity lifecycle
    }
    companion object {
        private const val PERMISSION_REQUEST_CODE = 1001
        private const val TAG = "FirebaseInit"
    }

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            checkFirebaseInitialization()
            setupFirebaseMessaging()
        } else {
            Toast.makeText(
                this,
                "Notifications disabled - some features may not work",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)

        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }

        checkFirebaseInitialization()
        checkNotificationPermission()
    }

    private fun checkFirebaseInitialization() {
        try {
            val firebaseApps = FirebaseApp.getApps(this)
            Log.d(TAG, "Number of Firebase apps initialized: ${firebaseApps.size}")

            if (firebaseApps.isEmpty()) {
                Log.e(TAG, "Firebase not initialized! Check google-services.json and package name")
                Toast.makeText(this, "Firebase initialization failed", Toast.LENGTH_LONG).show()
            } else {
                for (app in firebaseApps) {
                    Log.d(TAG, "Firebase app initialized: ${app.name}")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Firebase initialization check failed", e)
        }
    }

    private fun checkNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            when {
                ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) == android.content.pm.PackageManager.PERMISSION_GRANTED -> {
                    checkFirebaseInitialization()
                    setupFirebaseMessaging()
                }

                shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS) -> {
                    Toast.makeText(
                        this,
                        "Notifications are required for game alerts",
                        Toast.LENGTH_LONG
                    ).show()
                    requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                }

                else -> {
                    requestPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                }
            }
        } else {
            checkFirebaseInitialization()
            setupFirebaseMessaging()
        }
    }

    private fun setupFirebaseMessaging() {
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (!task.isSuccessful) {
                Log.e(TAG, "FCM token fetch failed", task.exception)
                Toast.makeText(
                    this,
                    "Failed to get FCM token: ${task.exception?.message}",
                    Toast.LENGTH_SHORT
                ).show()
                return@addOnCompleteListener
            }

            val token = task.result
            Log.d(TAG, "FCM Token: $token")
            sendRegistrationToServer(token)
        }

        FirebaseMessaging.getInstance().subscribeToTopic("osrs")
            .addOnCompleteListener { task ->
                val msg = if (task.isSuccessful) {
                    Log.d(TAG, "Subscribed to OSRS topic")
                    "Subscribed to OSRS notifications"
                } else {
                    Log.e(TAG, "OSRS topic subscription failed", task.exception)
                    "Failed to subscribe to OSRS topic"
                }
                Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
            }
    }

    private fun sendRegistrationToServer(token: String) {
        val client = OkHttpClient()
        val json = """
    {
        "token": "$token",
        "platform": "android"
    }
    """.trimIndent()

        // For LOCAL TESTING use one of these:
        // Use your computer's LOCAL IP when testing on a physical device
        val serverUrl = "http://192.168.1.12:5000/register" // Replace with your actual local IP

        try {
            val request = Request.Builder()
                .url(serverUrl)
                .post(json.toRequestBody("application/json".toMediaType()))
                .build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.e(TAG, "Server registration failed: ${e.message}", e)
                runOnUiThread {
                    Toast.makeText(
                        this@MainActivity,
                        "Failed to connect to server: ${e.message}",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }

            override fun onResponse(call: Call, response: Response) {
                val responseBody = response.body?.string()
                Log.d(TAG, "Server response: ${response.code} - $responseBody")

                if (!response.isSuccessful) {
                    Log.e(TAG, "Server error: ${response.code} - $responseBody")
                    runOnUiThread {
                        Toast.makeText(
                            this@MainActivity,
                            "Server error: ${response.code}",
                            Toast.LENGTH_LONG
                        ).show()
                    }
                } else {
                    Log.d(TAG, "Token successfully registered")
                    runOnUiThread {
                        Toast.makeText(
                            this@MainActivity,
                            "Successfully registered with server",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                }
                response.close()
            }
        })

    } catch (e: Exception) {
            Log.e(TAG, "Request creation failed", e)
        }
    }

    private fun isRunningOnEmulator(): Boolean {
        return (Build.FINGERPRINT.startsWith("google/sdk_gphone") ||
                Build.FINGERPRINT.startsWith("unknown") ||
                Build.MODEL.contains("google_sdk") ||
                Build.MODEL.contains("Emulator") ||
                Build.MODEL.contains("Android SDK built for x86") ||
                Build.MANUFACTURER.contains("Genymotion") ||
                (Build.BRAND.startsWith("generic") && Build.DEVICE.startsWith("generic")) ||
                Build.PRODUCT == "google_sdk" ||
                Build.HARDWARE.contains("goldfish") ||
                Build.HARDWARE.contains("ranchu"))
    }
}
