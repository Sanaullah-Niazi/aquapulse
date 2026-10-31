// FR-60 on Android: the phone registers for pushes after login, forgets its
// token on logout, and the push choices are saved with the other choices.
import 'dart:convert';

import 'package:aquapulse/push.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'helpers.dart';
import 'resident/resident_data.dart';

void main() {
  testWidgets('the phone registers its push token and forgets it on logout', (tester) async {
    final api = FakeApi();
    residentData(api);
    api.json('POST /me/push-devices', null);
    api.json('POST /me/push-devices/forget', null);
    api.json('POST /auth/logout', null);
    final push = FakePush();
    final (state, _) = await startApp(tester, api, role: 'resident', push: push);
    await tester.pumpAndSettle();

    final register = api.calls.where((r) => r.url.path.endsWith('/me/push-devices')).toList();
    expect(register, hasLength(1));
    expect(jsonDecode(register.single.body), {'token': push.value, 'platform': 'android'});

    // Firebase gives the phone a new token: the server hears about it.
    push.changes.add('fresh-push-token-0123456789abcdef');
    await tester.pumpAndSettle();
    expect(api.calls.where((r) => r.url.path.endsWith('/me/push-devices')), hasLength(2));

    await state.logout();
    await tester.pumpAndSettle();
    final forget = api.calls.lastWhere((r) => r.url.path.endsWith('/forget'));
    expect(jsonDecode(forget.body), {'token': 'fresh-push-token-0123456789abcdef'});
    expect(push.deleted, isTrue);
  });

  testWidgets('without Firebase in the build nothing is registered', (tester) async {
    final api = FakeApi();
    residentData(api);
    await startApp(tester, api, role: 'resident', push: FakePush(available: false));
    expect(api.called, isNot(contains('POST /me/push-devices')));
  });

  testWidgets('push choices are saved when the server has push', (tester) async {
    final api = FakeApi();
    residentData(api);
    api.json('GET /me/notifications', {
      'alert_email_min': 'warning',
      'email_muted_rules': <String>[],
      'quiet_from': null,
      'quiet_until': null,
      'push_min': 'warning',
      'push_muted_rules': <String>[],
      'push_available': true,
    });
    api.json('POST /me/push-devices', null);
    api.on('PATCH /me/notifications', (r) => jsonDecode(r.body));
    await startApp(tester, api, role: 'resident', push: FakePush());
    await tester.tap(find.text('Profile'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Notifications'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('push-min')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Critical only').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('push-mute-high_usage')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    final sent = jsonDecode(api.calls.lastWhere((r) => r.method == 'PATCH').body);
    expect(sent['push_min'], 'critical');
    expect(sent['push_muted_rules'], ['high_usage']);
  });

  testWidgets('the profile explains when the server has no push', (tester) async {
    final api = FakeApi();
    residentData(api);
    await startApp(tester, api, role: 'resident', push: FakePush());
    await tester.tap(find.text('Profile'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Notifications'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Push is not set up on this server'), findsOneWidget);
  });
}
