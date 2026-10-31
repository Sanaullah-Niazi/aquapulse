// Bar and line charts (fl_chart). Tap a bar or point to see its exact value.
import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../api/models.dart';

class BarsChart extends StatelessWidget {
  const BarsChart({super.key, required this.points, required this.label, this.unit = 'L'});
  final List<Point> points;

  /// Turns a point's start ("2026-10-30T00:00:00+05:00") into a short label.
  final String Function(String start) label;
  final String unit;

  @override
  Widget build(BuildContext context) {
    if (points.isEmpty) return const SizedBox(height: 60, child: Center(child: Text('No data for this period.')));
    final colour = Theme.of(context).colorScheme.primary;
    final every = (points.length / 6).ceil();
    return SizedBox(
      height: 200,
      child: BarChart(
        BarChartData(
          barGroups: [
            for (var i = 0; i < points.length; i++)
              BarChartGroupData(
                x: i,
                barRods: [BarChartRodData(toY: points[i].value, color: colour, width: points.length > 40 ? 3 : 8)],
              ),
          ],
          gridData: const FlGridData(drawVerticalLine: false),
          borderData: FlBorderData(show: false),
          titlesData: FlTitlesData(
            topTitles: const AxisTitles(),
            rightTitles: const AxisTitles(),
            leftTitles: const AxisTitles(sideTitles: SideTitles(showTitles: true, reservedSize: 44)),
            bottomTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                getTitlesWidget: (value, meta) {
                  final i = value.toInt();
                  if (i % every != 0 || i >= points.length) return const SizedBox.shrink();
                  return Text(label(points[i].start), style: const TextStyle(fontSize: 10));
                },
              ),
            ),
          ),
          barTouchData: BarTouchData(
            touchTooltipData: BarTouchTooltipData(
              getTooltipItem: (group, _, rod, _) => BarTooltipItem(
                '${label(points[group.x].start)}\n${rod.toY.round()} $unit',
                const TextStyle(color: Colors.white),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// A line over time, with optional dashed limit lines (the safe pH range).
class LineChartBox extends StatelessWidget {
  const LineChartBox({super.key, required this.points, this.limits = const [], this.unit = ''});
  final List<(DateTime, double)> points;
  final List<double> limits;
  final String unit;

  @override
  Widget build(BuildContext context) {
    if (points.isEmpty) return const SizedBox(height: 60, child: Center(child: Text('No readings for this period.')));
    final first = points.first.$1;
    double x(DateTime t) => t.difference(first).inMinutes.toDouble();
    return SizedBox(
      height: 180,
      child: LineChart(
        LineChartData(
          lineBarsData: [
            LineChartBarData(
              spots: [for (final p in points) FlSpot(x(p.$1), p.$2)],
              dotData: const FlDotData(show: false),
              color: Theme.of(context).colorScheme.primary,
            ),
          ],
          extraLinesData: ExtraLinesData(
            horizontalLines: [
              for (final y in limits) HorizontalLine(y: y, dashArray: [6, 4], color: Colors.orange),
            ],
          ),
          borderData: FlBorderData(show: false),
          titlesData: const FlTitlesData(
            topTitles: AxisTitles(),
            rightTitles: AxisTitles(),
            bottomTitles: AxisTitles(),
            leftTitles: AxisTitles(sideTitles: SideTitles(showTitles: true, reservedSize: 40)),
          ),
          lineTouchData: LineTouchData(
            touchTooltipData: LineTouchTooltipData(
              getTooltipItems: (spots) => [
                for (final s in spots)
                  LineTooltipItem('${s.y.toStringAsFixed(1)} $unit', const TextStyle(color: Colors.white)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
