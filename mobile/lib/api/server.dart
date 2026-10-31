// Which AquaPulse server the app talks to, and which certificates it trusts.
//
// The app can point at any AquaPulse server (FR-41): your Mac
// (https://localhost:8443, see the M9 guide for "adb reverse"), a Cloudflare
// tunnel or a cloud server. Public servers have normal certificates that
// Android already trusts. Your Mac uses the project's own CA (M5), so
// `make apk` copies that CA into the app (assets/certs/, git-ignored) and the
// app trusts it too, in addition to Android's normal list. Nothing else is
// trusted: there is no "ignore certificate errors" switch.
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

const defaultServer = 'https://localhost:8443';

/// Clean up what someone typed: add https://, drop a trailing slash or /api/v1.
/// Returns null if it is not a usable address. Only HTTPS is allowed (FR-71).
String? normaliseServer(String input) {
  var text = input.trim();
  if (text.isEmpty) return null;
  if (!text.contains('://')) text = 'https://$text';
  text = text.replaceAll(RegExp(r'/+$'), '');
  text = text.replaceAll(RegExp(r'/api(/v1)?$'), '');
  final uri = Uri.tryParse(text);
  if (uri == null || uri.host.isEmpty || uri.scheme != 'https') return null;
  return text;
}

/// The certificates the app trusts: Android's own, plus the local AquaPulse
/// CA if `make apk` put one in assets/certs/.
class ServerTrust {
  ServerTrust._(this.context, this.localCa);
  final SecurityContext context;

  /// The file name of the local CA, or null (the GitHub build has none).
  final String? localCa;

  static Future<ServerTrust> load([AssetBundle? bundle]) async {
    final assets = bundle ?? rootBundle;
    final context = SecurityContext(withTrustedRoots: true);
    String? found;
    try {
      final manifest = await AssetManifest.loadFromAssetBundle(assets);
      for (final name in manifest.listAssets()) {
        if (name.startsWith('assets/certs/') && (name.endsWith('.crt') || name.endsWith('.pem'))) {
          final bytes = await assets.load(name);
          context.setTrustedCertificatesBytes(bytes.buffer.asUint8List());
          found = name;
        }
      }
    } catch (_) {
      // no assets (tests) or a broken file: Android's normal list still works
    }
    return ServerTrust._(context, found);
  }

  HttpClient ioClient() => HttpClient(context: context)..connectionTimeout = const Duration(seconds: 10);

  http.Client httpClient() => IOClient(ioClient());
}
