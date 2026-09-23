package com.ohjn96.trainreservation

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 도는 중인 매크로 작업(열차·좌석·코레일 로그인·카드)을 암호화해 둔다.
 * 프로세스가 죽었다 살아나거나 폰을 재부팅해도 자동으로 이어서 돌리기 위해서다.
 *
 * 키는 Android Keystore 안에 있어 앱 밖으로 꺼낼 수 없다. 매크로가 정상적으로 끝나면
 * (예약 성공·사용자 중단·로그인 거부) 지운다.
 */
object SecureStore {
    private const val TAG = "SecureStore"
    private const val KEY_ALIAS = "train_job_key"
    private const val PREFS = "job"
    private const val KEY_DATA = "data"
    private const val GCM_TAG_BITS = 128

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    @Synchronized
    fun saveJob(context: Context, json: String) {
        try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            val encrypted = cipher.doFinal(json.toByteArray(Charsets.UTF_8))
            val value = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
                Base64.encodeToString(encrypted, Base64.NO_WRAP)
            // 곧바로 프로세스가 죽을 수도 있으니 동기로 쓴다
            prefs(context).edit().putString(KEY_DATA, value).commit()
        } catch (e: Exception) {
            Log.e(TAG, "saveJob failed", e)
        }
    }

    @Synchronized
    fun loadJob(context: Context): String? {
        val value = prefs(context).getString(KEY_DATA, null) ?: return null
        return try {
            val (iv, data) = value.split(":", limit = 2).map { Base64.decode(it, Base64.NO_WRAP) }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(GCM_TAG_BITS, iv))
            String(cipher.doFinal(data), Charsets.UTF_8)
        } catch (e: Exception) {
            // 키가 바뀌었거나(재설치·보안 설정 변경) 깨진 데이터: 되살릴 수 없으니 지운다
            Log.e(TAG, "loadJob failed", e)
            clearJob(context)
            null
        }
    }

    @Synchronized
    fun clearJob(context: Context) {
        prefs(context).edit().remove(KEY_DATA).commit()
    }

    fun hasJob(context: Context): Boolean = prefs(context).contains(KEY_DATA)

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
