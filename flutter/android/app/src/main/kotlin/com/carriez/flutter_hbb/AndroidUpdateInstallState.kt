package com.carriez.flutter_hbb

internal object AndroidUpdateInstallState {
    const val PHASE_PERMISSION = "permission"
    const val PHASE_INSTALLER = "installer"

    enum class Event {
        RESUME,
        PERMISSION_RESULT,
        INSTALLER_RESULT
    }

    enum class Action {
        WAIT,
        OPEN_INSTALLER,
        REPORT_INSTALLED,
        REPORT_FAILED
    }

    fun nextAction(
        phase: String,
        event: Event,
        permissionGranted: Boolean,
        installedVersionCode: Long,
        targetVersionCode: Long
    ): Action {
        if (installedVersionCode >= targetVersionCode) {
            return Action.REPORT_INSTALLED
        }
        return when (phase) {
            PHASE_PERMISSION -> when {
                permissionGranted -> Action.OPEN_INSTALLER
                event == Event.PERMISSION_RESULT -> Action.REPORT_FAILED
                else -> Action.WAIT
            }
            PHASE_INSTALLER -> when (event) {
                Event.INSTALLER_RESULT -> Action.REPORT_FAILED
                else -> Action.WAIT
            }
            else -> Action.REPORT_FAILED
        }
    }
}
