import 'package:flutter_hbb/common/widgets/address_book.dart';
import 'package:flutter_hbb/common/widgets/peer_card.dart';
import 'package:flutter_hbb/common/widgets/peer_tab_page.dart';
import 'package:flutter_hbb/models/peer_model.dart';
import 'package:flutter_test/flutter_test.dart';

Peer _peer({required String id, String alias = ''}) => Peer(
      id: id,
      username: 'Administrator',
      hostname: 'gep',
      alias: alias,
      platform: 'Windows',
      tags: const [],
      hash: '',
      password: '',
      forceAlwaysRelay: false,
      rdpPort: '',
      rdpUsername: '',
      loginName: '',
      device_group_name: '',
      note: '',
    );

void main() {
  test('new installations default to the compact tile view', () {
    expect(peerUiTypeFromLocalOption(''), PeerUiType.tile);
    expect(peerUiTypeFromLocalOption('0'), PeerUiType.grid);
    expect(peerUiTypeFromLocalOption('1'), PeerUiType.tile);
    expect(peerUiTypeFromLocalOption('2'), PeerUiType.list);
  });

  test('peer cards show alias and raw client id without platform visual', () {
    final peer = _peer(id: '83077683', alias: 'GEP');

    expect(peerCardPrimaryText(peer), 'GEP');
    expect(peerCardSecondaryText(peer), '83077683');
    expect(showPeerPlatformVisual, isFalse);
  });

  test('peer card falls back to formatted id when alias is empty', () {
    final peer = _peer(id: '83077683');

    expect(peerCardPrimaryText(peer), isNotEmpty);
    expect(peerCardSecondaryText(peer), isEmpty);
  });

  test('address book selector is hidden while tag panel remains enabled', () {
    expect(showAddressBookSelector, isFalse);
    expect(showAddressBookTagPanel, isTrue);
  });

  test('address book toolbar puts tags before search refresh and selection',
      () {
    expect(
      addressBookToolbarOrder,
      const [
        AddressBookToolbarAction.tags,
        AddressBookToolbarAction.search,
        AddressBookToolbarAction.refresh,
        AddressBookToolbarAction.multiSelection,
      ],
    );
  });
}
