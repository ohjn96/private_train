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
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
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
        const val REQ_NOTIFICATIONS = 1
    }

    private lateinit var webView: WebView
    private lateinit var content: FrameLayout
    private lateinit var loading: View
    private lateinit var loadingText: TextView
    /** 확인을 마친 서버 주소 (http://127.0.0.1:<포트>). 확인 전에는 null. */
    @Volatile
    private var baseUrl: String? = null
    @Volatile
    private var serverPort: Int = 0
    private val main = Handler(Looper.getMainLooper())

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 최근 앱 목록 미리보기·화면 녹화에 예약 정보·카드 입력칸이 찍히지 않게
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
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
        CookieManager.getInstance().setAcceptCookie(true)

        val asking = requestNotificationPermission()
        ServerService.start(this)
        waitForServerThenLoad(token)
        // 알림 권한 창과 겹치지 않게: 묻는 중이면 답을 받은 뒤(onRequestPermissionsResult)에 안내한다
        if (!asking) main.postDelayed({ askBatteryExemptionIfNeeded() }, 1500)
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
                    if (url.host == "127.0.0.1" || url.host == "localhost") {
                        // 우리 서버(확인한 포트)만 화면 안에서 연다. 같은 호스트의 다른 포트는
                        // 다른 앱일 수 있고, 쿠키는 포트를 가리지 않아 토큰이 그쪽으로 간다.
                        return !(url.scheme == "http" && url.host == "127.0.0.1" &&
                            serverPort != 0 && url.port == serverPort)
                    }
                    // 바깥 링크는 브라우저로
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, url))
                    } catch (e: Exception) {
                        Log.w("MainActivity", "no app for $url")
                    }
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
                    baseUrl?.let { webView.loadUrl("$it/") }
                    return true
                }
            }
            visibility = View.INVISIBLE
        }

    override fun onResume() {
        super.onResume()
        Bridge.foreground = java.lang.ref.WeakReference(this)
        // 서비스가 백그라운드 재시작을 거부당해 멈춰 있었다면("눌러서 다시 시작" 알림) 여기서
        // 다시 띄운다. 저장된 작업이 있으면 ServerService 가 자동으로 이어서 돌린다.
        if (ServerService.instance == null) ServerService.start(this)
        webView.onResume()
        warnIfNotificationsOff()
    }

    override fun onPause() {
        // 화면이 안 보일 때는 웹뷰를 쉬게 한다 (매크로는 서비스에서 계속 돈다).
        // 돌아오면 화면이 상태를 다시 받아 온다 (visibilitychange).
        if (Bridge.foreground?.get() === this) Bridge.foreground = null
        webView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    private var notificationWarned = false
    /** 알림 권한 창이 떠 있다 (답을 받기 전). 이 동안엔 "알림이 꺼져 있어요" 를 띄우지 않는다. */
    private var permissionPending = false

    /** 알림이 꺼져 있으면 예약 성공을 놓치므로 한 번 안내한다 (앱을 열 때마다 최대 한 번). */
    private fun warnIfNotificationsOff() {
        if (permissionPending) return  // 첫 onResume 은 권한 창의 답보다 먼저 온다
        if (notificationWarned || NotificationManagerCompat.from(this).areNotificationsEnabled()) return
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
        // 화면은 한 페이지 안에서 탭·단계(#/run, #/result …)를 오간다. 웹이 한 단계 되돌렸으면
        // (window.__appBack() == true) 그걸로 끝, 첫 화면(조회)이면 앱을 뒤로 보낸다.
        webView.evaluateJavascript("(window.__appBack && window.__appBack()) === true") { handled ->
            if (handled == "true") return@evaluateJavascript
            val path = Uri.parse(webView.url ?: "").path ?: "/"
            val history = webView.copyBackForwardList()
            val previous = if (history.currentIndex > 0) {
                Uri.parse(history.getItemAtIndex(history.currentIndex - 1).url).path
            } else null
            // 로그인 화면으로는 되돌아가지 않는다 (로그인 실패 뒤 뒤로 가기가 앞의 로그인 화면을 다시 띄웠다)
            if (path != "/" && path != "/login" && previous != "/login" && webView.canGoBack()) {
                webView.goBack()  // 다른 페이지에서 돌아올 때
            } else {
                // 끄지 않고 뒤로 보낸다. 매크로는 서비스에서 계속 돈다.
                moveTaskToBack(true)
            }
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
                val port = ServerService.port
                // 포트를 알고, 그 서버가 토큰을 안다고 증명한 뒤에만 쿠키(토큰)를 싣는다
                if (port != 0 && LocalServer.verify(port, token) && ping(port, token)) {
                    val url = LocalServer.baseUrl(port)
                    main.post {
                        serverPort = port
                        baseUrl = url
                        CookieManager.getInstance().apply {
                            // HttpOnly: 화면의 스크립트가 토큰을 읽지 못하게
                            setCookie(url, "app_token=$token; Path=/; SameSite=Strict; HttpOnly")
                            flush()
                        }
                        webView.loadUrl("$url/")
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

    private fun ping(port: Int, token: String): Boolean = try {
        val conn = URL("${LocalServer.baseUrl(port)}/manifest.webmanifest").openConnection() as HttpURLConnection
        conn.connectTimeout = 1000
        conn.readTimeout = 2000
        conn.setRequestProperty("Cookie", "app_token=$token")
        val ok = conn.responseCode == 200
        conn.disconnect()
        ok
    } catch (e: Exception) {
        false
    }

    /** 알림 권한을 묻는다. 창을 띄웠으면 true (답은 onRequestPermissionsResult 로 온다). */
    private fun requestNotificationPermission(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            getSharedPreferences("app", MODE_PRIVATE).edit().putBoolean("notif_asked", true).apply()
            permissionPending = true
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFICATIONS)
            return true
        }
        return false
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_NOTIFICATIONS) return
        permissionPending = false
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            // 권한이 없을 때 띄운 상단 "실행 중" 알림은 보이지 않았다: 권한을 받았으니 다시 올린다
            ServerService.instance?.refreshNotification() ?: ServerService.start(this)
        }
        main.postDelayed({ askBatteryExemptionIfNeeded() }, 500)
    }

    /**
     * 배터리 최적화에서 빼 달라고 안내한다. 이게 켜져 있으면 제조사(특히 삼성·샤오미)가
     * 화면이 꺼진 뒤 몇 시간 안에 매크로를 죽일 수 있다.
     * 설치·업데이트마다 한 번만 묻는다. 그 뒤로는 설정·진행 화면의 상태 줄에서 다시 요청한다.
     */
    private fun askBatteryExemptionIfNeeded() {
        if (isFinishing || PowerHelp.isExempt(this)) return
        val prefs = getSharedPreferences("app", MODE_PRIVATE)
        if (prefs.getInt("battery_asked_version", 0) == BuildConfig.VERSION_CODE) return
        prefs.edit().putInt("battery_asked_version", BuildConfig.VERSION_CODE).apply()

        val tip = PowerHelp.makerTip()
        AlertDialog.Builder(this)
            .setTitle("백그라운드에서 계속 돌리려면")
            .setMessage(
                "화면을 끄거나 앱을 닫아도 예약 매크로가 계속 돌도록 배터리 최적화에서 이 앱을 빼 주세요." +
                    (if (tip.isNotEmpty()) "\n\n추가로 $tip" else "") +
                    "\n\n다시 묻지 않아요. 나중에 설정 탭에서 켤 수 있어요."
            )
            .setPositiveButton("설정 열기") { _, _ -> PowerHelp.request(this) }
            .setNegativeButton("나중에", null)
            .show()
    }
}
