// O-02: the homes list (FR-43): search by unit or resident, sort by any column.
// pH outside the safe range shows a warning icon and word, not colour only.
import 'package:flutter/material.dart';

import '../../api/owner_models.dart';
import '../../format.dart' as fmt;
import '../../widgets/common.dart';
import 'home_detail.dart';
import 'overview.dart';

enum HomeSort { unit, resident, today, month, valve, ph, temp, online, alerts }

const sortNames = {
  HomeSort.unit: 'Unit',
  HomeSort.resident: 'Resident',
  HomeSort.today: 'Litres today',
  HomeSort.month: 'Litres this month',
  HomeSort.valve: 'Valve',
  HomeSort.ph: 'pH',
  HomeSort.temp: 'Temperature',
  HomeSort.online: 'Online',
  HomeSort.alerts: 'Alerts',
};

/// Search and sort, kept apart from the screen so it is easy to test.
List<OverviewRow> searchAndSort(List<OverviewRow> rows, String query, HomeSort sort, bool ascending) {
  final q = query.trim().toLowerCase();
  final found = rows
      .where(
        (r) => q.isEmpty || r.unitNo.toLowerCase().contains(q) || r.residents.any((n) => n.toLowerCase().contains(q)),
      )
      .toList();
  Comparable key(OverviewRow r) => switch (sort) {
    HomeSort.unit => r.unitNo,
    HomeSort.resident => r.residentNames.toLowerCase(),
    HomeSort.today => r.litresToday,
    HomeSort.month => r.litresMonth,
    HomeSort.valve => r.valve ?? '',
    HomeSort.ph => r.ph ?? -1,
    HomeSort.temp => r.tempC ?? -100,
    HomeSort.online => r.online ? 1 : 0,
    HomeSort.alerts => r.openAlerts,
  };
  found.sort((a, b) {
    final c = key(a).compareTo(key(b));
    return ascending ? c : -c;
  });
  return found;
}

class HomesScreen extends StatefulWidget {
  const HomesScreen({super.key, required this.buildingId});
  final String buildingId;
  @override
  State<HomesScreen> createState() => _HomesScreenState();
}

class _HomesScreenState extends State<HomesScreen> {
  String _query = '';
  HomeSort _sort = HomeSort.unit;
  bool _ascending = true;

  @override
  Widget build(BuildContext context) {
    return OverviewBuilder(
      buildingId: widget.buildingId,
      builder: (context, o, reload) {
        final rows = searchAndSort(o.rows, _query, _sort, _ascending);
        return ScreenList(
          onRefresh: reload,
          children: [
            TextField(
              key: const Key('home-search'),
              decoration: const InputDecoration(prefixIcon: Icon(Icons.search), labelText: 'Search unit or resident'),
              onChanged: (v) => setState(() => _query = v),
            ),
            Row(
              children: [
                const Text('Sort by '),
                Expanded(
                  child: DropdownButton<HomeSort>(
                    key: const Key('home-sort'),
                    isExpanded: true,
                    value: _sort,
                    items: [for (final s in HomeSort.values) DropdownMenuItem(value: s, child: Text(sortNames[s]!))],
                    onChanged: (s) => setState(() => _sort = s ?? _sort),
                  ),
                ),
                IconButton(
                  tooltip: _ascending ? 'Low to high' : 'High to low',
                  icon: Icon(_ascending ? Icons.arrow_upward : Icons.arrow_downward),
                  onPressed: () => setState(() => _ascending = !_ascending),
                ),
              ],
            ),
            if (rows.isEmpty) const Padding(padding: EdgeInsets.all(12), child: Text('No home matches.')),
            for (final r in rows) _HomeRow(row: r, phLow: o.phSafeLow, phHigh: o.phSafeHigh, buildingId: o.buildingId),
          ],
        );
      },
    );
  }
}

class _HomeRow extends StatelessWidget {
  const _HomeRow({required this.row, required this.phLow, required this.phHigh, required this.buildingId});
  final OverviewRow row;
  final double phLow;
  final double phHigh;
  final String buildingId;

  @override
  Widget build(BuildContext context) {
    final quality = fmt.phLabel(row.ph, phLow, phHigh);
    final valve = row.locked != null ? '🔒 ${row.valve ?? '?'}' : (row.valve ?? 'no device');
    return Card(
      child: ListTile(
        title: Text('Unit ${row.unitNo} · ${row.residentNames}'),
        subtitle: Wrap(
          spacing: 10,
          runSpacing: 2,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text('Today ${fmt.litres(row.litresToday)}'),
            Text('Month ${fmt.litres(row.litresMonth)}'),
            Text('Valve $valve'),
            Text('pH ${fmt.number(row.ph)}'),
            if (row.ph != null) QualityBadge(quality),
            Text('${fmt.number(row.tempC)} °C'),
            if (row.deviceId != null)
              Text(
                row.online ? 'Online' : 'Offline (${fmt.ago(row.lastSeenAt)})',
                style: TextStyle(color: row.online ? null : Colors.orange.shade800),
              ),
            if (row.openAlerts > 0) Text('⚠ ${row.openAlerts} alert(s)'),
          ],
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) =>
                HomeDetailScreen(homeId: row.homeId, buildingId: buildingId, residentName: row.residentNames),
          ),
        ),
      ),
    );
  }
}
