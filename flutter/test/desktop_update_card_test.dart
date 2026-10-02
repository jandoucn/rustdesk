import 'package:flutter/material.dart';
import 'package:flutter_hbb/desktop/widgets/desktop_update_card.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('update card shows only the version, install and close actions',
      (tester) async {
    var installs = 0;
    var closes = 0;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 280,
          child: DesktopUpdateCard(
            version: '1.5.2',
            installLabel: '安装',
            onInstall: () => installs++,
            onClose: () => closes++,
          ),
        ),
      ),
    ));

    expect(find.text('1.5.2'), findsOneWidget);
    expect(find.text('安装'), findsOneWidget);
    expect(find.byType(Text), findsNWidgets(2));
    expect(find.textContaining('Changelog'), findsNothing);
    expect(find.textContaining('更新日志'), findsNothing);
    expect(find.textContaining('github.com'), findsNothing);
    expect(find.byType(OutlinedButton), findsOneWidget);
    expect(find.byType(IconButton), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('1.5.2'));
    expect(installs, 0);
    expect(closes, 0);
    await tester.tap(find.text('安装'));
    expect(installs, 1);
    expect(closes, 0);
    await tester.tap(find.byIcon(Icons.close));
    expect(closes, 1);
    expect(installs, 1);
  });
}
