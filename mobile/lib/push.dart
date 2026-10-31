// Push notifications (M9, FR-60) through Firebase Cloud Messaging (free).
//
// Optional: the app only gets push when it was built with your Firebase
// project's google-services.json (see docs/milestones/M9 part 3). Then, after
// login, the app asks Android for permission to show notifications, gets this
// phone's push token from Firebase and sends it to the AquaPulse server. The
// server sends alerts to it, by each person's push choice. On logout the
// token is removed from the server and from the phone.
//
// When the app is closed or in the background, Android shows the
// notification by itself. When the app is open, the alert already arrives
// over the live connection.
import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

abstract class PushService {
  /// Start Firebase. False when this build has no Firebase settings.
  Future<bool> start();

  /// Ask to show notifications (Android 13+) and return this phone's token.
  Future<String?> token();

  /// Firebase sometimes gives the phone a new token.
  Stream<String> get tokenChanges;

  /// Forget the token on this phone (after logout).
  Future<void> deleteToken();
}

class FirebasePush implements PushService {
  bool _ready = false;

  @override
  Future<bool> start() async {
    try {
      await Firebase.initializeApp(); // reads google-services.json, if it was built in
      _ready = true;
    } catch (_) {
      _ready = false; // no Firebase settings in this build: push is off
    }
    return _ready;
  }

  @override
  Future<String?> token() async {
    if (!_ready) return null;
    final messaging = FirebaseMessaging.instance;
    final permission = await messaging.requestPermission();
    if (permission.authorizationStatus == AuthorizationStatus.denied) return null;
    // ignore: deprecated_member_use
    return messaging.getToken();
  }

  @override
  // ignore: deprecated_member_use
  Stream<String> get tokenChanges => _ready ? FirebaseMessaging.instance.onTokenRefresh : const Stream.empty();

  @override
  Future<void> deleteToken() async {
    // ignore: deprecated_member_use
    if (_ready) await FirebaseMessaging.instance.deleteToken();
  }
}

/// For tests: a pretend Firebase.
class FakePush implements PushService {
  FakePush({this.available = true, this.value = 'fake-push-token-0123456789abcdef'});
  bool available;
  String? value;
  bool deleted = false;
  final changes = StreamController<String>.broadcast();

  @override
  Future<bool> start() async => available;
  @override
  Future<String?> token() async => available ? value : null;
  @override
  Stream<String> get tokenChanges => changes.stream;
  @override
  Future<void> deleteToken() async => deleted = true;
}
