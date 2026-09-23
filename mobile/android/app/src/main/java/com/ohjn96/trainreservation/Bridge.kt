package com.ohjn96.trainreservation

import android.app.Activity
import android.content.Context
import java.lang.ref.WeakReference

/**
 * 파이썬(android_main.py)이 부르는 입구. Chaquopy 에서 정적 메서드로 보이도록 @JvmStatic.
 */
object Bridge {
    @Volatile
    var appContext: Context? = null

    /** 지금 화면에 떠 있는 MainActivity (설정 화면을 띄울 때 쓴다). 없으면 null. */
    @Volatile
    var foreground: WeakReference<Activity>? = null

    /** 매크로가 돌기 시작하거나 멈췄을 때. summary 는 노리는 열차 요약. */
    @JvmStatic
    fun onMacroState(running: Boolean, summary: String) {
        ServerService.instance?.onMacroState(running, summary)
    }

    /** 매크로 작업을 암호화해 저장 (프로세스가 죽어도 이어서 돌리려고) */
    @JvmStatic
    fun saveJob(json: String) {
        appContext?.let { SecureStore.saveJob(it, json) }
    }

    /** 매크로가 정상적으로 끝났다: 되살릴 작업을 지운다 */
    @JvmStatic
    fun clearJob() {
        appContext?.let { SecureStore.clearJob(it) }
    }

    /** 파이썬 서버가 실제로 연 포트 (무작위). 화면·헬스체크는 이 포트로 붙는다. */
    @JvmStatic
    fun onServerReady(port: Int) {
        ServerService.port = port
    }

    /** 예약 성공·결제·중단. kind: reserved / paid / pay_failed / stopped / resumed / gave_up */
    @JvmStatic
    fun notifyEvent(kind: String, title: String, body: String) {
        appContext?.let { Notifications.showEvent(it, kind, title, body) }
    }

    /** 배터리 최적화 예외 여부·제조사별 안내 (JSON). 웹 화면의 상태 줄이 쓴다. */
    @JvmStatic
    fun batteryStatus(): String {
        val ctx = appContext ?: return "{\"exempt\":true}"
        return PowerHelp.statusJson(ctx)
    }

    /** 배터리 최적화 예외 요청 창을 다시 띄운다 (첫 안내를 "다시 묻지 않기" 했어도). */
    @JvmStatic
    fun requestBatteryExemption(): Boolean {
        val activity = foreground?.get()
        if (activity != null && !activity.isFinishing) {
            activity.runOnUiThread { PowerHelp.request(activity) }
            return true
        }
        val ctx = appContext ?: return false
        PowerHelp.request(ctx)
        return true
    }
}
