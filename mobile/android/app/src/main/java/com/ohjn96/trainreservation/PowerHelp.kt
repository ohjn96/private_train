package com.ohjn96.trainreservation

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import org.json.JSONObject

/**
 * 배터리 최적화 예외. 이게 꺼져 있으면 제조사(특히 삼성·샤오미)가 화면이 꺼진 뒤
 * 매크로를 죽이거나, 죽은 뒤 백그라운드에서 다시 켜지 못하게 한다.
 * 첫 실행 안내(MainActivity)와 웹 화면의 상태 줄(/__app/battery → Bridge)이 같이 쓴다.
 */
object PowerHelp {
    private const val TAG = "PowerHelp"

    fun isExempt(context: Context): Boolean {
        val pm = context.getSystemService(PowerManager::class.java) ?: return true
        return pm.isIgnoringBatteryOptimizations(context.packageName)
    }

    /** 제조사 이름 (소문자). samsung / xiaomi / ... */
    fun maker(): String = Build.MANUFACTURER.orEmpty().lowercase()

    /** 제조사별로 더 해야 하는 설정. 모르는 제조사면 빈 문자열. */
    fun makerTip(): String = when (maker()) {
        "samsung" ->
            "삼성: 설정 → 배터리 → 백그라운드 사용 제한 → '절전 예외 앱'에 추가하고, " +
                "'사용하지 않는 앱을 절전 상태로 전환'을 꺼 주세요."
        "xiaomi", "redmi", "poco" ->
            "샤오미: 앱 정보 → '자동 시작' 허용, 배터리 절약 → '제한 없음'으로 바꿔 주세요."
        else -> ""
    }

    fun statusJson(context: Context): String = JSONObject()
        .put("exempt", isExempt(context))
        .put("maker", maker())
        .put("tip", makerTip())
        .toString()

    /** 예외 요청 창을 띄운다. 안 되면 목록 화면, 그것도 안 되면 앱 정보 화면. */
    @SuppressLint("BatteryLife")
    fun request(context: Context) {
        val pkg = context.packageName
        val attempts = listOf(
            Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).setData(Uri.parse("package:$pkg")),
            Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).setData(Uri.parse("package:$pkg")),
        )
        for (intent in attempts) {
            try {
                if (context !is android.app.Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
                return
            } catch (e: Exception) {
                Log.w(TAG, "cannot open ${intent.action}", e)
            }
        }
    }
}
