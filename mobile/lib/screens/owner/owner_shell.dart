// The owner's app (FR-41 to FR-47 on Android): pick a building, then its
// overview, homes, tank and pump, bills and alerts. Residents, devices,
// tariffs and alert rules are managed in the web app.
import 'package:flutter/material.dart';

import '../../api/owner_models.dart';
import '../../app_state.dart';
import '../../widgets/common.dart';
import '../shared/alerts.dart';
import '../shared/bills.dart';
import '../shared/profile.dart';
import 'bills.dart';
import 'homes.dart';
import 'overview.dart';
import 'tank.dart';

class OwnerShell extends StatefulWidget {
  const OwnerShell({super.key});
  @override
  State<OwnerShell> createState() => _OwnerShellState();
}

class _OwnerShellState extends State<OwnerShell> {
  static const _tabs = ['overview', 'homes', 'tank', 'bills', 'alerts', 'profile'];
  static const _titles = ['Overview', 'Homes', 'Tank and pump', 'Bills', 'Alerts', 'Profile'];
  int _tab = 0;
  String? _buildingId;

  @override
  void initState() {
    super.initState();
    BillScreen.ownerActions = OwnerBillActions.build;
  }

  void _open(String tab) => setState(() => _tab = _tabs.indexOf(tab));

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Loader<List<Building>>(
      load: () async => [
        for (final b in await api.get('/buildings') as List) Building.fromJson(b as Map<String, dynamic>),
      ],
      builder: (context, buildings, _) {
        final id = buildings.any((b) => b.id == _buildingId)
            ? _buildingId
            : (buildings.isEmpty ? null : buildings.first.id);
        final none = const ScreenList(
          children: [
            Notice(kind: NoticeKind.info, text: 'You have no building yet. A System Admin creates it for you.'),
          ],
        );
        final body = id == null && _tab < 4
            ? none
            : switch (_tab) {
                0 => OverviewScreen(buildingId: id!, open: _open),
                1 => HomesScreen(buildingId: id!),
                2 => TankScreen(buildingId: id!),
                3 => OwnerBillsScreen(buildingId: id!),
                4 => const AlertsScreen(),
                _ => const ProfileScreen(),
              };
        return Scaffold(
          appBar: AppBar(
            title: buildings.length > 1 && _tab < 4
                ? DropdownButton<String>(
                    value: id,
                    underline: const SizedBox(),
                    items: [for (final b in buildings) DropdownMenuItem(value: b.id, child: Text(b.name))],
                    onChanged: (v) => setState(() => _buildingId = v),
                  )
                : Text(_titles[_tab]),
            actions: const [LiveDot()],
          ),
          drawer: NavigationDrawer(
            selectedIndex: _tab,
            onDestinationSelected: (i) {
              Navigator.pop(context);
              setState(() => _tab = i);
            },
            children: const [
              Padding(padding: EdgeInsets.all(16), child: Text('AquaPulse · owner')),
              NavigationDrawerDestination(icon: Icon(Icons.dashboard_outlined), label: Text('Overview')),
              NavigationDrawerDestination(icon: Icon(Icons.apartment), label: Text('Homes')),
              NavigationDrawerDestination(icon: Icon(Icons.propane_tank_outlined), label: Text('Tank and pump')),
              NavigationDrawerDestination(icon: Icon(Icons.receipt_long_outlined), label: Text('Bills')),
              NavigationDrawerDestination(icon: Icon(Icons.notifications_outlined), label: Text('Alerts')),
              NavigationDrawerDestination(icon: Icon(Icons.person_outline), label: Text('Profile')),
              Padding(
                padding: EdgeInsets.all(16),
                child: Text(
                  'Residents, devices, tariffs and alert rules: use the web app.',
                  style: TextStyle(fontSize: 12),
                ),
              ),
            ],
          ),
          body: KeyedSubtree(key: ValueKey('$_tab/$id'), child: body),
          bottomNavigationBar: NavigationBar(
            selectedIndex: const {0: 0, 1: 1, 2: 2, 3: 3}[_tab] ?? 0,
            onDestinationSelected: (i) => setState(() => _tab = i),
            destinations: const [
              NavigationDestination(icon: Icon(Icons.dashboard_outlined), label: 'Overview'),
              NavigationDestination(icon: Icon(Icons.apartment), label: 'Homes'),
              NavigationDestination(icon: Icon(Icons.propane_tank_outlined), label: 'Tank'),
              NavigationDestination(icon: Icon(Icons.receipt_long_outlined), label: 'Bills'),
            ],
          ),
        );
      },
    );
  }
}
