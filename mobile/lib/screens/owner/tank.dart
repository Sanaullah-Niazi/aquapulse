// O-04: the tank and pump (FR-47): a live gauge, pump state, automatic /
// manual mode (switching to manual asks first and is audited by the API),
// thresholds, a 7-day level chart and daily pump running time.
import 'dart:async';

import 'package:flutter/material.dart';

import '../../api/client.dart';
import '../../api/owner_models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/charts.dart';
import '../../widgets/common.dart';

class TankScreen extends StatefulWidget {
  const TankScreen({super.key, required this.buildingId});
  final String buildingId;
  @override
  State<TankScreen> createState() => _TankScreenState();
}

class _TankScreenState extends State<TankScreen> {
  Tank? _tank;
  bool _none = false;
  Object? _error;
  StreamSubscription? _sub;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_sub != null) return;
    _sub = AppScope.read(context).live.events.listen((e) {
      final tank = _tank;
      if (tank == null) return;
      if (e['buildingId'] != null && e['buildingId'] != widget.buildingId) return;
      if (e['type'] == 'tank') {
        setState(() => tank.applyLive(e));
      } else if (e['type'] == 'command' && e['homeId'] == null) {
        _load();
      }
    });
    _load();
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final json = await AppScope.read(context).api.get('/buildings/${widget.buildingId}/tank');
      if (!mounted) return;
      setState(() {
        _tank = json == null ? null : Tank.fromJson(json as Map<String, dynamic>);
        _none = json == null;
        _error = null;
      });
    } on ApiError catch (error) {
      if (!mounted) return;
      setState(() => error.status == 404 ? _none = true : _error = error);
    }
  }

  Future<void> _send(Future<dynamic> Function() call) async {
    try {
      await call();
      await _load();
    } on ApiError catch (error) {
      if (mounted) toast(context, error.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tank = _tank;
    if (_none) {
      return const ScreenList(
        children: [Notice(kind: NoticeKind.info, text: 'This building has no tank yet.')],
      );
    }
    if (tank == null) return _error == null ? const LoadingBox() : ErrorBox(error: _error, retry: _load);
    final api = AppScope.of(context).api;
    final r = tank.reading;
    final pct = r?.levelPct;
    final manual = tank.mode == 'manual';
    final on = r?.pumpOn ?? false;
    final waiting = tank.waiting.isNotEmpty;
    return ScreenList(
      onRefresh: _load,
      children: [
        if (!tank.online)
          Notice(kind: NoticeKind.warn, text: 'The tank device is offline (last seen ${fmt.ago(tank.lastSeenAt)}).'),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                SizedBox(
                  width: 90,
                  height: 90,
                  child: Stack(
                    alignment: Alignment.center,
                    children: [
                      SizedBox.expand(
                        child: CircularProgressIndicator(
                          value: pct == null ? 0 : pct / 100,
                          strokeWidth: 10,
                          semanticsLabel: 'Tank level',
                          semanticsValue: pct == null ? 'unknown' : '${pct.round()}%',
                        ),
                      ),
                      Text(pct == null ? '-' : '${pct.round()}%', style: Theme.of(context).textTheme.titleLarge),
                    ],
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${fmt.litres(r?.levelL)} of ${fmt.litres(tank.capacityL)}'),
                      Text('Pump: ${on ? 'ON' : 'off'}', style: const TextStyle(fontWeight: FontWeight.bold)),
                      Text('Mode: ${manual ? 'manual' : 'automatic'}'),
                      Text('Automatic fills from ${tank.lowPct.round()}% up to ${tank.highPct.round()}%'),
                      if (r != null && r.dryRunProtect) const Text('⚠ Dry-run protection on: the sump is empty'),
                      if (r != null && r.overflowProtect) const Text('⚠ Overflow protection on'),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        if (manual)
          FilledButton.icon(
            onPressed: !tank.online || waiting
                ? null
                : () async {
                    final ok = await confirm(
                      context,
                      on ? 'Turn the pump off?' : 'Turn the pump on?',
                      on ? 'The pump stops filling the tank.' : 'The pump starts filling the tank from the sump.',
                    );
                    if (ok) {
                      await _send(
                        () => api.post('/tanks/${tank.id}/pump', {'action': on ? 'off' : 'on', 'reason': ''}),
                      );
                    }
                  },
            icon: Icon(on ? Icons.stop : Icons.play_arrow),
            label: Text(on ? 'Turn pump off' : 'Turn pump on'),
          ),
        if (waiting)
          const Padding(padding: EdgeInsets.only(top: 8), child: Text('Waiting for the tank device to confirm…')),
        const SizedBox(height: 8),
        if (manual)
          OutlinedButton(
            onPressed: () => _send(() => api.patch('/tanks/${tank.id}/settings', {'mode': 'auto'})),
            child: const Text('Back to automatic'),
          )
        else
          OutlinedButton(
            onPressed: () async {
              final ok = await confirm(
                context,
                'Switch to manual?',
                'The pump will no longer start and stop by itself. You must watch the tank level. This is recorded in the audit log.',
                yes: 'Switch to manual',
                danger: true,
              );
              if (ok) await _send(() => api.patch('/tanks/${tank.id}/settings', {'mode': 'manual'}));
            },
            child: const Text('Switch to manual'),
          ),
        const Heading('Level, last 7 days'),
        Loader<TankHistory>(
          load: () async => TankHistory.fromJson(
            await api.get(
              '/tanks/${tank.id}/history?from=${fmt.isoDay(DateTime.now().subtract(const Duration(days: 6)))}',
            ) as Map<String, dynamic>,
          ),
          builder: (context, h, _) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              LineChartBox(points: h.levels, limits: [tank.lowPct, tank.highPct], unit: '%'),
              const Heading('Pump running time per day'),
              for (final d in h.days)
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text(fmt.date(d.$1)),
                  trailing: Text('${d.$2.toStringAsFixed(1)} h · ${d.$3.toStringAsFixed(1)} kWh'),
                ),
            ],
          ),
        ),
      ],
    );
  }
}
