// The first screens: which server, then email and password, then (only if
// two-factor login is on) the 6-digit code from the authenticator app (FR-70).
import 'package:flutter/material.dart';

import '../../api/client.dart';
import '../../api/server.dart';
import '../../app_state.dart';
import '../../widgets/common.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});
  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _code = TextEditingController();
  bool _needCode = false;
  bool _busy = false;
  bool _show = false;
  Object? _error;

  Future<void> _login() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await AppScope.read(context)
          .login(_email.text.trim(), _password.text, code: _needCode ? _code.text.trim() : null);
      if (result == LoginResult.needCode && mounted) setState(() => _needCode = true);
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: ListView(
              shrinkWrap: true,
              padding: const EdgeInsets.all(20),
              children: [
                Icon(Icons.water_drop, size: 56, color: Theme.of(context).colorScheme.primary),
                Text('AquaPulse', textAlign: TextAlign.center, style: Theme.of(context).textTheme.headlineMedium),
                const SizedBox(height: 4),
                const Text('Smart water for your building', textAlign: TextAlign.center),
                const SizedBox(height: 20),
                if (!_needCode) ...[
                  TextField(
                    key: const Key('email'),
                    controller: _email,
                    keyboardType: TextInputType.emailAddress,
                    autofillHints: const [AutofillHints.email],
                    decoration: const InputDecoration(labelText: 'Email'),
                  ),
                  TextField(
                    key: const Key('password'),
                    controller: _password,
                    obscureText: !_show,
                    autofillHints: const [AutofillHints.password],
                    decoration: InputDecoration(
                      labelText: 'Password',
                      suffixIcon: IconButton(
                        tooltip: _show ? 'Hide password' : 'Show password',
                        icon: Icon(_show ? Icons.visibility_off : Icons.visibility),
                        onPressed: () => setState(() => _show = !_show),
                      ),
                    ),
                    onSubmitted: (_) => _login(),
                  ),
                ] else ...[
                  const Text(
                    'Two-factor login is on. Type the 6-digit code from your authenticator app, or one of your recovery codes.',
                  ),
                  TextField(
                    key: const Key('code'),
                    controller: _code,
                    autofocus: true,
                    keyboardType: TextInputType.number,
                    autofillHints: const [AutofillHints.oneTimeCode],
                    decoration: const InputDecoration(labelText: 'Code'),
                    onSubmitted: (_) => _login(),
                  ),
                ],
                const SizedBox(height: 16),
                if (_error != null) ErrorBox(error: _error),
                FilledButton(
                  key: const Key('login'),
                  onPressed: _busy ? null : _login,
                  child: Text(_busy ? 'Logging in…' : 'Log in'),
                ),
                if (_needCode)
                  TextButton(onPressed: () => setState(() => _needCode = false), child: const Text('Back')),
                const SizedBox(height: 16),
                ListTile(
                  leading: const Icon(Icons.dns_outlined),
                  title: const Text('Server'),
                  subtitle: Text(state.api.baseUrl),
                  trailing: const Icon(Icons.edit),
                  onTap: () =>
                      Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => const ServerScreen())),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Choose the AquaPulse server (FR-41): your Mac, a tunnel or a cloud server.
class ServerScreen extends StatefulWidget {
  const ServerScreen({super.key});
  @override
  State<ServerScreen> createState() => _ServerScreenState();
}

class _ServerScreenState extends State<ServerScreen> {
  late final _url = TextEditingController(text: AppScope.read(context).api.baseUrl);
  String? _message;
  bool _busy = false;

  Future<void> _save() async {
    final url = normaliseServer(_url.text);
    if (url == null) {
      setState(() => _message = 'Type an https:// address, like https://water.example.com');
      return;
    }
    setState(() => _busy = true);
    final state = AppScope.read(context);
    final old = state.api.baseUrl;
    state.api.baseUrl = url;
    try {
      // Check that an AquaPulse server answers there before saving it.
      await state.api.checkServer();
      await state.setServer(url);
      if (mounted) Navigator.pop(context);
    } on ApiError catch (error) {
      state.api.baseUrl = old;
      if (mounted) setState(() => _message = 'No AquaPulse server answered: ${error.message}');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ca = AppScope.of(context).localCa;
    return Scaffold(
      appBar: AppBar(title: const Text('Server')),
      body: ScreenList(
        children: [
          TextField(
            key: const Key('server'),
            controller: _url,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(labelText: 'Server address', hintText: 'https://localhost:8443'),
          ),
          const SizedBox(height: 8),
          const Text(
            'Your Mac: https://localhost:8443 (first run "adb reverse tcp:8443 tcp:8443", see the M9 guide). '
            'A tunnel or cloud server: its https:// address.',
          ),
          const SizedBox(height: 8),
          Text(
            ca == null
                ? 'This app trusts the normal public certificates only.'
                : 'This app also trusts your own AquaPulse certificate (added by make apk).',
          ),
          const SizedBox(height: 12),
          if (_message != null) Notice(kind: NoticeKind.bad, text: _message!),
          FilledButton(onPressed: _busy ? null : _save, child: Text(_busy ? 'Checking…' : 'Check and save')),
        ],
      ),
    );
  }
}
