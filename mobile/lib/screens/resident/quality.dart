// R-03: pH and temperature over the last 24 hours (10-minute averages), with
// the building's safe pH range drawn as dashed lines.
import 'package:flutter/material.dart';

import '../../app_state.dart';
import '../../widgets/charts.dart';
import '../../widgets/common.dart';

class QualityScreen extends StatelessWidget {
  const QualityScreen({super.key, required this.homeId, this.phLow = 6.5, this.phHigh = 8.5});
  final String homeId;
  final double phLow;
  final double phHigh;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    final from = DateTime.now().toUtc().subtract(const Duration(hours: 24)).toIso8601String();
    return Loader<List<Map<String, dynamic>>>(
      keyValue: homeId,
      load: () async =>
          (await api.get('/homes/$homeId/telemetry?from=${Uri.encodeQueryComponent(from)}&every=600') as List)
              .cast<Map<String, dynamic>>(),
      builder: (context, rows, reload) {
        List<(DateTime, double)> series(String key) => [
          for (final r in rows)
            if (r[key] != null) (DateTime.parse(r['time'] as String), (r[key] as num).toDouble()),
        ];
        return ScreenList(
          onRefresh: reload,
          children: [
            const Text(
              'pH tells if water is acidic or alkaline. 7 is neutral. Drinking water should stay inside the dashed '
              'lines (your building\'s safe range). Outside it, the app warns you.',
            ),
            Heading('pH, last 24 hours (safe ${phLow.toStringAsFixed(1)} to ${phHigh.toStringAsFixed(1)})'),
            LineChartBox(points: series('ph'), limits: [phLow, phHigh]),
            const Heading('Temperature, last 24 hours'),
            LineChartBox(points: series('temp_c'), unit: '°C'),
          ],
        );
      },
    );
  }
}
