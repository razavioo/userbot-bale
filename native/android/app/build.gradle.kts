plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.userbot_bale.vpn"
    compileSdk = 35

    signingConfigs {
        create("release") {
            // Populated from environment variables injected by CI (GitHub Actions secret).
            // Local release builds: set these in ~/.gradle/gradle.properties or local.properties.
            val keyStorePath = System.getenv("ANDROID_KEYSTORE_PATH") ?: ""
            val keyStorePass = System.getenv("ANDROID_KEYSTORE_PASSWORD") ?: ""
            val keyAlias    = System.getenv("ANDROID_KEY_ALIAS") ?: "userbot-bale"
            val keyPass     = System.getenv("ANDROID_KEY_PASSWORD") ?: keyStorePass
            if (keyStorePath.isNotEmpty()) {
                storeFile = file(keyStorePath)
                storePassword = keyStorePass
                this.keyAlias = keyAlias
                keyPassword = keyPass
            }
        }
    }

    defaultConfig {
        applicationId = "com.userbot_bale.vpn"
        minSdk = 29
        targetSdk = 35
        versionCode = 4
        // Read from the repo-root VERSION file so Python, Android, and macOS share one source of truth.
        // Falls back to "0.0.0" if the file isn't present (e.g. in a stripped archive build).
        val versionFile = rootProject.file("../../VERSION")
        versionName = if (versionFile.exists()) versionFile.readText().trim() else "0.0.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            val releaseSigning = signingConfigs.getByName("release")
            // Fall back to debug signing only when no keystore is configured (local dev).
            signingConfig = if (System.getenv("ANDROID_KEYSTORE_PATH")?.isNotEmpty() == true)
                releaseSigning else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { viewBinding = true; buildConfig = true }
    packaging {
        resources {
            excludes += setOf(
                "META-INF/DEPENDENCIES",
                "META-INF/LICENSE*",
                "META-INF/NOTICE*",
                "META-INF/AL2.0",
                "META-INF/LGPL2.1",
            )
        }
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.fragment:fragment-ktx:1.6.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.6.2")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.6.2")
    implementation("androidx.localbroadcastmanager:localbroadcastmanager:1.0.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-urlconnection:4.12.0")
    implementation("io.livekit:livekit-android:2.25.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
}
