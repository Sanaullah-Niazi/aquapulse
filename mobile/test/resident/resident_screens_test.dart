// FR-48 to FR-52 on Android: my home (live), the valve with its confirm step
// and device answer, a locked valve, usage, quality, bills and notifications.
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';
import 'resident_data.dart';

Future<void> openMenu(WidgetTester tester, String item) async {
  await tester.tap(find.byTooltip('Open navigation menu'));
  await tester.pumpAndSettle();
  await tester.tap(find.text(item).last);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('my home shows the API numbers and updates live', (tester) async {
    final api = FakeApi();
    residentData(api);
    final (state, _) = await startApp(tester, api, role: 'resident');

    expect(find.text('312 L'), findsOneWidget); // today
    expect(find.text('4,321 L'), findsOneWidget); // this month
    expect(find.text('Running: 3.2 L/min'), findsOneWidget);
    expect(find.text('pH 7.2'), findsOneWidget);
    expect(find.text('Safe'), findsOneWidget);
    expect(find.text('24.5 °C'), findsOneWidget);
    expect(find.text('PKR 1,234.00'), findsOneWidget);
    expect(find.text('Water is ON'), findsOneWidget);
    expect(find.text('High daily use'), findsOneWidget);

    // A new reading arrives over the live connection: no request, the screen changes.
    final before = api.calls.where((r) => r.url.path.endsWith('/live')).length;
    state.live.inject({
      'type': 'telemetry',
      'homeId': 'h1',
      'time': DateTime.now().toUtc().toIso8601String(),
      'flowLpm': 0.0,
      'ph': 9.1,
      'tempC': 26.0,
      'valve': 'open',
    });
    await tester.pumpAndSettle();
    expect(find.text('Not running'), findsOneWidget);
    expect(find.text('pH 9.1'), findsOneWidget);
    expect(find.text('Unsafe'), findsOneWidget); // label and icon, not colour only
    expect(api.calls.where((r) => r.url.path.endsWith('/live')).length, before);
  });

  testWidgets('turning the water off asks first, then waits for the device', (tester) async {
    final api = FakeApi();
    residentData(api);
    var polls = 0;
    api.on('POST /homes/h1/valve', (_) => {'id': 'c1', 'action': 'close', 'state': 'pending', 'result_reason': null});
    api.on('GET /commands/c1', (_) {
      polls++;
      return {'id': 'c1', 'action': 'close', 'state': polls >= 2 ? 'succeeded' : 'pending', 'result_reason': null};
    });
    await startApp(tester, api, role: 'resident');

    await tester.scrollUntilVisible(find.text('Turn water off'), 200);
    await tester.tap(find.text('Turn water off'));
    await tester.pumpAndSettle();
    expect(find.text('Turn the water off?'), findsOneWidget);
    expect(api.called.contains('POST /homes/h1/valve'), isFalse); // nothing sent before "yes"

    api.json('GET /homes/h1/live', liveJson(valve: 'closed'));
    await tester.tap(find.text('Yes, do it'));
    await tester.pump();
    await tester.pump();
    expect(find.text('Waiting for the valve to confirm…'), findsOneWidget);
    final sent = api.calls.firstWhere((r) => r.url.path.endsWith('/valve'));
    expect(jsonDecode(sent.body), {'action': 'close', 'reason': ''});

    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.text('Done. The valve confirmed it.'), findsOneWidget);
    expect(find.text('Water is OFF'), findsOneWidget);
    expect(find.text('Turn water on'), findsOneWidget);
  });

  testWidgets('a timed-out command shows a message and a retry button', (tester) async {
    final api = FakeApi();
    residentData(api);
    api.on(
      'POST /homes/h1/valve',
      (_) => {'id': 'c2', 'action': 'close', 'state': 'timeout', 'result_reason': 'no answer'},
    );
    await startApp(tester, api, role: 'resident');
    await tester.scrollUntilVisible(find.text('Turn water off'), 200);
    await tester.tap(find.text('Turn water off'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Yes, do it'));
    await tester.pumpAndSettle();
    expect(find.text('The valve did not answer in time. It may be offline.'), findsOneWidget);
    expect(find.text('Try again'), findsOneWidget);
  });

  testWidgets('a locked valve has no button and shows the reason', (tester) async {
    final api = FakeApi();
    residentData(
      api,
      live: liveJson(
        valve: 'closed',
        lock: {'locked_state': 'closed', 'reason': 'Pipe repair on floor 1', 'locked_at': '2026-10-30T10:00:00Z'},
      ),
    );
    await startApp(tester, api, role: 'resident');
    await tester.scrollUntilVisible(find.textContaining('Pipe repair on floor 1'), 200);
    expect(find.textContaining('Locked by the building owner: Pipe repair on floor 1'), findsOneWidget);
    expect(find.text('Turn water on'), findsNothing);
    expect(find.text('Turn water off'), findsNothing);
  });

  testWidgets('usage shows the comparison and hides the building average under 3 homes', (tester) async {
    final api = FakeApi();
    residentData(api);
    await startApp(tester, api, role: 'resident');
    await tester.tap(find.text('Usage'));
    await tester.pumpAndSettle();
    expect(find.text('4,000 L'), findsOneWidget); // last month
    expect(find.text('1,440 L'), findsOneWidget); // per person
    expect(find.textContaining('at least 3 homes'), findsOneWidget);
    expect(find.text('Litres per day'), findsOneWidget);
  });

  testWidgets('water quality loads 24 hours of averages', (tester) async {
    final api = FakeApi();
    residentData(api);
    await startApp(tester, api, role: 'resident');
    await openMenu(tester, 'Water quality');
    expect(find.textContaining('pH, last 24 hours'), findsOneWidget);
    final call = api.calls.lastWhere((r) => r.url.path.endsWith('/telemetry'));
    expect(call.url.queryParameters['every'], '600');
  });

  testWidgets('bills list, bill detail and the test-mode pay button', (tester) async {
    final api = FakeApi();
    residentData(api);
    final (state, _) = await startApp(tester, api, role: 'resident');
    await tester.tap(find.text('Bills'));
    await tester.pumpAndSettle();
    expect(find.text('PKR 1,170.00'), findsOneWidget);
    expect(find.text('To pay'), findsOneWidget);

    await tester.tap(find.text('PKR 1,170.00'));
    await tester.pumpAndSettle();
    expect(find.text('Sales tax 17%'), findsOneWidget);
    expect(find.text('Download PDF'), findsOneWidget);
    expect(find.text('Pay online (test mode)'), findsOneWidget);

    // The owner records a payment: the bill changes by itself.
    api.json('GET /bills/bill1', {
      ...(jsonDecode(jsonEncode(api.routes['GET /bills/bill1']!(api.calls.last))) as Map<String, dynamic>),
      'status': 'paid',
      'amount_paid': '1170.00',
      'outstanding': '0.00',
    });
    state.live.inject({'type': 'bill', 'billId': 'bill1'});
    await tester.pumpAndSettle();
    expect(find.text('Paid'), findsWidgets);
    expect(find.text('Pay online (test mode)'), findsNothing);
  });

  testWidgets('notification choices are saved', (tester) async {
    final api = FakeApi();
    residentData(api);
    api.on('PATCH /me/notifications', (r) => jsonDecode(r.body));
    await startApp(tester, api, role: 'resident');
    await tester.tap(find.text('Profile'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Notifications'));
    await tester.pumpAndSettle();
    // Residents do not get tank alerts, so those are not offered.
    expect(find.text('Tank low'), findsNothing);
    await tester.tap(find.text('High daily use'));
    await tester.tap(find.text('Quiet hours'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(find.text('Saved. It applies to the next alert.'), findsOneWidget);
    final sent = jsonDecode(api.calls.lastWhere((r) => r.method == 'PATCH').body);
    expect(sent, {
      'alert_email_min': 'warning',
      'email_muted_rules': ['high_usage'],
      'quiet_from': '22:00',
      'quiet_until': '07:00',
    });
  });

  testWidgets('the phone layout fits a small screen (360 px) without overflow', (tester) async {
    final api = FakeApi();
    residentData(api);
    await startApp(tester, api, role: 'resident', size: const Size(360, 740));
    expect(tester.takeException(), isNull);
    expect(find.text('Water is ON'), findsOneWidget);
  });
}
