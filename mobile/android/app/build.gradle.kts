plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// 저장소 루트 (이 프로젝트는 mobile/android/). 파이썬 코드(webui/, core/, korail2/)와
// VERSION, 폰 공통 런타임(mobile/shared/)을 여기서 가져온다.
val repoRoot: File = rootProject.projectDir.parentFile.parentFile
val appVersion: String = File(repoRoot, "VERSION").readText().trim()

// 업데이트 설치가 되려면 버전이 올라갈 때마다 커져야 한다. 미리보기 버전도 순서가 맞게:
//   3.0.0-alpha.N -> 3000000+N, -beta.N -> 3000030+N, -rc.N -> 3000060+N, 3.0.0 -> 3000099
//   (예전 2.3.3 = 20303 보다 항상 크다)
val appVersionCode: Int = run {
    val m = Regex("""(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?""").matchEntire(appVersion)
        ?: error("VERSION 형식이 이상합니다: $appVersion (예: 3.0.0 또는 3.0.0-beta.0)")
    val (major, minor, patch, stage, n) = m.destructured
    val pre = when (stage) {
        "" -> 99
        else -> mapOf("alpha" to 0, "beta" to 30, "rc" to 60).getValue(stage) + minOf(n.toInt(), 29)
    }
    major.toInt() * 1_000_000 + minor.toInt() * 10_000 + patch.toInt() * 100 + pre
}

// 릴리스 서명 키. CI 에선 시크릿으로, 로컬에선 환경변수로 넘긴다.
// 없으면 디버그 키로 서명한다 (설치는 되지만, 키가 바뀌면 지우고 다시 깔아야 한다).
val keystorePath: String? = System.getenv("ANDROID_KEYSTORE_PATH")

android {
    namespace = "com.ohjn96.trainreservation"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.ohjn96.trainreservation"
        minSdk = 26
        targetSdk = 35
        versionCode = appVersionCode
        versionName = appVersion

        ndk {
            // 요즘 폰(arm64) + 에뮬레이터(x86_64)
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
    }
}

// 파이썬 코드는 복사본을 두지 않고, 빌드할 때 저장소 루트(공통 모듈)에서 가져온다.
val pythonSrc = layout.buildDirectory.dir("python-src")
val syncPythonSources by tasks.registering(Sync::class) {
    from(repoRoot) {
        include("webui/**", "core/**", "korail2/**")
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    from(File(repoRoot, "mobile/shared")) {
        include("*.py")
    }
    into(pythonSrc)
}
tasks.named("preBuild") { dependsOn(syncPythonSources) }
// Chaquopy 가 파이썬 소스를 합치는 작업도 복사가 끝난 뒤에 돌도록
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }
    .configureEach { dependsOn(syncPythonSources) }

chaquopy {
    defaultConfig {
        version = "3.12"
        System.getenv("CHAQUOPY_BUILD_PYTHON")?.let { buildPython(it) }
        pip {
            install("-r", File(rootProject.projectDir, "requirements.txt").path)
        }
        // Flask 가 templates/static 을 파일로 읽으므로 실제 파일로 풀어 둔다
        extractPackages("webui")
    }
    sourceSets {
        getByName("main") {
            srcDir(pythonSrc.get().asFile)
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
}
