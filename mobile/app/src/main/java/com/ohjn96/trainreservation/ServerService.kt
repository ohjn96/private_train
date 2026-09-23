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
        const val PORT = 17650
        const val ACTION_STOP = "com.ohjn96.trainreservation.STOP"
        private const val TAG = "ServerService"

        /** 매크로가 도는 중이라는 표시. 프로세스가 죽었다 살아났을 때 알리려고 디스크에 남긴다. */
        private const val PREFS = "macro"
        private const val KEY_ACTIVE = "active"
        private const val KEY_SUMMARY = "summary"

        @Volatile
        var instance: ServerService? = null
            private set

        private val serverStarted = AtomicBoolean(false)

        fun start(context: Context) {
            val intent = Intent(context, ServerService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }

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
        goForeground()
        warnIfMacroWasLost()
        startPythonServer()
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
            this, "lost", "⚠️ 예약 매크로가 멈췄어요",
            "휴대폰이 메모리를 정리하면서 앱이 다시 시작됐어요. 앱을 열어 다시 로그인하고 매크로를 시작해 주세요." +
                if (summary.isNotEmpty()) "\n대상: $summary" else ""
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            shutdown()
            return START_NOT_STICKY
        }
        goForeground()
        // 시스템이 메모리 때문에 죽였다가 여유가 생기면 다시 띄운다
        return START_STICKY
    }

    override fun onDestroy() {
        releaseLocks()
        instance = null
        super.onDestroy()
    }

    private fun startPythonServer() {
        if (!serverStarted.compareAndSet(false, true)) return
        val filesDir = filesDir.absolutePath
        val token = AppToken.get(this)
        thread(name = "python-server", isDaemon = true) {
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))
                Python.getInstance().getModule("android_main")
                    .callAttr("start", filesDir, PORT, token, BuildConfig.VERSION_NAME, BuildConfig.DEBUG)
            } catch (e: Throwable) {
                Log.e(TAG, "python server crashed", e)
                serverStarted.set(false)
            }
        }
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

    private fun goForeground() {
        val notification = buildServiceNotification()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else 0
        ServiceCompat.startForeground(this, Notifications.SERVICE_ID, notification, type)
    }

    private fun buildServiceNotification() =
        NotificationCompat.Builder(this, Notifications.CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(if (macroRunning) "예약 매크로 실행 중" else "열차 예약 대기 중")
            .setContentText(
                if (macroRunning) macroSummary.ifEmpty { "좌석을 찾는 중입니다" }
                else "앱을 닫아도 켜져 있습니다. 끄려면 [종료]"
            )
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(Notifications.openAppIntent(this))
            .addAction(0, "종료", stopIntent())
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()

    private fun stopIntent(): PendingIntent {
        val intent = Intent(this, ServerService::class.java).setAction(ACTION_STOP)
        return PendingIntent.getService(this, 1, intent, PendingIntent.FLAG_IMMUTABLE)
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
        // 사용자가 직접 끈 것이므로 "멈췄어요" 알림을 띄우지 않게
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().commit()
        releaseLocks()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
        // 파이썬 서버 스레드는 멈출 수 없으므로 프로세스째 끝낸다. 다시 열면 새로 뜬다.
        android.os.Process.killProcess(android.os.Process.myPid())
    }
}
