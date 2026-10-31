// Test helpers: a fake AquaPulse API (no network) and a way to start the app on it.
import 'dart:convert';

import 'package:aquapulse/api/client.dart';
import 'package:aquapulse/api/token_store.dart';
import 'package:aquapulse/app_state.dart';
import 'package:aquapulse/live.dart';
import 'package:aquapulse/main.dart';
import 'package:aquapulse/push.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

typedef Handler = Object? Function(http.Request request);

/// Answers like the real API. Routes are "GET /homes/h1/live" (the path after
/// /api/v1, without the query). A handler returns a JSON value, or a
/// (status, body) record for errors.
class FakeApi {
  final routes = <String, Handler>{};
  final calls = <http.Request>[];

  void on(String route, Handler handler) => routes[route] = handler;
  void json(String route, Object? body) => routes[route] = (_) => body;

  List<String> get called => [for (final r in calls) '${r.method} ${r.url.path.replaceFirst('/api/v1', '')}'];

  late final client = MockClient((request) async {
    calls.add(request);
    final path = request.url.path.replaceFirst('/api/v1', '');
    final handler = routes['${request.method} $path'];
    if (handler == null) return http.Response(jsonEncode({'detail': 'Not Found'}), 404);
    final result = handler(request);
    if (result is (int, Object?)) return http.Response(jsonEncode({'detail': result.$2}), result.$1);
    return http.Response.bytes(utf8.encode(jsonEncode(result)), 200, headers: {'content-type': 'application/json'});
  });
}

Map<String, dynamic> meJson({
  String role = 'resident',
  List<String> homes = const ['h1'],
  List<String> buildings = const [],
}) => {
  'id': 'u1',
  'email': '$role@example.com',
  'full_name': role == 'owner' ? 'Owner One' : 'Resident One',
  'phone': null,
  'role': role,
  'is_active': true,
  'building_ids': buildings,
  'home_ids': homes,
  'two_factor': false,
};

const tokens = {'access_token': 'access-1', 'refresh_token': 'refresh-1', 'expires_in': 900, 'token_type': 'bearer'};

/// Starts the app on the fake API, already logged in when [role] is given.
Future<(AppState, MemoryTokenStore)> startApp(
  WidgetTester tester,
  FakeApi api, {
  String? role,
  Size size = const Size(420, 1800),
  PushService? push,
}) async {
  // A tall phone, so long screens are built without scrolling in most tests.
  tester.view.physicalSize = size * 2;
  tester.view.devicePixelRatio = 2;
  addTearDown(tester.view.reset);
  final store = MemoryTokenStore();
  final client = ApiClient(client: api.client, store: store, baseUrl: 'https://test.example');
  final state = AppState(api: client, live: Live(client), push: push); // no httpClient: no real WebSocket
  if (role != null) {
    store.refresh = 'saved-refresh';
    api.json('POST /auth/refresh', tokens);
  }
  await state.init();
  await tester.pumpWidget(AquaPulseApp(state: state));
  await tester.pumpAndSettle();
  return (state, store);
}
