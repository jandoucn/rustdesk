import 'dart:async';

import 'package:flutter_hbb/models/platform_model.dart';

const verifiedUpdateCommandKey = 'install-verified-update';

typedef UpdateCommand = Future<void> Function({
  required String key,
  required String value,
});

Future<void> requestVerifiedUpdate(UpdateCommand command) {
  return command(key: verifiedUpdateCommandKey, value: '');
}

bool isVerifiedUpdateBusy(String status) {
  return status == 'started' || status == 'installing';
}

void handleUpdate(String _) {
  unawaited(requestVerifiedUpdate(bind.mainSetCommon));
}
