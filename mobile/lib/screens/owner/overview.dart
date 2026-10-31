// O-01: the building overview (FR-42). New readings change the numbers on the
// spot; alerts, valve answers, locks, devices and bills load it again. The
// litres are also added up again every minute.
import 'dart:async';

import 'package:flutter/material.dart';

import '../../api/owner_models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/common.dart';

/// Loads one building's overview and keeps it live.
class OverviewBuilder extends StatefulWidget {
  const OverviewBuilder({super.key, required this.buildingId, required this.builder});
  final String buildingId;
  final Widget Function(BuildContext context, Overview overview, Future<void> Function() reload) builder;
  @override
  State<OverviewBuilder> createState() => _OverviewBuilderState();
}

class _OverviewBuilderState extends State<OverviewBuilder> {
  static const _reloadOn = {'alert', 'command', 'lock', 'device', 'bill'};
  Overview? _data;
  Object? _error;
  StreamSubscription? _sub;
  Timer? _timer;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_sub != null) return;
    _sub = AppScope.read(context).live.events.listen((e) {
      final data = _data;
      if (data == null) return;
      if (e['type'] == 'telemetry') {
        setState(() => data.applyTelemetry(e));
      } else if (e['type'] == 'tank' && (e['buildingId'] == null || e['buildingId'] == widget.buildingId)) {
        setState(() => data.applyTank(e));
      } else if (_reloadOn.contains(e['type'])) {
        _load();
      }
    });
    _timer = Timer.periodic(const Duration(minutes: 1), (_) => _load());
    _load();
  }

  @override
  void didUpdateWidget(covariant OverviewBuilder old) {
    super.didUpdateWidget(old);
    if (old.buildingId != widget.buildingId) {
      _data = null;
      _load();
    }
  }

  @override
  void dispose() {
    _sub?.cancel();
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final id = widget.buildingId;
    try {
      final json = await AppScope.read(context).api.get('/buildings/$id/overview') as Map<String, dynamic>;
      if (!mounted || id != widget.buildingId) return;
      setState(() {
        _data = Overview.fromJson(json);
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    if (data != null) return widget.builder(context, data, _load);
    if (_error != null) return ErrorBox(error: _error, retry: _load);
    return const LoadingBox();
  }
}

class OverviewScreen extends StatelessWidget {
  const OverviewScreen({super.key, required this.buildingId, required this.open});
  final String buildingId;

  /// Opens another tab: 'homes', 'tank', 'bills' or 'alerts'.
  final ValueChanged<String> open;

  @override
  Widget build(BuildContext context) {
    return OverviewBuilder(
      buildingId: buildingId,
      builder: (context, o, reload) {
        final change = o.litresLastMonthToDate > 0 ? (o.litresMonth / o.litresLastMonthToDate - 1) * 100 : null;
        final critical = o.alerts['critical'] ?? 0;
        final warning = o.alerts['warning'] ?? 0;
        final info = o.alerts['info'] ?? 0;
        final tank = o.tank;
        final offline = o.rows.where((r) => r.deviceId != null && !r.online).toList();
        return ScreenList(
          onRefresh: reload,
          children: [
            Text(o.name, style: Theme.of(context).textTheme.titleLarge),
            StatGrid(
              children: [
                StatCard(label: 'Today', icon: Icons.today, value: Text(fmt.litres(o.litresToday))),
                StatCard(
                  label: 'This month',
                  icon: Icons.calendar_month,
                  value: Text(fmt.litres(o.litresMonth)),
                  sub: Text(
                    change == null
                        ? 'No data for last month'
                        : '${change >= 0 ? '+' : ''}${change.toStringAsFixed(0)}% vs same days last month',
                  ),
                ),
                StatCard(label: 'Water flowing now', icon: Icons.waves, value: Text('${fmt.number(o.flowLpm)} L/min')),
                StatCard(
                  label: 'Homes online',
                  icon: Icons.wifi,
                  value: Text('${o.homesOnline} / ${o.homesWithDevice}'),
                  sub: Text('${o.homes} homes'),
                ),
                StatCard(
                  label: 'Valves',
                  icon: Icons.water_drop_outlined,
                  value: Text('${o.valvesClosed} closed'),
                  sub: Text('${o.valvesLocked} locked'),
                ),
                InkWell(
                  onTap: () => open('alerts'),
                  child: StatCard(
                    label: 'Open alerts',
                    icon: Icons.notifications_active_outlined,
                    value: Text('${critical + warning + info}'),
                    sub: Text('$critical critical · $warning warning · $info info'),
                  ),
                ),
                InkWell(
                  onTap: () => open('tank'),
                  child: StatCard(
                    label: 'Tank',
                    icon: Icons.propane_tank_outlined,
                    value: Text(tank?.levelPct == null ? '-' : '${tank!.levelPct!.toStringAsFixed(0)}%'),
                    sub: Text(
                      tank == null
                          ? 'No tank'
                          : 'Pump ${tank.pumpOn == true ? 'ON' : 'off'} · ${tank.mode == 'manual' ? 'manual' : 'automatic'}'
                                '${tank.online ? '' : ' · offline'}',
                    ),
                  ),
                ),
                InkWell(
                  onTap: () => open('bills'),
                  child: StatCard(
                    label: 'Unpaid bills',
                    icon: Icons.receipt_long,
                    value: Text(o.unpaidText.isEmpty ? '-' : o.unpaidText, style: const TextStyle(fontSize: 16)),
                    sub: Text('${o.unpaidBills} bills'),
                  ),
                ),
              ],
            ),
            if (critical > 0)
              Notice(
                kind: NoticeKind.bad,
                text: '$critical critical alert(s) need attention.',
                action: TextButton(onPressed: () => open('alerts'), child: const Text('See alerts')),
              ),
            if (offline.isNotEmpty)
              Notice(
                kind: NoticeKind.warn,
                text: 'Offline: ${offline.map((r) => 'unit ${r.unitNo}').join(', ')}.',
                action: TextButton(onPressed: () => open('homes'), child: const Text('See homes')),
              ),
          ],
        );
      },
    );
  }
}
