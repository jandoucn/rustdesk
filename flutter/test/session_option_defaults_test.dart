import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/utils/session_option_defaults.dart';

void main() {
  group('session option defaults', () {
    test('regular connections clear a persisted view-only option', () {
      expect(
        shouldToggleSessionOption(current: true, requested: false),
        isTrue,
      );
      expect(
        shouldToggleSessionOption(current: false, requested: false),
        isFalse,
      );
      expect(
        shouldToggleSessionOption(current: true, requested: true),
        isFalse,
      );
    });

    test('view-mode connections enable view-only', () {
      expect(
        shouldToggleSessionOption(current: false, requested: true),
        isTrue,
      );
    });

    test('session defaults only apply to new connections', () {
      expect(
        shouldApplySessionOptionDefaults(hasTabWindowId: false),
        isTrue,
      );
      expect(
        shouldApplySessionOptionDefaults(hasTabWindowId: true),
        isFalse,
      );
    });

    test('Windows and Android controllers swap keys for macOS peers', () {
      expect(
        shouldAutoEnableControlCommandSwap(
          localIsWindows: true,
          localIsAndroid: false,
          peerIsMacOS: true,
        ),
        isTrue,
      );
      expect(
        shouldAutoEnableControlCommandSwap(
          localIsWindows: false,
          localIsAndroid: true,
          peerIsMacOS: true,
        ),
        isTrue,
      );
    });

    test('other controller and peer combinations keep the existing default',
        () {
      expect(
        shouldAutoEnableControlCommandSwap(
          localIsWindows: true,
          localIsAndroid: false,
          peerIsMacOS: false,
        ),
        isFalse,
      );
      expect(
        shouldAutoEnableControlCommandSwap(
          localIsWindows: false,
          localIsAndroid: false,
          peerIsMacOS: true,
        ),
        isFalse,
      );
    });
  });
}
