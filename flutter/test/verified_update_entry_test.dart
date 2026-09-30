import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/desktop/widgets/update_progress.dart';

void main() {
  test('desktop update entry delegates to the verified Rust updater', () async {
    String? capturedKey;
    String? capturedValue;

    await requestVerifiedUpdate(({required key, required value}) async {
      capturedKey = key;
      capturedValue = value;
    });

    expect(capturedKey, verifiedUpdateCommandKey);
    expect(capturedValue, isEmpty);
  });

  test('desktop update entry blocks only active install states', () {
    expect(isVerifiedUpdateBusy('started'), isTrue);
    expect(isVerifiedUpdateBusy('installing'), isTrue);
    expect(isVerifiedUpdateBusy('downloaded'), isFalse);
    expect(isVerifiedUpdateBusy('failed'), isFalse);
  });
}
