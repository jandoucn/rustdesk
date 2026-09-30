bool shouldToggleSessionOption({
  required bool current,
  required bool requested,
}) =>
    current != requested;

bool shouldApplySessionOptionDefaults({required bool hasTabWindowId}) =>
    !hasTabWindowId;

bool shouldAutoEnableControlCommandSwap({
  required bool localIsWindows,
  required bool localIsAndroid,
  required bool peerIsMacOS,
}) =>
    (localIsWindows || localIsAndroid) && peerIsMacOS;
