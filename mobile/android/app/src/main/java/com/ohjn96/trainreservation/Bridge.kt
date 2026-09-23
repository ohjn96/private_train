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
}
