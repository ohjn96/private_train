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
    private var nextEventId = 100

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
            NotificationManagerCompat.from(context).notify(nextEventId++, notification)
        } catch (e: SecurityException) {
            // 알림 권한이 없으면 조용히 넘어간다 (앱 화면 로그에는 남아 있다)
        }
    }
}
