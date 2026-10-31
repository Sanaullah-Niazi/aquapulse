// AquaPulse Android app (M9, FR-41): one app for building owners and residents.
import 'package:flutter/material.dart';

import 'app_state.dart';
import 'screens/shared/login.dart';
import 'screens/shell.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final state = await AppState.create();
  runApp(AquaPulseApp(state: state));
  await state.init();
}

class AquaPulseApp extends StatelessWidget {
  const AquaPulseApp({super.key, required this.state});
  final AppState state;

  @override
  Widget build(BuildContext context) {
    const seed = Color(0xFF0B6E99);
    return AppScope(
      state: state,
      child: MaterialApp(
        title: 'AquaPulse',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(colorSchemeSeed: seed, useMaterial3: true),
        darkTheme: ThemeData(colorSchemeSeed: seed, brightness: Brightness.dark, useMaterial3: true),
        home: const _Start(),
      ),
    );
  }
}

class _Start extends StatelessWidget {
  const _Start();
  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    if (state.loading) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    return state.me == null ? const LoginScreen() : const Shell();
  }
}
