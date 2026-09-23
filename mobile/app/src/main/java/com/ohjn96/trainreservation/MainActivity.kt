package com.ohjn96.trainreservation

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.util.Log
import android.webkit.CookieManager
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import java.net.HttpURLConnection
import java.net.URL
import androidx.core.app.NotificationManagerCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import kotlin.concurrent.thread

/**
 * 화면 = 앱 안의 파이썬 서버(127.0.0.1)를 보여주는 WebView.
 * 매크로는 ServerService 에서 돌기 때문에 이 화면을 닫아도 계속된다.
 */
class MainActivity : Activity() {

    private companion object {
        /** 웹 화면의 바탕색 (app/templates/base.html 의 rail.ground) */
        const val GROUND = "#F6F4F0"
    }

    private lateinit var webView: WebView
    private lateinit var content: FrameLayout
    private lateinit var loading: View
    private lateinit var loadingText: TextView
    private val baseUrl = "http://127.0.0.1:${ServerService.PORT}"
    private val main = Handler(Looper.getMainLooper())

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        @Suppress("DEPRECATION")  // Android 15+ 는 무시하고 아래 여백 색을 쓴다
        window.statusBarColor = Color.parseColor(GROUND)
        // 밝은 바탕이므로 상태바 아이콘을 어둡게
        WindowCompat.getInsetsController(window, window.decorView).isAppearanceLightStatusBars = true

        webView = createWebView()
        loading = buildLoadingView()

        content = FrameLayout(this).apply {
            setBackgroundColor(Color.WHITE)
            addView(webView, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            addView(loading, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.parseColor(GROUND))  // 상태바 자리 (웹 화면 바탕과 같은 색)
            addView(content, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        // Android 15+ 는 화면이 상태바·내비게이션바 밑까지 그려진다. 그만큼 비켜 준다.
        // (키보드가 올라오면 그 높이만큼도 비켜서 입력칸이 가려지지 않게)
        ViewCompat.setOnApplyWindowInsetsListener(root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            root.setPadding(bars.left, bars.top, bars.right, 0)
            content.setPadding(0, 0, 0, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        setContentView(root)

        val token = AppToken.get(this)
        CookieManager.getInstance().apply {
            setAcceptCookie(true)
            // HttpOnly: 화면의 스크립트가 토큰을 읽지 못하게
            setCookie(baseUrl, "android_token=$token; Path=/; SameSite=Strict; HttpOnly")
            flush()
        }

        requestNotificationPermission()
        ServerService.start(this)
        waitForServerThenLoad(token)
        main.postDelayed({ askBatteryExemptionIfNeeded() }, 1500)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun createWebView(): WebView =
        WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true          // 호출 간격 등 화면 설정 기억
            settings.mediaPlaybackRequiresUserGesture = false  // 예약 성공 알림음
            settings.setSupportZoom(false)
            webChromeClient = WebChromeClient()        // alert() / confirm()
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val url = request.url
                    if (url.host == "127.0.0.1") return false
                    // 바깥 링크는 브라우저로
                    startActivity(Intent(Intent.ACTION_VIEW, url))
                    return true
                }

                // 화면(렌더러) 프로세스가 죽어도 앱은 살린다. 기본 동작은 앱 프로세스째
                // 종료라, 같은 프로세스에서 도는 매크로까지 끊긴다.
                override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                    Log.w("MainActivity", "WebView renderer gone (crash=${detail.didCrash()})")
                    content.removeView(webView)
                    webView.destroy()
                    webView = createWebView().apply { visibility = View.VISIBLE }
                    content.addView(webView, 0, ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
                    webView.loadUrl("$baseUrl/")
                    return true
                }
            }
            visibility = View.INVISIBLE
        }

    override fun onResume() {
        super.onResume()
        webView.onResume()
        warnIfNotificationsOff()
    }

    override fun onPause() {
        // 화면이 안 보일 때는 웹뷰를 쉬게 한다 (매크로는 서비스에서 계속 돈다).
        // 돌아오면 화면이 상태를 다시 받아 온다 (visibilitychange).
        webView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    private var notificationWarned = false

    /** 알림이 꺼져 있으면 예약 성공을 놓치므로 한 번 안내한다 (앱을 열 때마다 최대 한 번). */
    private fun warnIfNotificationsOff() {
        if (notificationWarned || NotificationManagerCompat.from(this).areNotificationsEnabled()) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED &&
            !getSharedPreferences("app", MODE_PRIVATE).getBoolean("notif_asked", false)
        ) {
            return  // 아직 권한을 물어보는 중
        }
        notificationWarned = true
        AlertDialog.Builder(this)
            .setTitle("알림이 꺼져 있어요")
            .setMessage("좌석을 잡아도 알려드릴 수 없어요. 결제 기한을 놓치지 않도록 알림을 켜 주세요.")
            .setPositiveButton("알림 켜기") { _, _ ->
                startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                )
            }
            .setNegativeButton("나중에", null)
            .show()
    }

    @Deprecated("Activity 기본 뒤로가기를 WebView 뒤로가기로 바꾼다")
    override fun onBackPressed() {
        // 첫 화면(검색)에서는 같은 화면 기록을 거슬러 가지 않고 바로 앱을 뒤로 보낸다
        val path = Uri.parse(webView.url ?: "").path ?: "/"
        if (path != "/" && webView.canGoBack()) {
            webView.goBack()
        } else {
            // 끄지 않고 뒤로 보낸다. 매크로는 서비스에서 계속 돈다.
            moveTaskToBack(true)
        }
    }

    private fun buildLoadingView(): View = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        gravity = android.view.Gravity.CENTER
        setBackgroundColor(Color.parseColor(GROUND))
        addView(ProgressBar(this@MainActivity))
        loadingText = TextView(this@MainActivity).apply {
            text = "준비 중..."
            setPadding(0, 32, 0, 0)
            setTextColor(Color.parseColor("#6B7280"))
        }
        addView(loadingText)
    }

