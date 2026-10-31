// FR-41 and FR-70: server address, login, the two-factor step, tokens in
// secure storage only, and the role deciding which screens appear.
import 'dart:convert';
import 'dart:io';

import 'package:aquapulse/api/server.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'helpers.dart';
import 'resident/resident_data.dart';

void main() {
  testWidgets('a resident logs in and sees only resident screens', (tester) async {
    final api = FakeApi();
    residentData(api);
    api.json('POST /auth/login', tokens);
    final (state, store) = await startApp(tester, api);

    expect(find.text('Log in'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('email')), 'resident01@example.com');
    await tester.enterText(find.byKey(const Key('password')), 'secret password');
    await tester.tap(find.byKey(const Key('login')));
    await tester.pumpAndSettle();

    expect(state.me!.role, 'resident');
    expect(find.text('My home'), findsWidgets);
    expect(find.text('Unit 01 · Demo Building'), findsOneWidget);
    // No owner screens anywhere.
    await tester.tap(find.byTooltip('Open navigation menu'));
    await tester.pumpAndSettle();
    expect(find.text('Overview'), findsNothing);
    expect(find.text('Homes'), findsNothing);
    expect(find.text('My bills'), findsOneWidget);

    // The refresh token is in the secure store; the access token is not saved anywhere.
    expect(store.refresh, 'refresh-1');
    final login = api.calls.firstWhere((r) => r.url.path.endsWith('/auth/login'));
    expect(jsonDecode(login.body), {'email': 'resident01@example.com', 'password': 'secret password'});
  });

  testWidgets('two-factor login asks for the code, then logs in', (tester) async {
    final api = FakeApi();
    residentData(api);
    api.on('POST /auth/login', (request) {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      if (body['code'] == null) return (401, 'Two-factor code required');
      if (body['code'] != '123456') return (401, 'Wrong two-factor code');
      return tokens;
    });
    final (state, _) = await startApp(tester, api);

    await tester.enterText(find.byKey(const Key('email')), 'resident01@example.com');
    await tester.enterText(find.byKey(const Key('password')), 'secret password');
    await tester.tap(find.byKey(const Key('login')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('code')), findsOneWidget);

    await tester.enterText(find.byKey(const Key('code')), '000000');
    await tester.tap(find.byKey(const Key('login')));
    await tester.pumpAndSettle();
    expect(find.text('Wrong two-factor code'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('code')), '123456');
    await tester.tap(find.byKey(const Key('login')));
    await tester.pumpAndSettle();
    expect(state.me, isNotNull);
  });

  testWidgets('a wrong password shows the API message', (tester) async {
    final api = FakeApi();
    api.on('POST /auth/login', (_) => (401, 'Wrong email or password'));
    await startApp(tester, api);
    await tester.enterText(find.byKey(const Key('email')), 'x@example.com');
    await tester.enterText(find.byKey(const Key('password')), 'nope');
    await tester.tap(find.byKey(const Key('login')));
    await tester.pumpAndSettle();
    expect(find.text('Wrong email or password'), findsOneWidget);
  });

  testWidgets('a saved login is used at start-up, and a refused one logs out', (tester) async {
    final api = FakeApi();
    residentData(api);
    final (state, store) = await startApp(tester, api, role: 'resident');
    expect(state.me, isNotNull);
    expect(store.refresh, 'refresh-1'); // swapped for a new one (refresh tokens work once)

    // The server ends the session: the next 401 sends us back to login.
    api.on('GET /homes/h1/live', (_) => (401, 'Not logged in'));
    api.on('POST /auth/refresh', (_) => (401, 'Session ended'));
    await state.api.get('/homes/h1/live').catchError((_) => null);
    await tester.pumpAndSettle();
    expect(state.me, isNull);
    expect(store.refresh, isNull);
    expect(find.text('Log in'), findsOneWidget);
  });

  testWidgets('a system admin is sent to the web console', (tester) async {
    final api = FakeApi();
    api.json('GET /me', meJson(role: 'sysadmin', homes: []));
    await startApp(tester, api, role: 'sysadmin');
    expect(find.textContaining('System Admins use the web console'), findsOneWidget);
  });

  test('server addresses are cleaned up, and only HTTPS is allowed', () {
    expect(normaliseServer('water.example.com'), 'https://water.example.com');
    expect(normaliseServer('https://localhost:8443/'), 'https://localhost:8443');
    expect(normaliseServer('https://x.example/api/v1'), 'https://x.example');
    expect(normaliseServer('http://x.example'), isNull);
    expect(normaliseServer('   '), isNull);
  });

  test('tokens are never kept in plain settings files (FR-41)', () {
    // No SharedPreferences anywhere in the app, and the token store uses the
    // Keystore-backed secure storage.
    final pubspec = File('pubspec.yaml').readAsStringSync();
    expect(pubspec.contains('shared_preferences'), isFalse);
    for (final file in Directory('lib').listSync(recursive: true).whereType<File>()) {
      expect(file.readAsStringSync().contains('SharedPreferences.getInstance'), isFalse, reason: file.path);
    }
    final store = File('lib/api/token_store.dart').readAsStringSync();
    expect(store, contains('FlutterSecureStorage'));
  });
}
