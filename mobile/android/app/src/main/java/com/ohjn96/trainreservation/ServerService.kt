package com.ohjn96.trainreservation

import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * 앱 안의 파이썬 서버(= 데스크톱 앱)를 띄우고, 앱을 내려도 계속 살아 있게 하는 포그라운드 서비스.
 *
 * - 서버는 프로세스당 한 번만 띄운다 (Chaquopy 파이썬은 한 번 시작하면 끌 수 없다).
 * - 예약 매크로가 도는 동안에만 절전 방지 잠금(CPU·Wi-Fi)을 쥔다. 화면이 꺼져도 조회가 계속된다.
 * - 상단 알림의 [종료] 를 누르면 프로세스째 끝낸다.
 */
class ServerService : Service() {

    companion object {
        /**
         * 파이썬 서버의 포트. 0 을 넘겨 운영체제가 빈 포트를 고르게 하고, 실제 포트는
         * 파이썬이 Bridge.onServerReady 로 알려준다 (아직 모르면 0).
         * 고정 포트는 다른 앱이 먼저 차지하고 우리 행세를 할 수 있어 쓰지 않는다.
         */
        @Volatile
        var port: Int = 0
            internal set
        const val ACTION_STOP = "com.ohjn96.trainreservation.STOP"
        const val ACTION_STOP_MACRO = "com.ohjn96.trainreservation.STOP_MACRO"
        private const val TAG = "ServerService"

        /** 매크로가 도는 중이라는 표시. 프로세스가 죽었다 살아났을 때 알리려고 디스크에 남긴다. */
        private const val PREFS = "macro"
        private const val KEY_ACTIVE = "active"
        private const val KEY_SUMMARY = "summary"

        // 헬스체크: 30초마다, 시작 뒤 90초는 기다린다 (파이썬이 뜨는 시간)
        private const val HEALTH_FIRST_DELAY_S = 90L
        private const val HEALTH_INTERVAL_S = 30L
        /** 연속으로 이만큼 응답이 없으면(약 2분) 프로세스를 다시 띄운다 */
        private const val HEALTH_MAX_FAILURES = 4
        /**
         * 매크로가 이 시간(초) 동안 아무 진행(조회·로그)이 없으면 멈춘 것으로 본다.
         * 예약·결제 중(/__health 의 phase)에는 아무리 오래 걸려도 다시 띄우지 않는다.
         */
        private const val STALL_LIMIT_S = 180

        @Volatile
        var instance: ServerService? = null
            private set

        private val serverStarted = AtomicBoolean(false)

        /** 되살릴 작업이 있거나 매크로가 돌던 중이었나 (멈췄다고 알릴 가치가 있나) */
        fun wasWorking(context: Context): Boolean =
            SecureStore.hasJob(context) ||
                context.getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(KEY_ACTIVE, false)

        fun start(context: Context) {
            val intent = Intent(context, ServerService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }

    private var health: ScheduledExecutorService? = null
    private var healthFailures = 0
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var macroRunning = false
    private var macroSummary = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        Bridge.appContext = applicationContext
        Notifications.createChannels(this)
        if (!goForeground()) {
            // 시스템이 백그라운드에서 다시 띄울 때 포그라운드 시작이 거부될 수 있다
            // (Android 12+). 죽지는 않되, 조용히 멈추지도 않는다: 하던 작업이 있었다면
            // "눌러서 다시 시작" 알림을 띄운다. 누르면 앱이 열리고 저장된 작업이 이어진다.
            onForegroundRefused()
            return
        }
        Notifications.cancel(this, Notifications.ID_RESTART_NEEDED)
        startPythonServer()
        startHealthChecks()
    }

    /** 저장된 작업을 읽는다 (Keystore 복호화라 메인 스레드에선 부르지 않는다). */
    private fun loadJobOrWarn(): String? {
        val job = SecureStore.loadJob(this)
        if (job != null) {
            // 돌던 매크로가 있었다 (프로세스가 죽었거나 폰을 재부팅함): 이어서 돌린다.
            // "다시 시작했어요" 알림은 파이썬이 로그인하고 매크로를 실제로 띄운 뒤에 보낸다.
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().apply()
        } else {
            warnIfMacroWasLost()
        }
        return job
    }

    /**
     * 메모리가 부족하면 안드로이드가 앱 프로세스를 죽였다가 서비스만 다시 띄운다(START_STICKY).
     * 그때 매크로와 로그인은 메모리와 함께 사라지므로, 돌던 중이었다면 멈췄다고 꼭 알린다.
     * (알리지 않으면 사용자는 계속 찾고 있다고 믿게 된다)
     */
    private fun warnIfMacroWasLost() {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_ACTIVE, false)) return
        val summary = prefs.getString(KEY_SUMMARY, "").orEmpty()
        prefs.edit().clear().apply()
        Notifications.showEvent(
            this, "lost", "예약 매크로가 멈췄어요",
            "휴대폰이 메모리를 정리하면서 앱이 다시 시작됐어요. 앱을 열어 다시 로그인하고 매크로를 시작해 주세요." +
                if (summary.isNotEmpty()) "\n대상: $summary" else ""
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                shutdown()
                return START_NOT_STICKY
            }
            ACTION_STOP_MACRO -> {
                // 결제 중일 수도 있으니 프로세스를 죽이지 않고 매크로만 멈추게 한다
                thread(name = "stop-macro", isDaemon = true) {
                    try {
                        Python.getInstance().getModule("android_main").callAttr("stop_macro")
                    } catch (e: Throwable) {
                        Log.e(TAG, "stop_macro failed", e)
                    }
                }
            }
        }
        if (!goForeground()) {
            onForegroundRefused()
            return START_NOT_STICKY
        }
        // 시스템이 메모리 때문에 죽였다가 여유가 생기면 다시 띄운다
        return START_STICKY
    }

    /** 포그라운드 시작이 거부됐다. 멈추되, 이어서 돌릴 작업이 있으면 사용자에게 알린다. */
    private fun onForegroundRefused() {
        if (wasWorking(this)) Notifications.showRestartNeeded(this)
        stopSelf()
    }

    override fun onDestroy() {
        health?.shutdownNow()
        releaseLocks()
        instance = null
        super.onDestroy()
    }

    private fun startPythonServer() {
        if (!serverStarted.compareAndSet(false, true)) return
        val filesDir = filesDir.absolutePath
        // 작업 복호화·토큰·파이썬 초기화는 모두 이 스레드에서 (메인 스레드가 버벅이지 않게)
        thread(name = "python-server", isDaemon = true) {
            try {
                val token = AppToken.get(this)
                val job = loadJobOrWarn()
                if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))
                val module = Python.getInstance().getModule("android_main")
                // 자동 재개는 서버가 준비되길 기다렸다가 따로 돈다 (start 는 돌아오지 않는다)
                if (job != null) module.callAttr("resume_job", job)
                // 포트 0 = 무작위. 실제 포트는 Bridge.onServerReady 로 온다
                module.callAttr("start", filesDir, 0, token, BuildConfig.VERSION_NAME, BuildConfig.DEBUG)
            } catch (e: Throwable) {
                Log.e(TAG, "python server crashed", e)
                serverStarted.set(false)
            }
        }
    }

    /**
     * 헬스체크. 서버가 계속 응답하지 않거나 매크로가 오래 멈춰 있으면 프로세스를 다시 띄운다.
     * 다시 뜨면 저장된 작업으로 자동 재개되므로, 사용자가 앱을 껐다 켤 필요가 없다.
     */
    private fun startHealthChecks() {
        if (health != null) return
        health = Executors.newSingleThreadScheduledExecutor().also {
            it.scheduleWithFixedDelay(
                ::checkHealth, HEALTH_FIRST_DELAY_S, HEALTH_INTERVAL_S, TimeUnit.SECONDS
            )
        }
    }

    private fun checkHealth() {
        val token = AppToken.get(this)
        val port = ServerService.port
        val status = try {
            // 토큰을 싣기 전에 그 포트의 서버가 우리 것인지 확인한다
            if (!LocalServer.verify(port, token)) throw IllegalStateException("hello failed")
            val conn = URL("${LocalServer.baseUrl(port)}/__health").openConnection() as HttpURLConnection
            conn.connectTimeout = 3000
            conn.readTimeout = 5000
            conn.setRequestProperty("Cookie", "app_token=$token")
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            conn.disconnect()
            JSONObject(body)
        } catch (e: Exception) {
            null
        }

        if (status == null) {
            healthFailures++
            Log.w(TAG, "health check failed ($healthFailures/$HEALTH_MAX_FAILURES)")
            if (healthFailures >= HEALTH_MAX_FAILURES) restartProcess("서버가 응답하지 않음")
            return
        }
        healthFailures = 0
        val stalled = status.optInt("stalled_seconds", 0)
        // 예약·결제 중이면 느려도 절대 죽이지 않는다 (결제 도중에 끊기면 좌석만 잡고 결제를 못 한다)
        val phase = if (status.isNull("phase")) "" else status.optString("phase", "")
        if (status.optBoolean("macro_running") && stalled >= STALL_LIMIT_S) {
            if (phase.isNotEmpty()) {
                Log.w(TAG, "macro quiet for ${stalled}s but in '$phase' phase: not restarting")
                return
            }
            restartProcess("매크로가 ${stalled}초 동안 멈춤")
        }
    }

    /** 프로세스를 끝낸다. START_STICKY 라 시스템이 곧 다시 띄우고, 저장된 작업이 이어진다. */
    private fun restartProcess(reason: String) {
        Log.w(TAG, "restarting process: $reason")
        releaseLocks()
        android.os.Process.killProcess(android.os.Process.myPid())
    }

    /** 파이썬이 부른다 (Bridge.onMacroState). 어느 스레드에서든 올 수 있다. */
    @Synchronized
    fun onMacroState(running: Boolean, summary: String) {
        macroRunning = running
        macroSummary = summary
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().apply {
            if (running) putBoolean(KEY_ACTIVE, true).putString(KEY_SUMMARY, summary) else clear()
        }.commit()  // 곧바로 죽을 수도 있으니 동기로 쓴다
        if (running) acquireLocks() else releaseLocks()
        goForeground()
    }

    /** 알림 권한을 방금 받았다: 권한 없을 때 올려 보이지 않던 상단 알림을 다시 올린다. */
    fun refreshNotification() {
        goForeground()
    }

    /** 상단 알림을 띄우고 포그라운드로. 시스템이 거부하면 false. */
    private fun goForeground(): Boolean {
        val notification = buildServiceNotification()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else 0
        return try {
            ServiceCompat.startForeground(this, Notifications.SERVICE_ID, notification, type)
            true
        } catch (e: Exception) {
            Log.e(TAG, "startForeground refused", e)
            false
        }
    }

    private fun buildServiceNotification() =
        NotificationCompat.Builder(this, Notifications.CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(if (macroRunning) "예약 매크로 실행 중" else "열차 예약 대기 중")
            .setContentText(
                if (macroRunning) macroSummary.ifEmpty { "좌석을 찾는 중입니다" }
                else "앱을 닫아도 켜져 있어요. 끄려면 [종료]"
            )
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(Notifications.openAppIntent(this))
            // 매크로가 도는 중엔 [매크로 중단] (결제 도중에 프로세스를 죽이지 않게), 쉴 땐 [종료]
            .addAction(0, if (macroRunning) "매크로 중단" else "종료", stopIntent())
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            // 잠금 화면에는 노리는 열차 대신 일반 문구만
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(
                Notifications.publicVersion(
                    this, Notifications.CHANNEL_SERVICE,
                    if (macroRunning) "예약 매크로 실행 중" else "열차 예약 대기 중"
                )
            )
            .build()

    private fun stopIntent(): PendingIntent {
        val action = if (macroRunning) ACTION_STOP_MACRO else ACTION_STOP
        val intent = Intent(this, ServerService::class.java).setAction(action)
        return PendingIntent.getService(
            this, if (macroRunning) 2 else 1, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
    }

    private fun acquireLocks() {
        if (wakeLock?.isHeld != true) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "TrainReservation:macro").apply {
                setReferenceCounted(false)
                acquire()
            }
        }
        if (wifiLock?.isHeld != true) {
            val wm = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            // LOW_LATENCY 는 앱이 화면에 떠 있을 때만 효과가 있다. 우리는 백그라운드가 목적.
            @Suppress("DEPRECATION")
            wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "TrainReservation:macro").apply {
                setReferenceCounted(false)
                acquire()
            }
        }
    }

    private fun releaseLocks() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wifiLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        wifiLock = null
    }

    private fun shutdown() {
        // 사용자가 직접 끈 것이므로 "멈췄어요" 알림도, 자동 재개도 하지 않게
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().commit()
        SecureStore.clearJob(this)
        releaseLocks()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
        // 파이썬 서버 스레드는 멈출 수 없으므로 프로세스째 끝낸다. 다시 열면 새로 뜬다.
        android.os.Process.killProcess(android.os.Process.myPid())
    }
}
