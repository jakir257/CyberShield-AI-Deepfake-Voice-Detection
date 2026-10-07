plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.codephoton.callstream"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.codephoton.callstream"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // debug-signed so `assembleRelease` stays sideloadable; swap in a real key to publish
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    // Android has no built-in WebSocket client (java.net.http is not on the platform)
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
