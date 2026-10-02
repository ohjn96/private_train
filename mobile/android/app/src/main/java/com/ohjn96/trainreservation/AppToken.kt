package com.ohjn96.trainreservation

import android.content.Context
import java.security.SecureRandom
import android.util.Base64

/**
 * 앱 안의 파이썬 서버는 127.0.0.1 에서 돈다. 같은 폰의 다른 앱도 거기에 붙을 수 있으므로
 * 설치마다 무작위 토큰을 만들어 WebView 쿠키로만 넘긴다. 토큰이 없으면 서버가 403.
 */
object AppToken {
    private const val PREFS = "app"
    private const val KEY = "server_token"

    fun get(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(KEY, null)?.let { return it }
        val bytes = ByteArray(32).also { SecureRandom().nextBytes(it) }
        val token = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        prefs.edit().putString(KEY, token).apply()
        return token
    }
}
