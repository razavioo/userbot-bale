# Baleobala VPN — ProGuard / R8 rules

# Keep LiveKit SDK public API (uses reflection internally)
-keep class io.livekit.** { *; }
-dontwarn io.livekit.**

# Keep OkHttp internals referenced via reflection
-keep class okhttp3.internal.** { *; }
-dontwarn okhttp3.**
-dontwarn okio.**

# Keep Kotlin coroutines
-keepnames class kotlinx.coroutines.** { *; }
-dontwarn kotlinx.coroutines.**

# Keep BuildConfig so version strings survive minification
-keep class com.baleobala.vpn.BuildConfig { *; }

# Keep VPN service entry points (referenced from AndroidManifest)
-keep class com.baleobala.vpn.BaleVpnService { *; }
-keep class com.baleobala.vpn.MainActivity { *; }
