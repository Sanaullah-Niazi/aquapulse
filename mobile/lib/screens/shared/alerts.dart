// Alerts (FR-59): newest first, open ones on top. Mark as seen ("acknowledge").
import 'package:flutter/material.dart';

import '../../api/client.dart';
import '../../api/models.dart';
import '../../app_state.dart';
import '../../widgets/alert_list.dart';
import '../../widgets/common.dart';

class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key});
  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> {
  bool _openOnly = true;

  @override
  Widget build(BuildContext context) {
    final api = AppScope.of(context).api;
    return Loader<List<Alert>>(
      keyValue: _openOnly,
      load: () async => [
        for (final a in await api.get('/alerts?limit=100${_openOnly ? '&active=true' : ''}') as List)
          Alert.fromJson(a as Map<String, dynamic>),
      ],
      reloadWhen: (e) => e['type'] == 'alert',
      builder: (context, alerts, reload) => ScreenList(
        onRefresh: reload,
        children: [
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Only open alerts'),
            value: _openOnly,
            onChanged: (v) => setState(() => _openOnly = v),
          ),
          if (alerts.isEmpty) const Text('No alerts. All good.'),
          for (final alert in alerts)
            AlertTile(
              alert: alert,
              trailing: alert.state == 'open'
                  ? TextButton(
                      onPressed: () async {
                        try {
                          await api.post('/alerts/${alert.id}/acknowledge');
                          await reload();
                        } on ApiError catch (error) {
                          if (context.mounted) toast(context, error.message);
                        }
                      },
                      child: const Text('Seen'),
                    )
                  : null,
            ),
        ],
      ),
    );
  }
}
