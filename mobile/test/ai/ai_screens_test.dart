// M10 on Android: the forecast with its range and bill estimate (FR-65),
// insights with their reason and evidence (FR-63, FR-64, FR-66), the owner's
// automatic shut-off switch (FR-61) and the model list (FR-67).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';
import '../owner/owner_data.dart';
import '../resident/resident_data.dart';
import 'ai_data.dart';

Future<void> openMenu(WidgetTester tester, String item) async {
  await tester.tap(find.byTooltip('Open navigation menu'));
  await tester.pumpAndSettle();
  await tester.tap(find.text(item).last);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('a resident sees the forecast, the bill estimate and explained insights', (tester) async {
    final api = FakeApi();
    residentData(api);
    aiData(api);
    await startApp(tester, api, role: 'resident', size: const Size(420, 2600));
    await openMenu(tester, 'AI insights');

    expect(find.text('3,500 L'), findsOneWidget); // next 7 days: 7 x 500
    expect(find.text('12,500 L'), findsOneWidget);
    expect(find.text('likely 12,450 L to 12,560 L'), findsOneWidget);
    expect(find.text('PKR 780.00'), findsOneWidget);
    expect(find.text('likely PKR 774.00 to PKR 787.20'), findsOneWidget);
    expect(find.textContaining('Not a bill'), findsOneWidget);

    expect(find.text('Burst pipe suspected'), findsOneWidget);
    expect(find.textContaining('up to 35.0 L/min'), findsOneWidget);
    expect(find.textContaining('confidence 95% · sure'), findsOneWidget);
    expect(find.text('Unusual water use'), findsOneWidget);
    expect(find.textContaining('model v3'), findsOneWidget);
    expect(find.text('Times the usual day: 2.44 (normal 1.0)'), findsOneWidget);
  });

  testWidgets('without water rates the bill estimate says why', (tester) async {
    final api = FakeApi();
    residentData(api);
    aiData(api);
    api.json('GET /homes/h1/forecast', forecastJson(bill: null));
    await startApp(tester, api, role: 'resident', size: const Size(420, 2600));
    await openMenu(tester, 'AI insights');
    expect(find.text('The owner has not set water rates for this month yet.'), findsOneWidget);
  });

  testWidgets('the owner switches automatic shut-off on for a home', (tester) async {
    final api = FakeApi();
    ownerData(api);
    aiData(api);
    var on = false;
    api.on('GET /homes/h1', (_) => {'id': 'h1', 'auto_shutoff_enabled': on});
    api.on('PATCH /homes/h1', (r) {
      on = (jsonDecode(r.body) as Map)['auto_shutoff_enabled'] as bool;
      return {'id': 'h1', 'auto_shutoff_enabled': on};
    });
    await startApp(tester, api, role: 'owner', size: const Size(420, 4000));
    await tester.tap(find.text('Homes'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Unit 01 · Resident One'));
    await tester.pumpAndSettle();

    expect(find.text('Burst pipe suspected'), findsOneWidget);
    expect(find.text('Off: you get the alert, and you decide.'), findsOneWidget);
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(on, isTrue);
    expect(find.textContaining('On: the valve closes by itself'), findsOneWidget);
  });

  testWidgets('the owner sees the building forecast and which model is live', (tester) async {
    final api = FakeApi();
    ownerData(api);
    aiData(api);
    await startApp(tester, api, role: 'owner', size: const Size(420, 2600));
    await openMenu(tester, 'AI insights');
    expect(find.text('Forecast for the whole building'), findsOneWidget);
    expect(find.text('Unusual use v2 · live'), findsOneWidget);
    expect(find.text('Forecast v1'), findsOneWidget);
    expect(find.textContaining('MAPE 16% · naive 20% · in range 85%'), findsOneWidget);
  });
}