    /** 파이썬 서버가 뜰 때까지(첫 실행은 파일을 푸느라 10초 넘게 걸리기도 한다) 기다린다. */
    private fun waitForServerThenLoad(token: String) {
        thread(name = "wait-server", isDaemon = true) {
            val deadline = System.currentTimeMillis() + 90_000
            while (System.currentTimeMillis() < deadline) {
                if (ping(token)) {
                    main.post {
                        webView.loadUrl("$baseUrl/")
                        webView.visibility = View.VISIBLE
                        loading.visibility = View.GONE
                    }
                    return@thread
                }
                Thread.sleep(500)
            }
            main.post { loadingText.text = "서버를 시작하지 못했습니다. 앱을 완전히 종료한 뒤 다시 열어 주세요." }
        }
    }

    private fun ping(token: String): Boolean = try {
        val conn = URL("$baseUrl/manifest.webmanifest").openConnection() as HttpURLConnection
        conn.connectTimeout = 1000
        conn.readTimeout = 2000
        conn.setRequestProperty("Cookie", "android_token=$token")
        val ok = conn.responseCode == 200
        conn.disconnect()
        ok
    } catch (e: Exception) {
        false
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            getSharedPreferences("app", MODE_PRIVATE).edit().putBoolean("notif_asked", true).apply()
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    /**
     * 배터리 최적화에서 빼 달라고 한 번 안내한다. 이게 켜져 있으면 제조사(특히 삼성·샤오미)가
     * 화면이 꺼진 뒤 몇 시간 안에 매크로를 죽일 수 있다.
     */
    private fun askBatteryExemptionIfNeeded() {
        val pm = getSystemService(PowerManager::class.java)
        if (pm.isIgnoringBatteryOptimizations(packageName)) return
        val prefs = getSharedPreferences("app", MODE_PRIVATE)
        if (prefs.getBoolean("battery_dont_ask", false)) return

        AlertDialog.Builder(this)
            .setTitle("백그라운드에서 계속 돌리려면")
            .setMessage(
                "화면을 끄거나 앱을 닫아도 예약 매크로가 계속 돌도록 배터리 최적화에서 이 앱을 빼 주세요.\n\n" +
                    "삼성폰은 추가로: 설정 → 배터리 → 백그라운드 사용 제한 → '절전 예외 앱'에 추가"
            )
            .setPositiveButton("설정 열기") { _, _ ->
                @SuppressLint("BatteryLife")
                val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.parse("package:$packageName"))
                try {
                    startActivity(intent)
                } catch (e: Exception) {
                    startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                }
            }
            .setNegativeButton("나중에", null)
            .setNeutralButton("다시 묻지 않기") { _, _ ->
                prefs.edit().putBoolean("battery_dont_ask", true).apply()
            }
            .show()
    }
}
