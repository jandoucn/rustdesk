package com.carriez.flutter_hbb

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import java.io.File

class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(
            PackageInstaller.EXTRA_STATUS,
            PackageInstaller.STATUS_FAILURE
        )
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty()
        val stagedApk = intent.getStringExtra(EXTRA_STAGED_APK).orEmpty()

        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                MainActivity.emitUpdateInstallStatus(context, STATUS_CONFIRMATION_REQUIRED, "")
                @Suppress("DEPRECATION")
                val confirmation = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirmation == null) {
                    deleteStagedApk(stagedApk)
                    MainActivity.emitUpdateInstallStatus(
                        context,
                        STATUS_FAILED,
                        "missing_user_confirmation_intent"
                    )
                } else {
                    confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    try {
                        context.startActivity(confirmation)
                    } catch (error: Exception) {
                        Log.e(TAG, "Unable to open package installer confirmation", error)
                        deleteStagedApk(stagedApk)
                        MainActivity.emitUpdateInstallStatus(
                            context,
                            STATUS_FAILED,
                            "open_user_confirmation_failed"
                        )
                    }
                }
            }

            PackageInstaller.STATUS_SUCCESS -> {
                deleteStagedApk(stagedApk)
                MainActivity.emitUpdateInstallStatus(context, STATUS_INSTALLED, "")
                context.packageManager.getLaunchIntentForPackage(context.packageName)?.let { launch ->
                    launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                    try {
                        context.startActivity(launch)
                    } catch (error: Exception) {
                        Log.e(TAG, "Update installed but RustDesk could not be reopened", error)
                    }
                }
            }

            else -> {
                deleteStagedApk(stagedApk)
                MainActivity.emitUpdateInstallStatus(
                    context,
                    STATUS_FAILED,
                    packageInstallerError(status, message)
                )
            }
        }
    }

    private fun deleteStagedApk(path: String) {
        if (path.isNotEmpty()) {
            runCatching { File(path).delete() }
        }
    }

    private fun packageInstallerError(status: Int, message: String): String {
        val code = when (status) {
            PackageInstaller.STATUS_FAILURE_ABORTED -> "aborted"
            PackageInstaller.STATUS_FAILURE_BLOCKED -> "blocked"
            PackageInstaller.STATUS_FAILURE_CONFLICT -> "conflict"
            PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> "incompatible"
            PackageInstaller.STATUS_FAILURE_INVALID -> "invalid"
            PackageInstaller.STATUS_FAILURE_STORAGE -> "storage"
            else -> "failure"
        }
        return if (message.isEmpty()) code else "$code:$message"
    }

    companion object {
        const val ACTION_UPDATE_INSTALL_STATUS =
            "com.carriez.flutter_hbb.action.UPDATE_INSTALL_STATUS"
        const val EXTRA_STAGED_APK = "staged_apk"
        const val STATUS_CONFIRMATION_REQUIRED = "confirmation_required"
        const val STATUS_FAILED = "failed"
        const val STATUS_INSTALLED = "installed"
        private const val TAG = "UpdateInstallReceiver"
    }
}
