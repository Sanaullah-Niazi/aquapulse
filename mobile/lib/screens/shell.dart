// After login, the server role decides which screens appear (FR-41): a
// resident never sees owner screens, and the API refuses owner calls from a
// resident token anyway (403).
import 'package:flutter/material.dart';

import '../app_state.dart';
import '../widgets/common.dart';
import 'owner/owner_shell.dart';
import 'resident/my_home.dart';
import 'resident/quality.dart';
import 'resident/usage.dart';
import 'shared/ai.dart';
import 'shared/alerts.dart';
import 'shared/bills.dart';
import 'shared/profile.dart';

class Shell extends StatelessWidget {
  const Shell({super.key});
  @override
  Widget build(BuildContext context) {
    final me = AppScope.of(context).me!;
    if (me.isResident) return const ResidentShell();
    if (me.isOwner) return const OwnerShell();
    // System Admins use the web console (M8); the app is for owners and residents.
    return Scaffold(
      appBar: AppBar(title: const Text('AquaPulse')),
      body: ScreenList(
        children: [
          const Notice(
            kind: NoticeKind.info,
            text: 'System Admins use the web console (https://your-server/app/). This app is for building owners and residents.',
          ),
          FilledButton(onPressed: () => AppScope.read(context).logout(), child: const Text('Log out')),
        ],
      ),
    );
  }
}

class ResidentShell extends StatefulWidget {
  const ResidentShell({super.key});
  @override
  State<ResidentShell> createState() => _ResidentShellState();
}

class _ResidentShellState extends State<ResidentShell> {
  int _tab = 0;
  String? _homeId;

  static const _titles = ['My home', 'My usage', 'Water quality', 'My bills', 'Alerts', 'Profile', 'AI insights'];

  @override
  Widget build(BuildContext context) {
    final homes = AppScope.of(context).me!.homeIds;
    final homeId = _homeId != null && homes.contains(_homeId) ? _homeId : (homes.isEmpty ? null : homes.first);
    void openTab(int i) => setState(() => _tab = i);
    final noHome = const ScreenList(
      children: [
        Notice(kind: NoticeKind.info, text: 'You are not linked to a home yet. Ask your building owner to invite you.'),
      ],
    );
    final body = switch (_tab) {
      0 => MyHomeScreen(homeId: homeId, onPickHome: (id) => setState(() => _homeId = id), openAlerts: () => openTab(4)),
      1 => homeId == null ? noHome : UsageScreen(homeId: homeId),
      2 => homeId == null ? noHome : QualityScreen(homeId: homeId),
      3 => const MyBillsScreen(),
      4 => const AlertsScreen(),
      6 => homeId == null ? noHome : AiScreen(homeId: homeId),
      _ => const ProfileScreen(),
    };
    // Seven places do not fit in a bottom bar: four there, all seven in the menu.
    return Scaffold(
      appBar: AppBar(title: Text(_titles[_tab]), actions: const [LiveDot()]),
      drawer: NavigationDrawer(
        selectedIndex: _tab,
        onDestinationSelected: (i) {
          Navigator.pop(context);
          openTab(i);
        },
        children: const [
          Padding(padding: EdgeInsets.all(16), child: Text('AquaPulse')),
          NavigationDrawerDestination(icon: Icon(Icons.home_outlined), label: Text('My home')),
          NavigationDrawerDestination(icon: Icon(Icons.bar_chart), label: Text('My usage')),
          NavigationDrawerDestination(icon: Icon(Icons.science_outlined), label: Text('Water quality')),
          NavigationDrawerDestination(icon: Icon(Icons.receipt_long_outlined), label: Text('My bills')),
          NavigationDrawerDestination(icon: Icon(Icons.notifications_outlined), label: Text('Alerts')),
          NavigationDrawerDestination(icon: Icon(Icons.person_outline), label: Text('Profile')),
          NavigationDrawerDestination(icon: Icon(Icons.auto_awesome_outlined), label: Text('AI insights')),
        ],
      ),
      body: KeyedSubtree(key: ValueKey('$_tab/$homeId'), child: body),
      bottomNavigationBar: NavigationBar(
        selectedIndex: const {0: 0, 1: 1, 3: 2, 5: 3}[_tab] ?? 0,
        onDestinationSelected: (i) => openTab(const [0, 1, 3, 5][i]),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.bar_chart), label: 'Usage'),
          NavigationDestination(icon: Icon(Icons.receipt_long_outlined), label: 'Bills'),
          NavigationDestination(icon: Icon(Icons.person_outline), label: 'Profile'),
        ],
      ),
    );
  }
}
