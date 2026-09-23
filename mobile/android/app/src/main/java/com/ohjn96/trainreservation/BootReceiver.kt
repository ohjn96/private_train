package com.ohjn96.trainreservation

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/** 폰을 재부팅해도, 돌던 매크로가 있었다면 자동으로 이어서 돌린다. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != Intent.ACTION_LOCKED_BOOT_COMPLETED
        ) return
        if (!SecureStore.hasJob(context)) return
        try {
            ServerService.start(context)
        } catch (e: Exception) {
            Log.e("BootReceiver", "could not resume after boot", e)
        }
    }
}
