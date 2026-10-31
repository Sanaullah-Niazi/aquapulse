// M10: AI insights. The forecast with its 80% range and the month-end bill
// (FR-65), and leaks, bursts and unusual days with their reason (FR-63 to FR-66).
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api/ai_models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/charts.dart';
import '../../widgets/common.dart';

/// The resident's AI screen: forecast and insights for one home.
class AiScreen extends StatelessWidget {
  const AiScreen({super.key, required this.homeId});
  final String homeId;

  @override
  Widget build(BuildContext context) => ScreenList(
    children: [
      const Text('What AquaPulse expects you to use, and anything unusual it noticed, each with its reason.'),
      const Heading('Forecast'),
      ForecastSection(path: '/homes/$homeId/forecast'),
      const Heading('Insights'),
      InsightsSection(homeId: homeId),
    ],
  );
}

class ForecastSection extends StatelessWidget {
  const ForecastSection({super.key, required this.path});
  final String path;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Loader<Forecast>(
      keyValue: path,
      load: () async => Forecast.fromJson(await api.get(path) as Map<String, dynamic>),
      builder: (context, f, _) {
        if (f.historyDays == 0) return const Text('No water use recorded yet, so there is nothing to forecast.');
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            StatGrid(
              children: [
                StatCard(label: 'Next 7 days', value: Text(fmt.litres(f.next7))),
                StatCard(
                  label: 'This month (to ${fmt.date(f.monthLastDay)})',
                  value: Text(fmt.litres(f.monthLitres)),
                  sub: Text('likely ${fmt.litres(f.monthLow)} to ${fmt.litres(f.monthHigh)}'),
                ),
                StatCard(
                  label: 'Bill estimate this month',
                  value: Text(f.billText ?? '-'),
                  sub: f.billLow != null && f.billHigh != null ? Text('likely ${f.billLow} to ${f.billHigh}') : null,
                ),
              ],
            ),
            ForecastChart(forecast: f),
            const Text(
              'Solid line: real litres per day. Dashed: forecast. Shaded: where the real litres should fall on 8 days out of 10.',
              style: TextStyle(fontSize: 12),
            ),
            const SizedBox(height: 4),
            Text(f.billMessage, style: const TextStyle(fontSize: 12)),
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text('How is this worked out?', style: TextStyle(fontSize: 14)),
              children: [Text('${f.methodName}. ${f.explanation}')],
            ),
          ],
        );
      },
    );
  }
}

/// The last 14 real days, then the 7 forecast days with a shaded range.
class ForecastChart extends StatelessWidget {
  const ForecastChart({super.key, required this.forecast});
  final Forecast forecast;

