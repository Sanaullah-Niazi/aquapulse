// What the M10 AI endpoints answer, for the widget tests.
import '../helpers.dart';

Map<String, dynamic> forecastJson({int history = 30, String? bill = 'PKR 780.00'}) => {
  'method': 'holt_winters',
  'history_days': history,
  'explanation': 'Learned from 30 days: the usual level and the weekly pattern.',
  'recent': [
    for (var i = 0; i < 14; i++) {'day': '2026-10-${(17 + i).toString().padLeft(2, '0')}', 'litres': 400.0 + i},
  ],
  'days': [
    for (var i = 0; i < 7; i++)
      {'day': '2026-1${i == 0 ? '0-31' : '1-0$i'}', 'litres': 500.0, 'low': 450.0, 'high': 560.0},
  ],
  'month_end': {'last_day': '2026-10-31', 'litres': 12500.0, 'low': 12450.0, 'high': 12560.0},
  'bill': {
    'currency_code': 'PKR',
    'total': bill == null ? null : '780.00',
    'total_text': bill,
    'low_text': bill == null ? null : 'PKR 774.00',
    'high_text': bill == null ? null : 'PKR 787.20',
    'message': bill == null
        ? 'The owner has not set water rates for this month yet.'
        : 'An estimate for the whole month: real use so far plus the forecast. Not a bill.',
  },
};

final insightsJson = [
  {
    'alert_id': 'a1',
    'kind': 'burst',
    'title': 'Burst pipe suspected',
    'severity': 'critical',
    'state': 'open',
    'explanation': 'Very high flow, up to 35.0 L/min, for 5 min since 09:57.',
    'confidence': 0.95,
    'high_confidence': true,
    'opened_at': '2026-10-31T05:00:00Z',
    'last_seen_at': '2026-10-31T05:05:00Z',
    'resolved_at': null,
    'evidence': {
      'burst_lpm': 24.0,
      'points': [
        ['2026-10-31T04:58:00Z', 2.0],
        ['2026-10-31T05:00:00Z', 35.0],
        ['2026-10-31T05:05:00Z', 34.0],
      ],
    },
  },
  {
    'alert_id': 'a2',
    'kind': 'unusual_use',
    'title': 'Unusual water use',
    'severity': 'info',
    'state': 'resolved',
    'explanation': "Used 1196 L on Tue 08 Sep, 2.4 times this home's usual day in the 7 days before (490 L).",
    'confidence': null,
    'high_confidence': false,
    'opened_at': '2026-09-09T01:00:00Z',
    'last_seen_at': '2026-09-09T01:00:00Z',
    'resolved_at': '2026-09-09T01:10:00Z',
    'evidence': {
      'model_version': 3,
      'top_features': [
        {'feature': 'ratio_7d', 'value': 2.44, 'normal': 1.0, 'distance': 9.1},
      ],
    },
  },
];

final modelsJson = [
  {
    'id': 'm2',
    'kind': 'unusual_use',
    'version': 2,
    'trained_at': '2026-10-31T20:00:00Z',
    'data_from': '2026-09-01',
    'data_to': '2026-10-30',
    'rows': 500,
    'metrics': {'doubled_recall': 0.95, 'false_rate': 0.02},
    'params': {},
    'live': true,
    'note': 'live: not worse than the version before',
  },
  {
    'id': 'm1',
    'kind': 'forecast',
    'version': 1,
    'trained_at': '2026-10-30T20:00:00Z',
    'data_from': '2026-09-01',
    'data_to': '2026-10-29',
    'rows': 59,
    'metrics': {'method_mape': 0.157, 'naive_mape': 0.195, 'coverage': 0.85},
    'params': {'method': 'holt_winters'},
    'live': false,
    'note': 'kept out: holt winters was more accurate',
  },
];

void aiData(FakeApi api) {
  api.json('GET /homes/h1/forecast', forecastJson());
  api.json('GET /homes/h1/insights', insightsJson);
  api.json('GET /buildings/b1/forecast', forecastJson());
  api.json('GET /buildings/b1/ai/models', modelsJson);
}
