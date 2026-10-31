// R-02: my water use per day and per month, against last month and against
// the building's average per person (FR-49). Only my own stay is shown: the
// API never sends anything from before I moved in.
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../api/models.dart';
import '../../app_state.dart';
import '../../format.dart' as fmt;
import '../../widgets/charts.dart';
import '../../widgets/common.dart';

class UsageScreen extends StatefulWidget {
  const UsageScreen({super.key, required this.homeId});
  final String homeId;
  @override
  State<UsageScreen> createState() => _UsageScreenState();
}

class _UsageScreenState extends State<UsageScreen> {
  int _days = 30;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    final id = widget.homeId;
    String daysAgo(int n) => fmt.isoDay(DateTime.now().subtract(Duration(days: n)));
    return ScreenList(
      children: [
        const Heading('This month compared'),
        Loader<Comparison>(
          keyValue: id,
          load: () async => Comparison.fromJson(await api.get('/homes/$id/comparison') as Map<String, dynamic>),
          builder: (context, c, _) => StatGrid(
            children: [
              StatCard(label: 'This month', value: Text(fmt.litres(c.litres))),
              StatCard(
                label: 'Last month (same days)',
                value: Text(c.previousMonthLitres == null ? '-' : fmt.litres(c.previousMonthLitres)),
                sub: c.previousMonthLitres == null ? const Text('You did not live here yet') : null,
              ),
              StatCard(label: 'Per person (${c.peopleInHome} people)', value: Text(fmt.litres(c.litresPerPerson))),
              StatCard(
                label: 'Building average per person',
                value: Text(fmt.litres(c.buildingLitresPerPerson)),
                sub: c.buildingLitresPerPerson == null
                    ? const Text('Shown when at least 3 homes are occupied, so nobody\'s use can be worked out')
                    : null,
              ),
            ],
          ),
        ),
        Heading(
          'Litres per day',
          trailing: SegmentedButton<int>(
            showSelectedIcon: false,
            segments: const [
              ButtonSegment(value: 7, label: Text('7 d')),
              ButtonSegment(value: 30, label: Text('30 d')),
              ButtonSegment(value: 90, label: Text('90 d')),
            ],
            selected: {_days},
            onSelectionChanged: (s) => setState(() => _days = s.first),
          ),
        ),
        Loader<Consumption>(
          keyValue: '$id/$_days',
          load: () async => Consumption.fromJson(
            await api.get('/homes/$id/consumption?granularity=day&from=${daysAgo(_days - 1)}') as Map<String, dynamic>,
          ),
          builder: (context, data, _) => Column(
            children: [
              BarsChart(points: data.points, label: (s) => DateFormat.Md().format(DateTime.parse(s.substring(0, 10)))),
              Text('Total: ${fmt.litres(data.totalLitres)}'),
            ],
          ),
        ),
        const Heading('Litres per month (last 12 months)'),
        Loader<Consumption>(
          keyValue: id,
          load: () async => Consumption.fromJson(
            await api.get('/homes/$id/consumption?granularity=month&from=${daysAgo(364)}') as Map<String, dynamic>,
          ),
          builder: (context, data, _) =>
              BarsChart(points: data.points, label: (s) => DateFormat.MMM().format(DateTime.parse(s.substring(0, 10)))),
        ),
      ],
    );
  }
}