  @override
  Widget build(BuildContext context) {
    final f = forecast;
    final scheme = Theme.of(context).colorScheme;
    final n = f.recent.length;
    final labels = [for (final r in f.recent) r.$1, for (final d in f.days) d.day];
    final actual = [for (var i = 0; i < n; i++) FlSpot(i.toDouble(), f.recent[i].$2)];
    final start = n == 0 ? <FlSpot>[] : [FlSpot((n - 1).toDouble(), f.recent.last.$2)];
    final mean = [...start, for (var i = 0; i < f.days.length; i++) FlSpot((n + i).toDouble(), f.days[i].litres)];
    final low = [...start, for (var i = 0; i < f.days.length; i++) FlSpot((n + i).toDouble(), f.days[i].low)];
    final high = [...start, for (var i = 0; i < f.days.length; i++) FlSpot((n + i).toDouble(), f.days[i].high)];
    LineChartBarData line(List<FlSpot> spots, Color c, {List<int>? dash, bool show = true}) => LineChartBarData(
      spots: spots,
      color: show ? c : Colors.transparent,
      barWidth: show ? 2 : 0,
      dashArray: dash,
      dotData: const FlDotData(show: false),
    );
    return Semantics(
      label: 'Forecast: ${[for (final d in f.days) '${d.day} ${d.litres.round()} litres'].join(', ')}',
      child: SizedBox(
        height: 200,
        child: LineChart(
          LineChartData(
            lineBarsData: [
              line(actual, scheme.primary),
              line(mean, scheme.tertiary, dash: [6, 4]),
              line(low, scheme.tertiary, show: false),
              line(high, scheme.tertiary, show: false),
            ],
            betweenBarsData: [BetweenBarsData(fromIndex: 2, toIndex: 3, color: scheme.tertiary.withValues(alpha: 0.2))],
            gridData: const FlGridData(drawVerticalLine: false),
            borderData: FlBorderData(show: false),
            titlesData: FlTitlesData(
              topTitles: const AxisTitles(),
              rightTitles: const AxisTitles(),
              leftTitles: const AxisTitles(sideTitles: SideTitles(showTitles: true, reservedSize: 44)),
              bottomTitles: AxisTitles(
                sideTitles: SideTitles(
                  showTitles: true,
                  interval: 1,
                  getTitlesWidget: (value, meta) {
                    final i = value.toInt();
                    if (i % 4 != 0 || i >= labels.length) return const SizedBox.shrink();
                    return Text(
                      DateFormat.Md().format(DateTime.parse(labels[i])),
                      style: const TextStyle(fontSize: 10),
                    );
                  },
                ),
              ),
            ),
            lineTouchData: LineTouchData(
              touchTooltipData: LineTouchTooltipData(
                getTooltipItems: (spots) => [
                  for (final s in spots)
                    s.barIndex < 2 ? LineTooltipItem('${s.y.round()} L', const TextStyle(color: Colors.white)) : null,
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class InsightsSection extends StatelessWidget {
  const InsightsSection({super.key, required this.homeId});
  final String homeId;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Loader<List<Insight>>(
      keyValue: homeId,
      load: () async => [
        for (final i in await api.get('/homes/$homeId/insights') as List) Insight.fromJson(i as Map<String, dynamic>),
      ],
      reloadWhen: (e) => e['type'] == 'alert',
      builder: (context, items, _) => items.isEmpty
          ? const Text('Nothing unusual found. AquaPulse watches for leaks, burst pipes and unusual days.')
          : Column(children: [for (final i in items.take(10)) InsightCard(insight: i)]),
    );
  }
}

const _featureNames = {
  'litres': 'Litres in the day',
  'ratio_7d': 'Times the usual day',
  'z_7d': 'Distance from the usual day',
  'night_min_lpm': 'Lowest night flow (L/min)',
  'peak_hour_share': 'Share in the busiest hour',
  'active_hours': 'Hours with water use',
};

class InsightCard extends StatelessWidget {
  const InsightCard({super.key, required this.insight});
  final Insight insight;

  @override
  Widget build(BuildContext context) {
    final i = insight;
    final colour = switch (i.severity) {
      'critical' => Colors.red,
      'warning' => Colors.orange,
      _ => Theme.of(context).colorScheme.primary,
    };
    final facts = [
      fmt.dateTime(i.openedAt),
      if (i.confidence != null) 'confidence ${(i.confidence! * 100).round()}%',
      if (i.highConfidence) 'sure',
      if (i.resolvedAt != null) 'over',
      if (i.modelVersion != null) 'model v${i.modelVersion}',
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.auto_awesome, color: colour, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(i.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(i.explanation),
            const SizedBox(height: 4),
            Text(facts.join(' · '), style: const TextStyle(fontSize: 12)),
            if (i.points.length > 1) ...[
              const SizedBox(height: 8),
              LineChartBox(
                points: i.points,
                unit: 'L/min',
                limits: [
                  if (i.kind == 'burst' && i.evidence['burst_lpm'] != null) (i.evidence['burst_lpm'] as num).toDouble(),
                ],
              ),
            ],
            for (final (name, value, normal) in i.topFeatures)
              Text('${_featureNames[name] ?? name}: $value (normal $normal)', style: const TextStyle(fontSize: 12)),
          ],
        ),
      ),
    );
  }
}

/// FR-61: the owner lets AquaPulse close the valve by itself on a sure leak.
class AutoShutoffSwitch extends StatefulWidget {
  const AutoShutoffSwitch({super.key, required this.homeId});
  final String homeId;
  @override
  State<AutoShutoffSwitch> createState() => _AutoShutoffSwitchState();
}

class _AutoShutoffSwitchState extends State<AutoShutoffSwitch> {
  bool? _on;
  bool _saving = false;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Loader<bool>(
      keyValue: widget.homeId,
      load: () async => (await api.get('/homes/${widget.homeId}') as Map)['auto_shutoff_enabled'] as bool? ?? false,
      builder: (context, loaded, _) {
        final on = _on ?? loaded;
        return Card(
          child: Column(
            children: [
              SwitchListTile(
                title: const Text('Automatic shut-off'),
                subtitle: Text(
                  on
                      ? 'On: the valve closes by itself on a sure leak or burst. At most once an hour, never on a locked valve.'
                      : 'Off: you get the alert, and you decide.',
                ),
                value: on,
                onChanged: _saving
                    ? null
                    : (v) async {
                        setState(() => _saving = true);
                        try {
                          final saved = await api.patch('/homes/${widget.homeId}', {'auto_shutoff_enabled': v}) as Map;
                          setState(() => _on = saved['auto_shutoff_enabled'] as bool? ?? v);
                        } catch (e) {
                          if (context.mounted) toast(context, 'Could not save: $e');
                        } finally {
                          if (mounted) setState(() => _saving = false);
                        }
                      },
              ),
            ],
          ),
        );
      },
    );
  }
}

/// The owner's AI screen: the building forecast and the model registry (FR-67).
class OwnerAiScreen extends StatelessWidget {
  const OwnerAiScreen({super.key, required this.buildingId});
  final String buildingId;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return ScreenList(
      children: [
        const Heading('Forecast for the whole building'),
        ForecastSection(path: '/buildings/$buildingId/forecast'),
        const Heading('AI models'),
        const Text(
          'Models retrain every night. A new version goes live only if it is not worse than the live one.',
          style: TextStyle(fontSize: 12),
        ),
        Loader<List<AiModelVersion>>(
          keyValue: buildingId,
          load: () async => [
            for (final m in await api.get('/buildings/$buildingId/ai/models') as List)
              AiModelVersion.fromJson(m as Map<String, dynamic>),
          ],
          builder: (context, models, _) => models.isEmpty
              ? const Text(
                  'No models yet. They are trained after 01:00 building time once there are about 2 weeks of readings.',
                )
              : Column(
                  children: [
                    for (final m in models.take(20))
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(
                          m.live ? Icons.check_circle : Icons.remove_circle_outline,
                          color: m.live ? Colors.green : null,
                        ),
                        title: Text('${m.name} v${m.version}${m.live ? ' · live' : ''}'),
                        subtitle: Text('${fmt.dateTime(m.trainedAt)}\n${m.scores}\n${m.note}'),
                        isThreeLine: true,
                      ),
                  ],
                ),
        ),
      ],
    );
  }
}
