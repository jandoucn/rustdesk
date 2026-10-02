import 'package:flutter/material.dart';
import 'package:flutter_hbb/common.dart';
import 'package:flutter_hbb/models/model.dart';
import 'package:flutter_hbb/models/input_model.dart';
import 'package:flutter_hbb/models/peer_tab_model.dart';
import 'package:flutter_hbb/common/widgets/setting_widgets.dart';
import 'package:flutter_hbb/consts.dart';
import 'package:flutter_hbb/desktop/pages/connection_page.dart';
import 'package:flutter_hbb/desktop/pages/desktop_setting_page.dart';
import 'package:flutter_hbb/desktop/widgets/remote_toolbar.dart';
import 'package:flutter_hbb/models/terminal_model.dart';
import 'package:flutter_hbb/models/rustdesk_terminal.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android defaults virtual mouse on unless explicitly disabled', () {
    expect(
      showVirtualMouseFromStoredOption(value: '', android: true),
      isTrue,
    );
    expect(
      showVirtualMouseFromStoredOption(value: 'Y', android: true),
      isTrue,
    );
    expect(
      showVirtualMouseFromStoredOption(value: 'N', android: true),
      isFalse,
    );
    expect(
      showVirtualMouseFromStoredOption(value: '', android: false),
      isFalse,
    );
  });

  test('display topology removal falls back to the only remaining display', () {
    expect(
      normalizedDisplayIndexAfterTopologyChange(
        currentDisplay: 1,
        serverDisplay: 0,
        displayCount: 1,
      ),
      0,
    );
    expect(
      normalizedDisplayIndexAfterTopologyChange(
        currentDisplay: 7,
        serverDisplay: 1,
        displayCount: 2,
      ),
      1,
    );
  });

  test('display topology keeps all-display and valid selections', () {
    expect(
      normalizedDisplayIndexAfterTopologyChange(
        currentDisplay: kAllDisplayValue,
        serverDisplay: 0,
        displayCount: 2,
      ),
      kAllDisplayValue,
    );
    expect(
      normalizedDisplayIndexAfterTopologyChange(
        currentDisplay: 1,
        serverDisplay: 0,
        displayCount: 2,
      ),
      1,
    );
  });

  test('Android pointer coordinates reach the selected macOS secondary display', () {
    final secondary = Rect.fromLTRB(-1920, 0, 0, 1080);

    final secondaryPoint = InputModel.getPointInRemoteRect(
      true,
      kPeerPlatformMacOS,
      kPointerEventKindMouse,
      '',
      -960,
      540,
      secondary,
    );
    expect(secondaryPoint?.x, -960);
    expect(secondaryPoint?.y, 540);
  });

  test('Android uses the same restricted peer tabs as desktop', () {
    expect(
      usesRestrictedPeerTabs(desktop: false, android: true),
      isTrue,
    );
    expect(
      restrictedPeerTabVisibility,
      [true, false, false, true, false],
    );
    expect(restrictedPeerTabOrder, [0, 3, 1, 2, 4]);
    expect(allowsPeerTabVisibilityMenu(android: true), isFalse);
    expect(allowsPeerTabVisibilityMenu(android: false), isTrue);
  });

  test('standard edition hides recording settings and remote chat', () {
    expect(showRecordingSettingsForEdition(sosMode: false), isFalse);
    expect(showRecordingSettingsForEdition(sosMode: true), isTrue);
    for (final action in StandardHiddenRemoteToolbarAction.values) {
      expect(showRemoteToolbarActionForEdition(action: action, sosMode: false),
          isFalse);
      expect(showRemoteToolbarActionForEdition(action: action, sosMode: true),
          isTrue);
    }
  });

  test('terminal clipboard defaults on but preserves an explicit denial', () {
    expect(
        terminalClipboardOptionWithDefault(''), kTerminalClipboardWriteAllowed);
    expect(terminalClipboardOptionWithDefault(kTerminalClipboardWriteDenied),
        kTerminalClipboardWriteDenied);
    expect(terminalClipboardOptionWithDefault(kTerminalClipboardWriteAllowed),
        kTerminalClipboardWriteAllowed);
    expect(
      terminalClipboardWritePermission(
        '',
        remoteClipboardEnabled: true,
      ),
      TerminalClipboardWritePermission.allowed,
    );
  });

  test('standard connection footer only contains service status', () {
    expect(connectionFooterExtrasForEdition(sosMode: false), isEmpty);
    expect(connectionFooterExtrasForEdition(sosMode: true), isEmpty);
  });

  test('update policy accepts integer or string revisions and rejects stale',
      () {
    expect(shouldApplyUpdatePolicy(currentRevision: -1, incomingRevision: 0),
        isTrue);
    expect(shouldApplyUpdatePolicy(currentRevision: 2, incomingRevision: 3),
        isTrue);
    expect(shouldApplyUpdatePolicy(currentRevision: 2, incomingRevision: '3'),
        isTrue);
    expect(shouldApplyUpdatePolicy(currentRevision: 3, incomingRevision: 3),
        isFalse);
    expect(shouldApplyUpdatePolicy(currentRevision: 3, incomingRevision: '2'),
        isFalse);
  });

  test('startup update check is disabled by default', () {
    expect(shouldCheckSoftwareUpdateOnStartup(''), isFalse);
    expect(shouldCheckSoftwareUpdateOnStartup('N'), isFalse);
    expect(shouldCheckSoftwareUpdateOnStartup('Y'), isTrue);
  });

  test('startup update prompt is shown for desktop startup checks', () {
    expect(
      shouldShowStartupUpdatePrompt(
        isDesktopMainWindow: true,
        autoUpdate: false,
        requestOrigin: 'startup',
        updateUrl: 'https://download.example/update.exe',
      ),
      isTrue,
    );
  });

  test('remote update prompt requires a visible main window', () {
    expect(
      shouldShowDesktopUpdatePrompt(
        windowVisible: true,
        autoUpdate: false,
        requestOrigin: 'command',
        updateUrl: 'https://download.example/update.exe',
      ),
      isTrue,
    );
    expect(
      shouldShowDesktopUpdatePrompt(
        windowVisible: false,
        autoUpdate: false,
        requestOrigin: 'command',
        updateUrl: 'https://download.example/update.exe',
      ),
      isFalse,
    );
    expect(
      shouldShowDesktopUpdatePrompt(
        windowVisible: true,
        autoUpdate: false,
        requestOrigin: 'startup',
        updateUrl: 'https://download.example/update.exe',
      ),
      isTrue,
    );
    expect(
      shouldShowDesktopUpdatePrompt(
        windowVisible: true,
        autoUpdate: false,
        requestOrigin: 'system',
        updateUrl: 'https://download.example/update.exe',
      ),
      isTrue,
    );
    expect(
      shouldShowDesktopUpdatePrompt(
        windowVisible: true,
        autoUpdate: false,
        requestOrigin: 'manual',
        updateUrl: 'https://download.example/update.exe',
      ),
      isFalse,
    );
  });

  test(
    'startup update prompt is suppressed for auto-update and non-startup checks',
    () {
      final base = {
        'isDesktopMainWindow': true,
        'autoUpdate': false,
        'requestOrigin': 'startup',
        'updateUrl': 'https://download.example/update.exe',
      };
      expect(
        shouldShowStartupUpdatePrompt(
          isDesktopMainWindow: base['isDesktopMainWindow'] as bool,
          autoUpdate: true,
          requestOrigin: base['requestOrigin'] as String,
          updateUrl: base['updateUrl'] as String,
        ),
        isFalse,
      );
      expect(
        shouldShowStartupUpdatePrompt(
          isDesktopMainWindow: base['isDesktopMainWindow'] as bool,
          autoUpdate: base['autoUpdate'] as bool,
          requestOrigin: 'system',
          updateUrl: base['updateUrl'] as String,
        ),
        isFalse,
      );
    },
  );

  test('scheduled update interval defaults to five hours and stays bounded',
      () {
    expect(scheduledUpdateIntervalHours(''), 5);
    expect(scheduledUpdateIntervalHours('invalid'), 5);
    expect(scheduledUpdateIntervalHours('0'), 1);
    expect(scheduledUpdateIntervalHours('169'), 168);
    expect(scheduledUpdateIntervalHours('12'), 12);
  });

  test('update check result exposes server version metadata', () {
    final state = UpdateUiState();

    state.applyCheckResult({
      'target_version': '1.5.0',
      'target_build_seq': 150,
      'current_version': '1.4.9',
      'current_build_seq': '149',
      'channel': 'stable',
      'mode': 'notify',
      'url': 'https://example.test/rustdesk.dmg',
      'error': '',
    });

    expect(state.targetVersion.value, '1.5.0');
    expect(state.targetBuildSeq.value, '150');
    expect(state.currentVersion.value, '1.4.9');
    expect(state.currentBuildSeq.value, '149');
    expect(state.channel.value, 'stable');
    expect(state.mode.value, 'notify');
    expect(state.updateUrl.value, 'https://example.test/rustdesk.dmg');
    expect(state.checkResultSerial.value, 1);
  });

  test('manual update check exposes a stable user-facing status', () {
    expect(
      softwareUpdateCheckStatus(error: '', updateUrl: '', checking: true),
      'Checking for updates',
    );
    expect(
      softwareUpdateCheckStatus(
          error: '', updateUrl: 'https://example.test/app.dmg'),
      'Update available',
    );
    expect(
      softwareUpdateCheckStatus(error: '', updateUrl: ''),
      'Up to date',
    );
    expect(
      softwareUpdateCheckStatus(error: 'network failed', updateUrl: ''),
      'network failed',
    );
  });

  test('manual update checks always resolve to a visible dialog result', () {
    expect(
      manualUpdateResultKind(
          error: '', updateUrl: 'https://example.test/app.exe'),
      ManualUpdateResultKind.updateAvailable,
    );
    expect(
      manualUpdateResultKind(error: '', updateUrl: ''),
      ManualUpdateResultKind.upToDate,
    );
    expect(
      manualUpdateResultKind(error: 'network failed', updateUrl: ''),
      ManualUpdateResultKind.error,
    );
  });

  test('manual update result only matches the request that started it', () {
    expect(
      isMatchingManualUpdateCheck(
        pendingRequestId: 'manual-1',
        requestOrigin: 'manual',
        requestId: 'manual-1',
      ),
      isTrue,
    );
    expect(
      isMatchingManualUpdateCheck(
        pendingRequestId: 'manual-1',
        requestOrigin: 'startup',
        requestId: 'manual-1',
      ),
      isFalse,
    );
    expect(
      isMatchingManualUpdateCheck(
        pendingRequestId: 'manual-1',
        requestOrigin: 'manual',
        requestId: 'manual-2',
      ),
      isFalse,
    );
  });

  test('Android install states only finish on installed or failed', () {
    expect(
      androidUpdateInstallStatus(status: 'permission_required'),
      'Installation permission required',
    );
    expect(
      androidUpdateInstallStatus(status: 'confirmation_required'),
      'Installation confirmation required',
    );
    expect(
      androidUpdateInstallStatus(status: 'installing'),
      'Installing update',
    );
    expect(isAndroidUpdateInstallTerminal('permission_required'), isFalse);
    expect(isAndroidUpdateInstallTerminal('confirmation_required'), isFalse);
    expect(isAndroidUpdateInstallTerminal('installing'), isFalse);
    expect(isAndroidUpdateInstallTerminal('installed'), isTrue);
    expect(isAndroidUpdateInstallTerminal('failed'), isTrue);
    expect(
      androidUpdateInstallStatus(status: 'failed', error: 'blocked'),
      'blocked',
    );
  });

  test('local update metadata is available before the first server check', () {
    final metadata = parseLocalUpdateMetadata(
      '{"version":"1.5.0","build_seq":2026093006,"channel":"stable"}',
    );

    expect(metadata.version, '1.5.0');
    expect(metadata.buildSeq, '2026093006');
    expect(metadata.channel, 'stable');
  });

  testWidgets('About update controls show versions, switches and action',
      (tester) async {
    var startup = false;
    var automatic = false;
    var scheduled = false;
    var intervalHours = 5;
    var checks = 0;

    await tester.pumpWidget(MaterialApp(
      home: Material(
        child: StandardAboutUpdateControls(
          currentVersion: '1.4.9',
          latestVersion: '1.5.0',
          updateStatus: 'Update available',
          checking: true,
          checkOnStartup: startup,
          autoUpdate: automatic,
          scheduledUpdate: scheduled,
          scheduledUpdateIntervalHours: intervalHours,
          onCheckOnStartupChanged: (value) async => startup = value,
          onAutoUpdateChanged: (value) async => automatic = value,
          onScheduledUpdateChanged: (value) async => scheduled = value,
          onScheduledUpdateIntervalChanged: (value) async =>
              intervalHours = value,
          onCheckUpdate: () async => checks++,
          translator: (value) => value,
        ),
      ),
    ));

    expect(find.textContaining('1.4.9'), findsOneWidget);
    expect(find.textContaining('1.5.0'), findsOneWidget);
    expect(find.text('Check for software update on startup'), findsOneWidget);
    expect(find.text('Auto update'), findsOneWidget);
    expect(
        find.text('Check for software updates periodically'), findsOneWidget);
    expect(find.text('5 hours'), findsOneWidget);
    expect(find.text('Check for updates'), findsOneWidget);

    final checkButton = tester.widget<OutlinedButton>(
      find.byWidgetPredicate((widget) => widget is OutlinedButton),
    );
    expect(checkButton.onPressed, isNull);

    await tester.tap(find.text('Check for software update on startup'));
    await tester.pump();
    await tester.tap(find.text('Auto update'));
    await tester.pump();
    await tester.tap(find.text('Check for software updates periodically'));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('scheduled-update-increment')));
    await tester.pump();
    expect(startup, isTrue);
    expect(automatic, isTrue);
    expect(scheduled, isTrue);
    expect(intervalHours, 6);
    expect(checks, 0);
  });

  testWidgets('Android About update controls fit a phone and start a check',
      (tester) async {
    var checks = 0;

    await tester.pumpWidget(MaterialApp(
      home: MediaQuery(
        data: const MediaQueryData(size: Size(360, 800)),
        child: Material(
          child: SingleChildScrollView(
            child: StandardAboutUpdateControls(
              currentVersion: '1.5.1',
              latestVersion: '',
              currentBuildSeq: '2026100202',
              channel: 'stable',
              checkOnStartup: false,
              autoUpdate: false,
              scheduledUpdate: false,
              scheduledUpdateIntervalHours: 5,
              onCheckOnStartupChanged: (_) async {},
              onAutoUpdateChanged: (_) async {},
              onScheduledUpdateChanged: (_) async {},
              onScheduledUpdateIntervalChanged: (_) async {},
              onCheckUpdate: () async => checks++,
              translator: (value) => value,
            ),
          ),
        ),
      ),
    ));

    expect(tester.takeException(), isNull);
    expect(find.textContaining('2026100202'), findsOneWidget);
    expect(find.text('Latest version: Not checked'), findsOneWidget);
    await tester.tap(find.text('Check for updates'));
    await tester.pump();
    expect(checks, 1);
  });
}
