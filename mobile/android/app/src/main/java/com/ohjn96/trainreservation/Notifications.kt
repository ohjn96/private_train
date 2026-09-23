package com.ohjn96.trainreservation

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

object Notifications {
    /** 상단에 늘 떠 있는 "실행 중" 알림 (조용히) */
    const val CHANNEL_SERVICE = "service"
    /** 예약 성공·결제·중단 (소리·진동) */
    const val CHANNEL_EVENTS = "events"

    const val SERVICE_ID = 1

    /**
     * 상태 알림은 종류마다 번호를 고정한다: 같은 종류의 새 알림이 옛것을 바꿔 끼운다.
     * (프로세스가 다시 떠도 같은 번호라 "멈췄어요" 가 쌓이지 않는다)
     */
    const val ID_LOST = 10
    const val ID_RESUMED = 11
    const val ID_GAVE_UP = 12
    const val ID_STOPPED = 13
    /** 백그라운드에서 다시 시작이 거부됨 → 눌러서 다시 시작 */
    const val ID_RESTART_NEEDED = 14

    /**
     * 결과 알림(예약 성공·결제)은 절대 서로 덮어쓰면 안 된다 ("…까지 결제" 안내가 사라진다).
     * 번호를 디스크에 두고 계속 올린다. 프로세스가 다시 떠도 이어서 센다.
     */
    private const val PREFS = "notifications"
    private const val KEY_NEXT_ID = "next_result_id"
    private const val RESULT_ID_FIRST = 1000

    /** 결과 알림(덮어쓰면 안 되는 것)인가 */
    private fun isResult(kind: String) = kind == "reserved" || kind == "paid" || kind == "pay_failed"

    private fun fixedId(kind: String): Int? = when (kind) {
        "lost" -> ID_LOST
        "resumed" -> ID_RESUMED
        "gave_up" -> ID_GAVE_UP
        "stopped" -> ID_STOPPED
        "restart_needed" -> ID_RESTART_NEEDED
        else -> null
    }

    /** 결과 알림 번호: 이전보다 항상 크다 (넘치면 처음으로). */
    @Synchronized
    private fun nextResultId(context: Context): Int {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val id = prefs.getInt(KEY_NEXT_ID, RESULT_ID_FIRST).coerceAtLeast(RESULT_ID_FIRST)
        val next = if (id >= Int.MAX_VALUE - 1) RESULT_ID_FIRST else id + 1
        prefs.edit().putInt(KEY_NEXT_ID, next).commit()  // 곧 죽을 수도 있으니 동기로
        return id
    }

    /** 알림 번호. 결과 알림·모르는 종류는 겹치지 않는 새 번호, 상태 알림은 고정 번호. */
    fun idFor(context: Context, kind: String): Int =
        if (isResult(kind)) nextResultId(context) else fixedId(kind) ?: nextResultId(context)

    fun createChannels(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_SERVICE, "실행 상태", NotificationManager.IMPORTANCE_LOW).apply {
                description = "예약 매크로가 백그라운드에서 도는 동안 표시됩니다"
                setShowBadge(false)
            }
        )
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_EVENTS, "예약 알림", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "예약 성공, 결제 결과, 매크로 중단"
                enableVibration(true)
            }
        )
    }

    fun openAppIntent(context: Context): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return PendingIntent.getActivity(
            context, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    /** 잠금 화면에 대신 보일 내용 없는 알림 */
    fun publicVersion(context: Context, channel: String, title: String) =
        NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .build()

    fun showEvent(context: Context, kind: String, title: String, body: String) {
        if (kind == "resumed") cancel(context, ID_RESTART_NEEDED)  // 다시 돌기 시작했다
        val notification = NotificationCompat.Builder(context, CHANNEL_EVENTS)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(body.lineSequence().firstOrNull() ?: body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setAutoCancel(true)
            .setContentIntent(openAppIntent(context))
            // 잠금 화면에는 열차·결제 내용 대신 "예약 알림이 있어요" 만
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(publicVersion(context, CHANNEL_EVENTS, "예약 알림이 있어요"))
            .build()
        try {
            NotificationManagerCompat.from(context).notify(idFor(context, kind), notification)
        } catch (e: SecurityException) {
            // 알림 권한이 없으면 조용히 넘어간다 (앱 화면 로그에는 남아 있다)
        }
    }

    /**
     * 시스템이 백그라운드에서 포그라운드 서비스를 다시 띄우지 못하게 했을 때 (Android 12+).
     * 조용히 멈추지 않고, 누르면 앱이 열리고 저장된 작업이 자동으로 이어지도록 알린다.
     */
    fun showRestartNeeded(context: Context) {
        createChannels(context)
        showEvent(
            context, "restart_needed", "예약 찾기가 멈췄어요 — 눌러서 다시 시작",
            "휴대폰이 백그라운드에서 앱을 다시 켜지 못하게 했어요. 이 알림을 누르면 앱이 열리고 하던 예약 찾기를 이어서 해요.\n" +
                "자주 멈추면 앱의 설정 탭에서 배터리 예외를 켜 주세요."
        )
    }

    fun cancel(context: Context, id: Int) {
        NotificationManagerCompat.from(context).cancel(id)
    }
}
