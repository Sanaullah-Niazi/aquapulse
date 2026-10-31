// Who is logged in, for the whole app: server address, login (with the
// two-factor code, FR-70), logout and "who am I".
import 'dart:async';

import 'package:flutter/widgets.dart';

import 'api/client.dart';
import 'api/models.dart';
import 'api/server.dart';
import 'api/token_store.dart';
import 'live.dart';
import 'push.dart';

enum LoginResult { ok, needCode }

class AppState extends ChangeNotifier {
  AppState({required this.api, required this.live, this.push});

  /// The real app: secure storage, the saved server, and the trusted CA.
  static Future<AppState> create() async {
    final trust = await ServerTrust.load();
    final store = SecureTokenStore();
    final server = await store.readServer() ?? defaultServer;
    final api = ApiClient(client: trust.httpClient(), store: store, baseUrl: server);
    final state = AppState(
      api: api,
      live: Live(api, httpClient: trust.ioClient),
    );
    state.localCa = trust.localCa;
    return state;
  }

  final ApiClient api;
  final Live live;

  /// Push notifications (M9); null or not ready = no push in this build.
  final PushService? push;
  bool pushReady = false;
  String? _pushToken;
  StreamSubscription<String>? _tokenSub;
  Me? me;

  /// True while we check a saved login at start-up.
  bool loading = true;

  /// The local CA built into this app by `make apk` (shown on the server screen).
  String? localCa;

  /// Start-up: if a refresh token is saved, log in with it. A busy or briefly
  /// unreachable server must not log you out, so try again a few times.
  Future<void> init() async {
    final push = this.push;
    if (push != null) pushReady = await push.start();
    api.onLoggedOut = () {
      live.stop();
      me = null;
      notifyListeners();
    };
    try {
      if (await api.hasRefreshToken()) {
        await withRetry(() async {
          if (await api.refreshTokens()) await reload();
        });
      }
    } catch (_) {
      // the server is away; the login screen shows, the token stays saved
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  Future<void> reload() async {
    me = Me.fromJson(await api.get('/me') as Map<String, dynamic>);
    live.start();
    notifyListeners();
    unawaited(_registerPush());
  }

  /// Tell the server where to send this person's pushes. Never blocks login.
  Future<void> _registerPush() async {
    final push = this.push;
    if (!pushReady || push == null) return;
    try {
      final token = await push.token();
      if (token == null) return; // notifications not allowed on this phone
      _pushToken = token;
      await api.post('/me/push-devices', {'token': token, 'platform': 'android'});
      _tokenSub ??= push.tokenChanges.listen((fresh) async {
        _pushToken = fresh;
        if (me != null) await api.post('/me/push-devices', {'token': fresh, 'platform': 'android'});
      });
    } catch (_) {
      // no network or push refused: alerts still come live and by email
    }
  }

  Future<void> setServer(String url) async {
    api.baseUrl = url;
    await api.store.writeServer(url);
    notifyListeners();
  }

  Future<LoginResult> login(String email, String password, {String? code}) async {
    try {
      final tokens = await api.json(
        'POST',
        '/auth/login',
        auth: false,
        body: {'email': email, 'password': password, if (code != null && code.isNotEmpty) 'code': code},
      );
      await api.saveTokens(Tokens.fromJson(tokens as Map<String, dynamic>));
      await reload();
      return LoginResult.ok;
    } on ApiError catch (error) {
      // The password was right; two-factor login is on (FR-70).
      if (error.status == 401 && (code == null || code.isEmpty) && error.message == 'Two-factor code required') {
        return LoginResult.needCode;
      }
      rethrow;
    }
  }

  Future<void> logout({bool everywhere = false}) async {
    final token = _pushToken;
    if (token != null) {
      try {
        await api.post('/me/push-devices/forget', {'token': token});
        await push?.deleteToken();
      } catch (_) {
        // the server forgets a phone by itself once Firebase says it is gone
      }
      _pushToken = null;
    }
    try {
      await api.post(everywhere ? '/auth/logout-all' : '/auth/logout');
    } catch (_) {
      // logged out anyway, below
    }
    live.stop();
    await api.forgetTokens();
    me = null;
    notifyListeners();
  }
}

/// Gives every screen the app state: `AppScope.of(context)`.
class AppScope extends InheritedNotifier<AppState> {
  const AppScope({super.key, required AppState state, required super.child}) : super(notifier: state);

  static AppState of(BuildContext context) => context.dependOnInheritedWidgetOfExactType<AppScope>()!.notifier!;

  /// Without listening for changes (in callbacks).
  static AppState read(BuildContext context) => context.getInheritedWidgetOfExactType<AppScope>()!.notifier!;
}
