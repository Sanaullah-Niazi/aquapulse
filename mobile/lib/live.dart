// The live connection (FR-25): one WebSocket for the whole app. Readings,
// valve answers, alerts and bill changes arrive the moment they happen, so
// screens update without pulling down to refresh. If the connection drops
// (Wi-Fi, server restart), it comes back by itself, waiting a bit longer each
// time (1 s, 2 s, 4 s ... at most 30 s).
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import 'api/client.dart';

typedef LiveEvent = Map<String, dynamic>;

enum LiveStatus { off, connecting, live }

class Live {
  Live(this.api, {this.httpClient});

  final ApiClient api;

  /// Makes the HttpClient for the WebSocket (so it trusts the same CA as the
  /// API calls). Null turns the live connection off (the tests do that).
  final HttpClient Function()? httpClient;

  final _events = StreamController<LiveEvent>.broadcast();
  final status = ValueNotifier(LiveStatus.off);
  WebSocket? _socket;
  bool _wanted = false;
  int _retry = 0;
  Timer? _timer;

  Stream<LiveEvent> get events => _events.stream;

  /// For tests: pretend an event arrived.
  void inject(LiveEvent event) => _events.add(event);

  void start() {
    _wanted = true;
    _connect();
  }

  void stop() {
    _wanted = false;
    _timer?.cancel();
    _socket?.close();
    _socket = null;
    status.value = LiveStatus.off;
  }

  Future<void> _connect() async {
    final makeClient = httpClient;
    if (!_wanted || _socket != null || makeClient == null) return;
    status.value = LiveStatus.connecting;
    try {
      // A one-time ticket (30 seconds), so the token never goes into a URL.
      final answer = await api.post('/ws-ticket') as Map<String, dynamic>;
      if (!_wanted) return;
      final ticket = Uri.encodeQueryComponent(answer['ticket'] as String);
      final url = '${api.baseUrl.replaceFirst(RegExp('^http'), 'ws')}/api/v1/ws?ticket=$ticket';
      final socket = await WebSocket.connect(url, customClient: makeClient());
      socket.pingInterval = const Duration(seconds: 25);
      _socket = socket;
      socket.listen(
        (message) {
          final event = jsonDecode(message as String) as LiveEvent;
          if (event['type'] == 'ready') {
            _retry = 0;
            status.value = LiveStatus.live;
          }
          _events.add(event);
        },
        onDone: _closed,
        onError: (_) => _closed(),
        cancelOnError: true,
      );
    } catch (_) {
      _scheduleReconnect();
    }
  }

  void _closed() {
    _socket = null;
    status.value = _wanted ? LiveStatus.connecting : LiveStatus.off;
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (!_wanted) return;
    _timer?.cancel();
    final seconds = 1 << (_retry < 5 ? _retry : 5);
    _retry++;
    _timer = Timer(Duration(seconds: seconds > 30 ? 30 : seconds), _connect);
  }
}
