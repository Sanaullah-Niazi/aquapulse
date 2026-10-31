// O-03: one home (FR-43, FR-44): live readings, the valve with the resident's
// name in the confirm step, lock / unlock with a reason (at least 10
// characters), hourly / daily / monthly use, the home's bills and alerts.
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api/client.dart';
import '../../api/models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/alert_list.dart';
import '../../widgets/charts.dart';
import '../../widgets/common.dart';
import '../../widgets/valve_control.dart';
import '../shared/bills.dart';
import '../shared/home_live.dart';

class HomeDetailScreen extends StatelessWidget {
  const HomeDetailScreen({super.key, required this.homeId, required this.buildingId, required this.residentName});
  final String homeId;
  final String buildingId;
  final String residentName;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Scaffold(
      appBar: AppBar(title: const Text('Home'), actions: const [LiveDot()]),
      body: HomeLiveBuilder(
        homeId: homeId,
        builder: (context, live, reload) {
          final r = live.reading;
          final quality = fmt.phLabel(r?.ph, live.phSafeLow, live.phSafeHigh);
          return ScreenList(
            onRefresh: reload,
            children: [
              Text('Unit ${live.unitNo} · $residentName', style: Theme.of(context).textTheme.titleLarge),
              if (live.deviceId != null && !live.online)
                Notice(kind: NoticeKind.warn, text: 'Device offline, last seen ${fmt.ago(live.lastSeenAt)}.'),
              StatGrid(
                children: [
                  StatCard(label: 'Flow now', value: Text('${fmt.number(r?.flowLpm)} L/min')),
                  StatCard(label: 'pH', value: Text(fmt.number(r?.ph)), sub: QualityBadge(quality)),
                  StatCard(label: 'Temperature', value: Text(r == null ? '-' : '${fmt.number(r.tempC)} °C')),
                  StatCard(label: 'Meter', value: Text(fmt.litres(r?.totalL))),
                ],
              ),
              const Heading('Valve'),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      ValveControl(live: live, unit: live.unitNo, residentName: residentName, onChanged: reload),
                      if (live.deviceId != null) LockControl(live: live, onChanged: reload),
                    ],
                  ),
                ),
              ),
              UsageCard(homeId: homeId),
              const Heading('Bills'),
              Loader<List<BillSummary>>(
                load: () async => [
                  for (final b in await api.get('/buildings/$buildingId/bills') as List)
                    if (b['home_id'] == homeId) BillSummary.fromJson(b as Map<String, dynamic>),
                ],
                reloadWhen: billEvent,
                builder: (context, bills, _) => bills.isEmpty
                    ? const Text('No bills yet.')
                    : Column(children: [for (final b in bills.take(6)) BillTile(bill: b)]),
              ),
              const Heading('Alerts'),
              Loader<List<Alert>>(
                load: () async => [
                  for (final a in await api.get('/alerts?home_id=$homeId&limit=5') as List)
                    Alert.fromJson(a as Map<String, dynamic>),
                ],
                reloadWhen: (e) => e['type'] == 'alert',
                builder: (context, alerts, _) => alerts.isEmpty
                    ? const Text('No alerts.')
                    : Column(children: [for (final a in alerts) AlertTile(alert: a)]),
              ),
            ],
          );
        },
      ),
    );
  }
}

/// FR-27 / FR-44: lock the valve open or closed with a reason the residents see.
class LockControl extends StatefulWidget {
  const LockControl({super.key, required this.live, required this.onChanged});
  final HomeLive live;
  final VoidCallback onChanged;
  @override
  State<LockControl> createState() => _LockControlState();
}

class _LockControlState extends State<LockControl> {
  bool _open = false;
  String _state = 'closed';
  final _reason = TextEditingController();
  Object? _error;
  bool _busy = false;

  Future<void> _submit() async {
    final live = widget.live;
    setState(() => _busy = true);
    try {
      final api = AppScope.read(context).api;
      if (live.lock != null) {
        await api.post('/homes/${live.homeId}/valve-unlock', {'reason': _reason.text.trim()});
      } else {
        await api.post('/homes/${live.homeId}/valve-lock', {'state': _state, 'reason': _reason.text.trim()});
      }
      if (!mounted) return;
      setState(() {
        _open = false;
        _error = null;
        _reason.clear();
      });
      widget.onChanged();
    } on ApiError catch (error) {
      setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final locked = widget.live.lock != null;
    if (!_open) {
      return Padding(
        padding: const EdgeInsets.only(top: 8),
        child: OutlinedButton.icon(
          onPressed: () => setState(() => _open = true),
          icon: Icon(locked ? Icons.lock_open : Icons.lock),
          label: Text(locked ? 'Unlock the valve' : 'Lock the valve'),
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 8),
        if (!locked)
          DropdownButton<String>(
            isExpanded: true,
            value: _state,
            items: const [
              DropdownMenuItem(value: 'closed', child: Text('Lock it closed (water off)')),
              DropdownMenuItem(value: 'open', child: Text('Lock it open (water on)')),
            ],
            onChanged: (v) => setState(() => _state = v ?? _state),
          ),
        TextField(
          key: const Key('lock-reason'),
          controller: _reason,
          maxLength: 300,
          decoration: const InputDecoration(
            labelText: 'Reason (the residents see it)',
            helperText: 'At least 10 characters',
          ),
          onChanged: (_) => setState(() {}),
        ),
        if (_error != null) ErrorBox(error: _error),
        Wrap(
          spacing: 8,
          children: [
            FilledButton(
              onPressed: _busy || _reason.text.trim().length < 10 ? null : _submit,
              child: Text(locked ? 'Unlock' : 'Lock'),
            ),
            TextButton(onPressed: () => setState(() => _open = false), child: const Text('Cancel')),
          ],
        ),
      ],
    );
  }
}

/// Hourly (24 h), daily (30 days) or monthly (12 months) litres.
class UsageCard extends StatefulWidget {
  const UsageCard({super.key, required this.homeId});
  final String homeId;
  @override
  State<UsageCard> createState() => _UsageCardState();
}

class _UsageCardState extends State<UsageCard> {
  String _range = 'day';

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    final now = DateTime.now();
    final (from, label) = switch (_range) {
      'hour' => (
        fmt.isoDay(now.subtract(const Duration(days: 1))),
        (String s) => DateFormat.Hm().format(DateTime.parse(s).toLocal()),
      ),
      'month' => (
        fmt.isoDay(now.subtract(const Duration(days: 364))),
        (String s) => DateFormat.MMM().format(DateTime.parse(s.substring(0, 10))),
      ),
      _ => (
        fmt.isoDay(now.subtract(const Duration(days: 29))),
        (String s) => DateFormat.Md().format(DateTime.parse(s.substring(0, 10))),
      ),
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Heading(
          'Water use',
          trailing: SegmentedButton<String>(
            showSelectedIcon: false,
            segments: const [
              ButtonSegment(value: 'hour', label: Text('24 h')),
              ButtonSegment(value: 'day', label: Text('30 d')),
              ButtonSegment(value: 'month', label: Text('12 m')),
            ],
            selected: {_range},
            onSelectionChanged: (s) => setState(() => _range = s.first),
          ),
        ),
        Loader<Consumption>(
          keyValue: _range,
          load: () async => Consumption.fromJson(
            await api.get('/homes/${widget.homeId}/consumption?granularity=$_range&from=$from') as Map<String, dynamic>,
          ),
          builder: (context, data, _) => Column(
            children: [
              BarsChart(points: data.points, label: label),
              Text('Total: ${fmt.litres(data.totalLitres)}'),
            ],
          ),
        ),
      ],
    );
  }
}
