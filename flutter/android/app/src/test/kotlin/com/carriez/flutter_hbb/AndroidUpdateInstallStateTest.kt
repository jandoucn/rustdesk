package com.carriez.flutter_hbb

import org.junit.Assert.assertEquals
import org.junit.Test

class AndroidUpdateInstallStateTest {
    @Test
    fun permissionResultContinuesOnlyAfterPermissionIsGranted() {
        assertEquals(
            AndroidUpdateInstallState.Action.OPEN_INSTALLER,
            AndroidUpdateInstallState.nextAction(
                phase = AndroidUpdateInstallState.PHASE_PERMISSION,
                event = AndroidUpdateInstallState.Event.PERMISSION_RESULT,
                permissionGranted = true,
                installedVersionCode = 10,
                targetVersionCode = 11
            )
        )
        assertEquals(
            AndroidUpdateInstallState.Action.REPORT_FAILED,
            AndroidUpdateInstallState.nextAction(
                phase = AndroidUpdateInstallState.PHASE_PERMISSION,
                event = AndroidUpdateInstallState.Event.PERMISSION_RESULT,
                permissionGranted = false,
                installedVersionCode = 10,
                targetVersionCode = 11
            )
        )
    }

    @Test
    fun coldStartWaitsForPermissionWithoutDiscardingThePendingUpdate() {
        assertEquals(
            AndroidUpdateInstallState.Action.WAIT,
            AndroidUpdateInstallState.nextAction(
                phase = AndroidUpdateInstallState.PHASE_PERMISSION,
                event = AndroidUpdateInstallState.Event.RESUME,
                permissionGranted = false,
                installedVersionCode = 10,
                targetVersionCode = 11
            )
        )
        assertEquals(
            AndroidUpdateInstallState.Action.OPEN_INSTALLER,
            AndroidUpdateInstallState.nextAction(
                phase = AndroidUpdateInstallState.PHASE_PERMISSION,
                event = AndroidUpdateInstallState.Event.RESUME,
                permissionGranted = true,
                installedVersionCode = 10,
                targetVersionCode = 11
            )
        )
    }

    @Test
    fun installerReturnWithoutVersionChangeIsTerminalFailure() {
        assertEquals(
            AndroidUpdateInstallState.Action.REPORT_FAILED,
            AndroidUpdateInstallState.nextAction(
                phase = AndroidUpdateInstallState.PHASE_INSTALLER,
                event = AndroidUpdateInstallState.Event.INSTALLER_RESULT,
                permissionGranted = true,
                installedVersionCode = 10,
                targetVersionCode = 11
            )
        )
    }

    @Test
    fun upgradedVersionWinsForBothInstallerReturnAndColdStart() {
        AndroidUpdateInstallState.Event.entries.forEach { event ->
            assertEquals(
                AndroidUpdateInstallState.Action.REPORT_INSTALLED,
                AndroidUpdateInstallState.nextAction(
                    phase = AndroidUpdateInstallState.PHASE_INSTALLER,
                    event = event,
                    permissionGranted = true,
                    installedVersionCode = 11,
                    targetVersionCode = 11
                )
            )
        }
    }

    @Test
    fun coldStartWithUnchangedVersionWaitsForTheInstallerResult() {
        assertEquals(
            AndroidUpdateInstallState.Action.WAIT,
            AndroidUpdateInstallState.nextAction(
                phase = AndroidUpdateInstallState.PHASE_INSTALLER,
                event = AndroidUpdateInstallState.Event.RESUME,
                permissionGranted = true,
                installedVersionCode = 10,
                targetVersionCode = 11
            )
        )
    }
}
