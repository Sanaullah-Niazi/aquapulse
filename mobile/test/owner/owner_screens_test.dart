// FR-41 to FR-47 on Android for owners: overview (live), homes search and
// sort with the pH warning, valve and lock, tank and pump, bills.
import 'dart:convert';

import 'package:aquapulse/api/owner_models.dart';
import 'package:aquapulse/screens/owner/homes.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';
import 'owner_data.dart';

Future<void> openMenu(WidgetTester tester, String item) async {
  await tester.tap(find.byTooltip('Open navigation menu'));
  await tester.pumpAndSettle();
  await tester.tap(find.text(item).last);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('an owner sees the building overview, updated live', (tester) async {
    final api = FakeApi();
    ownerData(api);
    final (state, _) = await startApp(tester, api, role: 'owner');

    expect(find.text('Demo Building'), findsOneWidget);
    expect(find.text('650 L'), findsOneWidget);
    expect(find.text('9,000 L'), findsOneWidget);
    expect(find.text('-10% vs same days last month'), findsOneWidget);
    expect(find.text('2 / 3'), findsOneWidget);
    expect(find.text('1 critical · 2 warning · 0 info'), findsOneWidget);
    expect(find.text('64%'), findsOneWidget);
    expect(find.text('PKR 2,340.00'), findsOneWidget);
    expect(find.textContaining('Offline: unit 03'), findsOneWidget);
    expect(find.text('My home'), findsNothing); // no resident screens

    state.live.inject({'type': 'telemetry', 'homeId': 'h1', 'flowLpm': 6.0, 'deltaL': 50.0, 'valve': 'open'});
    await tester.pumpAndSettle();
    expect(find.text('8.0 L/min'), findsOneWidget); // 6 + 1 + 1
    expect(find.text('700 L'), findsOneWidget);
  });

  test('homes are searched by unit or resident and sorted by any column', () {
    final rows = [for (final r in overviewJson()['rows'] as List) OverviewRow.fromJson(r as Map<String, dynamic>)];
    expect(searchAndSort(rows, 'ayesha', HomeSort.unit, true).map((r) => r.unitNo), ['02']);
    expect(searchAndSort(rows, '03', HomeSort.unit, true).map((r) => r.unitNo), ['03']);
    expect(searchAndSort(rows, '', HomeSort.today, true).first.unitNo, '02');
    expect(searchAndSort(rows, '', HomeSort.ph, false).first.unitNo, '02');
    expect(searchAndSort(rows, '', HomeSort.resident, true).map((r) => r.unitNo), ['02', '03', '01']);
    expect(searchAndSort(rows, '', HomeSort.online, true).first.unitNo, '03');
    expect(searchAndSort(rows, '', HomeSort.alerts, false).first.unitNo, '02');
  });

  testWidgets('the homes list searches and warns about unsafe pH with a word, not colour only', (tester) async {
    final api = FakeApi();
    ownerData(api);
    await startApp(tester, api, role: 'owner');
    await tester.tap(find.text('Homes'));
    await tester.pumpAndSettle();
    expect(find.text('Unit 02 · Ayesha Khan'), findsOneWidget);
    expect(find.text('Unsafe'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('home-search')), 'bilal');
    await tester.pumpAndSettle();
    expect(find.text('Unit 03 · Bilal Ahmed'), findsOneWidget);
    expect(find.text('Unit 01 · Resident One'), findsNothing);
  });

  testWidgets('home detail: the valve confirm names the resident; a lock needs a 10-character reason', (tester) async {
    final api = FakeApi();
    ownerData(api);
    api.on('POST /homes/h1/valve-lock', (r) => {'ok': true});
    await startApp(tester, api, role: 'owner');
    await tester.tap(find.text('Homes'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Unit 01 · Resident One'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Turn water off'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Unit 01 (Resident One)'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Lock the valve'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('lock-reason')), 'too short');
    await tester.pump();
    final lock = find.widgetWithText(FilledButton, 'Lock');
    expect(tester.widget<FilledButton>(lock).onPressed, isNull);
    await tester.enterText(find.byKey(const Key('lock-reason')), 'Pipe repair on floor 1');
    await tester.pump();
    await tester.tap(lock);
    await tester.pumpAndSettle();
    final sent = api.calls.firstWhere((r) => r.url.path.endsWith('/valve-lock'));
    expect(jsonDecode(sent.body), {'state': 'closed', 'reason': 'Pipe repair on floor 1'});
  });

  testWidgets('tank: switching to manual asks first, then the pump button appears', (tester) async {
    final api = FakeApi();
    ownerData(api);
    api.on('PATCH /tanks/t1/settings', (r) {
      api.json('GET /buildings/b1/tank', tankJson(mode: 'manual'));
      return {'commands': <Object>[]};
    });
    final (state, _) = await startApp(tester, api, role: 'owner');
    await tester.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text('Tank')));
    await tester.pumpAndSettle();
    expect(find.text('64%'), findsOneWidget);
    expect(find.text('Pump: off'), findsOneWidget);
    expect(find.text('Turn pump on'), findsNothing);
    expect(find.text('2.5 h · 1.9 kWh'), findsOneWidget);

    await tester.tap(find.text('Switch to manual'));
    await tester.pumpAndSettle();
    expect(find.text('Switch to manual?'), findsOneWidget);
    expect(api.called.contains('PATCH /tanks/t1/settings'), isFalse);
    await tester.tap(find.widgetWithText(FilledButton, 'Switch to manual'));
    await tester.pumpAndSettle();
    expect(jsonDecode(api.calls.lastWhere((r) => r.method == 'PATCH').body), {'mode': 'manual'});
    expect(find.text('Turn pump on'), findsOneWidget);

    // A live tank reading moves the gauge.
    state.live.inject({'type': 'tank', 'levelPct': 70.0, 'pumpOn': true, 'mode': 'manual'});
    await tester.pumpAndSettle();
    expect(find.text('70%'), findsOneWidget);
    expect(find.text('Pump: ON'), findsOneWidget);
  });

  testWidgets('bills: make drafts, issue all, record a payment', (tester) async {
    final api = FakeApi();
    ownerData(api);
    api.on(
      'POST /buildings/b1/bills/generate',
      (_) => {'created': 3, 'updated': 0, 'kept': 0, 'issued': 0, 'problems': <String>[]},
    );
    api.on('POST /buildings/b1/bills/issue-all', (_) => {'issued': 1});
    api.on('POST /bills/bill1/payments', (r) => {'id': 'p1'});
    await startApp(tester, api, role: 'owner');
    await tester.tap(find.text('Bills'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Make draft bills'));
    await tester.pumpAndSettle();
    expect(find.textContaining('3 new, 0 recalculated'), findsOneWidget);

    await tester.tap(find.text('Issue all drafts (1)'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Yes, do it'));
    await tester.pumpAndSettle();
    expect(find.text('1 bills issued.'), findsOneWidget);

    await tester.tap(find.text('Unit 01 · September 2026'));
    await tester.pumpAndSettle();
    expect(find.text('Owner actions'), findsOneWidget);
    expect(find.text('Pay online (test mode)'), findsNothing); // only residents pay online
    await tester.enterText(find.byKey(const Key('pay-amount')), '500');
    await tester.tap(find.byKey(const Key('pay-save')));
    await tester.pumpAndSettle();
    final sent = api.calls.firstWhere((r) => r.url.path.endsWith('/payments'));
    expect(jsonDecode(sent.body), {'amount': '500', 'method': 'cash', 'reference': ''});
  });

  testWidgets('owner screens fit a 360 px phone without overflow', (tester) async {
    final api = FakeApi();
    ownerData(api);
    await startApp(tester, api, role: 'owner', size: const Size(360, 740));
    expect(tester.takeException(), isNull);
    for (final tab in ['Homes', 'Tank', 'Bills']) {
      await tester.tap(find.descendant(of: find.byType(NavigationBar), matching: find.text(tab)));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull, reason: tab);
    }
  });
}
