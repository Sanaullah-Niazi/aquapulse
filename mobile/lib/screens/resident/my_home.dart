// R-01: my home at a glance (FR-48), with the water on/off button (FR-51).
// Everything updates live, without pulling down.
import 'package:flutter/material.dart';

import '../../api/models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/alert_list.dart';
import '../../widgets/common.dart';
import '../../widgets/valve_control.dart';
import '../shared/home_live.dart';

/// A resident with more than one home picks which one to see.
class HomePicker extends StatelessWidget {
  const HomePicker({super.key, required this.homeId, required this.onChanged});
  final String homeId;
  final ValueChanged<String> onChanged;
  @override
  Widget build(BuildContext context) {
    final homes = AppScope.of(context).me!.homeIds;
    if (homes.length < 2) return const SizedBox.shrink();
    return DropdownButton<String>(
      value: homeId,
      items: [for (var i = 0; i < homes.length; i++) DropdownMenuItem(value: homes[i], child: Text('Home ${i + 1}'))],
      onChanged: (id) => id == null ? null : onChanged(id),
    );
  }
}

class MyHomeScreen extends StatelessWidget {
  const MyHomeScreen({super.key, required this.homeId, required this.onPickHome, required this.openAlerts});
  final String? homeId;
  final ValueChanged<String> onPickHome;
  final VoidCallback openAlerts;

  @override
  Widget build(BuildContext context) {
    final id = homeId;
    if (id == null) {
      return const ScreenList(
        children: [
          Notice(
            kind: NoticeKind.info,
            text: 'You are not linked to a home yet. Ask your building owner to invite you.',
          ),
        ],
      );
    }
    final api = AppScope.of(context).api;
    return HomeLiveBuilder(
      homeId: id,
      builder: (context, live, reload) {
        final reading = live.reading;
        final quality = fmt.phLabel(reading?.ph, live.phSafeLow, live.phSafeHigh);
        final running = (reading?.flowLpm ?? 0) > 0.05;
        final first = fmt.isoDay(DateTime(DateTime.now().year, DateTime.now().month));
        return ScreenList(
          onRefresh: reload,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Unit ${live.unitNo} · ${live.buildingName}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                HomePicker(homeId: id, onChanged: onPickHome),
              ],
            ),
            if (live.deviceId != null && !live.online)
              Notice(
                kind: NoticeKind.warn,
                text: 'Your water device is offline (last seen ${fmt.ago(live.lastSeenAt)}).',
              ),
            // Today and this month in one call: daily totals since the 1st (building time).
            Loader<Consumption>(
              keyValue: id,
              load: () async => Consumption.fromJson(
                await api.get('/homes/$id/consumption?granularity=day&from=$first') as Map<String, dynamic>,
              ),
              reloadWhen: (e) => e['type'] == 'telemetry' && e['homeId'] == id,
              builder: (context, usage, _) {
                final last = usage.points.isEmpty ? null : usage.points.last;
                final today = last != null && last.start.substring(0, 10) == usage.to ? last.value : 0.0;
                return StatGrid(
                  children: [
                    StatCard(label: 'Today', icon: Icons.today, value: Text(fmt.litres(today))),
                    StatCard(
                      label: 'This month',
                      icon: Icons.calendar_month,
                      value: Text(fmt.litres(usage.totalLitres)),
                    ),
                  ],
                );
              },
            ),
            StatGrid(
              children: [
                StatCard(
                  label: 'Water right now',
                  icon: Icons.waves,
                  value: Text(
                    running ? 'Running: ${fmt.number(reading?.flowLpm)} L/min' : 'Not running',
                    style: const TextStyle(fontSize: 16),
                  ),
                ),
                StatCard(
                  label: 'Water quality',
                  icon: Icons.science_outlined,
                  value: Text('pH ${fmt.number(reading?.ph)}'),
                  sub: QualityBadge(quality),
                ),
                StatCard(
                  label: 'Temperature',
                  icon: Icons.thermostat,
                  value: Text(reading == null ? '-' : '${fmt.number(reading.tempC)} °C'),
                ),
                Loader<BillEstimate>(
                  keyValue: id,
                  load: () async =>
                      BillEstimate.fromJson(await api.get('/homes/$id/bill-estimate') as Map<String, dynamic>),
                  builder: (context, estimate, _) => StatCard(
                    label: 'Bill so far',
                    icon: Icons.receipt_long,
                    value: Text(estimate.totalText ?? '-'),
                    sub: Text(estimate.message),
                  ),
                ),
              ],
            ),
            const Heading('Water on / off'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: ValveControl(live: live, unit: live.unitNo, onChanged: reload),
              ),
            ),
            Heading(
              'Latest alerts',
              trailing: TextButton(onPressed: openAlerts, child: const Text('See all')),
            ),
            Loader<List<Alert>>(
              keyValue: id,
              load: () async => [
                for (final a in await api.get('/alerts?home_id=$id&limit=3') as List)
                  Alert.fromJson(a as Map<String, dynamic>),
              ],
              reloadWhen: (e) => e['type'] == 'alert',
              builder: (context, alerts, _) => alerts.isEmpty
                  ? const Text('No alerts. All good.')
                  : Column(children: [for (final a in alerts) AlertTile(alert: a)]),
            ),
          ],
        );
      },
    );
  }
}
