import 'package:flutter/material.dart';
import 'package:flutter_hbb/common/widgets/address_book.dart';
import 'package:flutter_hbb/common/widgets/peer_card.dart';
import 'package:flutter_hbb/common/widgets/peer_tab_page.dart';
import 'package:flutter_hbb/desktop/pages/desktop_home_page.dart';
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

  test('address book cards preserve platform visual and show raw client id', () {
    final peer = _peer(id: '83077683', alias: 'GEP');

    expect(peerCardPrimaryText(peer), 'GEP');
    expect(peerCardSecondaryText(peer), '83077683');
    expect(showPeerPlatformVisual, isTrue);
  });

  test('address book card falls back to the official formatted id', () {
    final peer = _peer(id: '83077683');

    expect(peerCardPrimaryText(peer), '83 077 683');
    expect(peerCardSecondaryText(peer), isEmpty);
  });

  test('address book selector is hidden while tag panel remains enabled', () {
    expect(showAddressBookSelector, isFalse);
    expect(showAddressBookTagPanel, isTrue);
    expect(addressBookTagPanelWidth, inInclusiveRange(72, 80));
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

  test('only the standard dual-pane home uses the collapsible sidebar', () {
    expect(
      useCollapsibleDesktopSidebar(incomingOnly: false, sosMode: false),
      isTrue,
    );
    expect(
      useCollapsibleDesktopSidebar(incomingOnly: true, sosMode: false),
      isFalse,
    );
    expect(
      useCollapsibleDesktopSidebar(incomingOnly: false, sosMode: true),
      isFalse,
    );
    expect(standardDesktopSidebarExpandedWidth, 200);
    expect(standardDesktopSidebarCollapsedWidth, 28);
  });

  test('desktop sidebar collapse state is restored only from explicit yes', () {
    expect(desktopSidebarCollapsedFromLocalOption('Y'), isTrue);
    expect(desktopSidebarCollapsedFromLocalOption(''), isFalse);
    expect(desktopSidebarCollapsedFromLocalOption('N'), isFalse);
  });

  test('hidden desktop sidebar content uses the scaffold background', () {
    final theme = ThemeData.light();

    expect(
      desktopHomeEmptyPaneBackground(theme),
      theme.scaffoldBackgroundColor,
    );
  });
}
