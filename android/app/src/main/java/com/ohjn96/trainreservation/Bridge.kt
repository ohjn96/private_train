package com.ohjn96.trainreservation

import android.content.Context

/**
 * 파이썬(android_main.py)이 부르는 입구. Chaquopy 에서 정적 메서드로 보이도록 @JvmStatic.
 */
object Bridge {
    @Volatile
    var appContext: Context? = null

    /** 매크로가 돌기 시작하거나 멈췄을 때. summary 는 노리는 열차 요약. */
    @JvmStatic
    fun onMacroState(running: Boolean, summary: String) {
        ServerService.instance?.onMacroState(running, summary)
    }

    /** 예약 성공·결제·중단. kind: reserved / paid / pay_failed / stopped */
    @JvmStatic
    fun notifyEvent(kind: String, title: String, body: String) {
        appContext?.let { Notifications.showEvent(it, kind, title, body) }
    }
}
