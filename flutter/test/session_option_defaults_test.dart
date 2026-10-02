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

    test('Windows controllers swap Control and Command for macOS peers', () {
      expect(
        shouldAutoEnableControlCommandSwap(
          localIsWindows: true,
          peerIsMacOS: true,
        ),
        isTrue,
      );
    });

    test('Android controllers do not enable desktop key swapping', () {
      expect(
        shouldAutoEnableControlCommandSwap(
          localIsWindows: false,
          peerIsMacOS: true,
        ),
        isFalse,
      );
    });

    test('Windows controllers keep normal keys for non-macOS peers', () {
      expect(
        shouldAutoEnableControlCommandSwap(
          localIsWindows: true,
          peerIsMacOS: false,
        ),
        isFalse,
      );
    });
  });
}
