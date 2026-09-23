package com.ohjn96.trainreservation

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * 앱 안의 파이썬 서버(127.0.0.1:<무작위 포트>)에 붙는 도우미.
 *
 * 토큰을 쿠키로 보내기 전에 그 포트에 떠 있는 게 정말 우리 서버인지 확인한다.
 * 같은 폰의 다른 앱이 포트를 차지하고 우리 행세를 하면 토큰을 가로챌 수 있기 때문이다.
 * 서버는 GET /__hello?nonce=<무작위> 에 HMAC-SHA256(key=토큰, msg=nonce) 을 hex 로 돌려준다.
 */
object LocalServer {
    private val random = SecureRandom()

    fun baseUrl(port: Int) = "http://127.0.0.1:$port"

    /** 그 포트의 서버가 토큰을 아는지(= 우리 서버인지). 쿠키는 싣지 않는다. */
    fun verify(port: Int, token: String): Boolean {
        if (port <= 0) return false
        val nonce = ByteArray(16).also { random.nextBytes(it) }.toHex()
        return try {
            val conn = URL("${baseUrl(port)}/__hello?nonce=$nonce").openConnection() as HttpURLConnection
            conn.connectTimeout = 1000
            conn.readTimeout = 2000
            conn.instanceFollowRedirects = false
            try {
                if (conn.responseCode != 200) return false
                val body = conn.inputStream.bufferedReader().use { it.readText() }
                val given = JSONObject(body).optString("mac")
                MessageDigest.isEqual(given.toByteArray(), mac(token, nonce).toByteArray())
            } finally {
                conn.disconnect()
            }
        } catch (e: Exception) {
            false
        }
    }

    private fun mac(token: String, nonce: String): String {
        val hmac = Mac.getInstance("HmacSHA256")
        hmac.init(SecretKeySpec(token.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return hmac.doFinal(nonce.toByteArray(Charsets.UTF_8)).toHex()
    }

    private fun ByteArray.toHex() = joinToString("") { "%02x".format(it) }
}
