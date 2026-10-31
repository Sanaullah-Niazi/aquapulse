// Where the app keeps what must survive a restart: the refresh token and the
// server address (FR-41). On the phone this is flutter_secure_storage, which
// encrypts with a key held by the Android Keystore. Tokens are never written
// to plain settings files (SharedPreferences): a test checks that.
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

abstract class TokenStore {
  Future<String?> readRefresh();
  Future<void> writeRefresh(String token);
  Future<void> deleteRefresh();
  Future<String?> readServer();
  Future<void> writeServer(String url);
}

class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage]) : _storage = storage ?? const FlutterSecureStorage();
  final FlutterSecureStorage _storage;
  static const _refresh = 'aquapulse.refresh';
  static const _server = 'aquapulse.server';

  @override
  Future<String?> readRefresh() => _storage.read(key: _refresh);
  @override
  Future<void> writeRefresh(String token) => _storage.write(key: _refresh, value: token);
  @override
  Future<void> deleteRefresh() => _storage.delete(key: _refresh);
  @override
  Future<String?> readServer() => _storage.read(key: _server);
  @override
  Future<void> writeServer(String url) => _storage.write(key: _server, value: url);
}

/// Kept in memory only: used by the tests.
class MemoryTokenStore implements TokenStore {
  String? refresh;
  String? server;
  @override
  Future<String?> readRefresh() async => refresh;
  @override
  Future<void> writeRefresh(String token) async => refresh = token;
  @override
  Future<void> deleteRefresh() async => refresh = null;
  @override
  Future<String?> readServer() async => server;
  @override
  Future<void> writeServer(String url) async => server = url;
}
