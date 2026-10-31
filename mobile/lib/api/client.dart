// Talking to the AquaPulse API (FR-41).
//
// Logging in gives two tokens (FR-69):
// * the access token (15 minutes) is kept only in memory;
// * the refresh token (7 days, works once) is kept in the phone's secure
//   storage (Android Keystore), never in plain settings files. When the access
//   token runs out, we swap the refresh token for new ones and repeat the
//   request without you noticing.
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'token_store.dart';

/// An answer from the API that was not OK, with its message for people.
class ApiError implements Exception {
  ApiError(this.status, this.message, {this.retryAfter});

  /// The HTTP status (0 when the server could not be reached at all).
  final int status;
  final String message;
  final int? retryAfter;

  bool get temporary => status == 0 || status == 429 || status >= 500;

  @override
  String toString() => message;
}

/// The tokens the API gives at login and on refresh.
class Tokens {
  Tokens(this.access, this.refresh);
  factory Tokens.fromJson(Map<String, dynamic> json) =>
      Tokens(json['access_token'] as String, json['refresh_token'] as String);
  final String access;
  final String refresh;
}

class ApiClient {
  ApiClient({required this.client, required this.store, required this.baseUrl});

  /// Sends the requests. Tests give a fake one; the app gives one that also
  /// trusts the AquaPulse CA when it was built with it (see server.dart).
  http.Client client;
  final TokenStore store;

  /// The server, e.g. "https://localhost:8443" (no /api/v1, no trailing slash).
  String baseUrl;

  /// Called when the login is gone (refresh token refused): back to login.
  void Function() onLoggedOut = () {};

  String? _access;
  Future<bool>? _refreshing;

  Uri url(String path) => Uri.parse('$baseUrl/api/v1$path');

  Future<void> saveTokens(Tokens tokens) async {
    _access = tokens.access;
    await store.writeRefresh(tokens.refresh);
  }

  Future<void> forgetTokens() async {
    _access = null;
    await store.deleteRefresh();
  }

  Future<bool> hasRefreshToken() async => (await store.readRefresh()) != null;

  /// Swap the refresh token for new tokens, one at a time: a refresh token
  /// used twice ends the session (stolen-token protection, M3).
  Future<bool> refreshTokens() {
    return _refreshing ??= () async {
      try {
        final token = await store.readRefresh();
        if (token == null) return false;
        final response = await _send('POST', '/auth/refresh', body: {'refresh_token': token}, auth: false);
        if (response.statusCode == 429 || response.statusCode >= 500) {
          throw _errorFrom(response); // busy or down: the login is still good, keep it
        }
        if (response.statusCode != 200) {
          await forgetTokens();
          return false;
        }
        await saveTokens(Tokens.fromJson(jsonDecode(response.body) as Map<String, dynamic>));
        return true;
      } finally {
        _refreshing = null;
      }
    }();
  }

  Future<http.Response> _send(String method, String path, {Object? body, bool auth = true}) async {
    final request = http.Request(method, url(path));
    if (body != null) {
      request.headers['Content-Type'] = 'application/json';
      request.body = jsonEncode(body);
    }
    if (auth && _access != null) request.headers['Authorization'] = 'Bearer $_access';
    try {
      final streamed = await client.send(request).timeout(const Duration(seconds: 20));
      return await http.Response.fromStream(streamed);
    } on ApiError {
      rethrow;
    } catch (_) {
      throw ApiError(0, 'Cannot reach the server at $baseUrl. Check the address and your connection.');
    }
  }

  /// Is an AquaPulse server answering at [baseUrl]? Throws an [ApiError] if not.
  Future<void> checkServer() async {
    http.Response response;
    try {
      response = await client.get(Uri.parse('$baseUrl/api/healthz')).timeout(const Duration(seconds: 10));
    } catch (error) {
      throw ApiError(0, 'Cannot reach $baseUrl ($error)');
    }
    if (response.statusCode != 200) throw ApiError(response.statusCode, 'Error ${response.statusCode}');
  }

  /// Call the API and return its answer (or throw an [ApiError]).
  Future<http.Response> raw(String method, String path, {Object? body, bool auth = true}) async {
    if (auth && _access == null && await hasRefreshToken()) await refreshTokens();
    var response = await _send(method, path, body: body, auth: auth);
    if (response.statusCode == 401 && auth && await hasRefreshToken() && await refreshTokens()) {
      response = await _send(method, path, body: body, auth: auth); // the access token had run out
    }
    if (response.statusCode == 401 && auth) {
      await forgetTokens();
      onLoggedOut();
    }
    if (response.statusCode >= 400) throw _errorFrom(response);
    return response;
  }

  /// Call the API and return its JSON answer.
  Future<dynamic> json(String method, String path, {Object? body, bool auth = true}) async {
    final response = await raw(method, path, body: body, auth: auth);
    if (response.statusCode == 204 || response.body.isEmpty) return null;
    return jsonDecode(utf8.decode(response.bodyBytes));
  }

  Future<dynamic> get(String path) => json('GET', path);
  Future<dynamic> post(String path, [Object? body]) => json('POST', path, body: body ?? const {});
  Future<dynamic> patch(String path, Object body) => json('PATCH', path, body: body);

  /// A file the API makes (a PDF bill), with its file name.
  Future<(String, Uint8List)> file(String path, String fallbackName) async {
    final response = await raw('GET', path);
    final header = response.headers['content-disposition'] ?? '';
    final name = RegExp(r'filename="?([^";]+)"?').firstMatch(header)?.group(1) ?? fallbackName;
    return (name, response.bodyBytes);
  }
}

/// FastAPI answers {"detail": "text"} or, for bad input, a list of problems.
ApiError _errorFrom(http.Response response) {
  var message = 'Error ${response.statusCode}';
  try {
    final body = jsonDecode(utf8.decode(response.bodyBytes));
    final detail = body is Map ? body['detail'] : null;
    if (detail is String) {
      message = detail;
    } else if (detail is List) {
      message = detail
          .map((d) {
            final loc = (d['loc'] as List?)?.last ?? '';
            return '$loc: ${d['msg']}';
          })
          .join('; ');
    }
  } catch (_) {
    // not JSON: keep the status
  }
  if (response.statusCode == 429) message = 'Too many tries. Please wait a few minutes.';
  return ApiError(response.statusCode, message, retryAfter: int.tryParse(response.headers['retry-after'] ?? ''));
}

/// Run [call] again a few times while the API is busy (429), down (5xx) or
/// unreachable, waiting longer each time (or as long as the API asks).
Future<T> withRetry<T>(Future<T> Function() call, {int tries = 4, Duration first = const Duration(seconds: 2)}) async {
  for (var attempt = 1; ; attempt++) {
    try {
      return await call();
    } on ApiError catch (error) {
      if (!error.temporary || attempt >= tries) rethrow;
      final asked = error.retryAfter;
      var wait = asked != null && asked > 0 ? Duration(seconds: asked) : first * (1 << (attempt - 1));
      if (wait > const Duration(seconds: 30)) wait = const Duration(seconds: 30);
      await Future<void>.delayed(wait);
    }
  }
}
