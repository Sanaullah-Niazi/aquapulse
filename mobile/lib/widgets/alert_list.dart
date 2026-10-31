// A list of alerts with severity shown by icon and word (not colour only).
import 'package:flutter/material.dart';

import '../api/models.dart';
import '../format.dart' as fmt;

const severityText = {'critical': 'Critical', 'warning': 'Warning', 'info': 'Info'};

IconData severityIcon(String severity) => switch (severity) {
  'critical' => Icons.dangerous,
  'warning' => Icons.warning_amber,
  _ => Icons.info_outline,
};

Color severityColour(String severity) => switch (severity) {
  'critical' => Colors.red.shade700,
  'warning' => Colors.orange.shade800,
  _ => Colors.blue.shade700,
};

const alertStateText = {
  'open': 'Open',
  'acknowledged': 'Seen',
  'resolved': 'Resolved',
  'auto_resolved': 'Cleared by itself',
};

class AlertTile extends StatelessWidget {
  const AlertTile({super.key, required this.alert, this.trailing});
  final Alert alert;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) => ListTile(
    contentPadding: EdgeInsets.zero,
    leading: Icon(severityIcon(alert.severity), color: severityColour(alert.severity)),
    title: Text(alert.title),
    subtitle: Text(
      '${severityText[alert.severity] ?? alert.severity} · ${alertStateText[alert.state] ?? alert.state} · '
      '${fmt.dateTime(alert.openedAt)}\n${alert.message}',
    ),
    isThreeLine: true,
    trailing: trailing,
  );
}
