// M10: AI insights, the forecast with its month-end bill, and the model registry.
double _d0(Object? v) => v == null ? 0 : (v as num).toDouble();

class Insight {
  Insight.fromJson(Map<String, dynamic> j)
    : alertId = j['alert_id'] as String,
      kind = j['kind'] as String,
      title = j['title'] as String,
      severity = j['severity'] as String,
      explanation = j['explanation'] as String,
      confidence = j['confidence'] == null ? null : (j['confidence'] as num).toDouble(),
      highConfidence = j['high_confidence'] as bool? ?? false,
      openedAt = j['opened_at'] as String,
      resolvedAt = j['resolved_at'] as String?,
      evidence = (j['evidence'] as Map?)?.cast<String, dynamic>() ?? const {};
  final String alertId, kind, title, severity, explanation, openedAt;
  final double? confidence;
  final bool highConfidence;
  final String? resolvedAt;
  final Map<String, dynamic> evidence;

  /// The flow readings behind a leak or burst: (time, L/min).
  List<(DateTime, double)> get points => [
    for (final p in evidence['points'] as List? ?? const [])
      (DateTime.parse((p as List)[0] as String).toLocal(), _d0(p[1])),
  ];

  /// For unusual days: the numbers that made the day unusual.
  List<(String, double, double)> get topFeatures => [
    for (final f in evidence['top_features'] as List? ?? const [])
      (f['feature'] as String, _d0(f['value']), _d0(f['normal'])),
  ];
  int? get modelVersion => evidence['model_version'] as int?;
}

class ForecastDay {
  ForecastDay.fromJson(Map<String, dynamic> j)
    : day = j['day'] as String,
      litres = _d0(j['litres']),
      low = _d0(j['low']),
      high = _d0(j['high']);
  final String day;
  final double litres, low, high;
}

class Forecast {
  Forecast.fromJson(Map<String, dynamic> j)
    : method = j['method'] as String,
      historyDays = j['history_days'] as int,
      explanation = j['explanation'] as String,
      recent = [for (final d in j['recent'] as List) (d['day'] as String, _d0(d['litres']))],
      days = [for (final d in j['days'] as List) ForecastDay.fromJson(d as Map<String, dynamic>)],
      monthLastDay = j['month_end']['last_day'] as String,
      monthLitres = _d0(j['month_end']['litres']),
      monthLow = _d0(j['month_end']['low']),
      monthHigh = _d0(j['month_end']['high']),
      billText = j['bill']['total_text'] as String?,
      billLow = j['bill']['low_text'] as String?,
      billHigh = j['bill']['high_text'] as String?,
      billMessage = j['bill']['message'] as String;
  final String method, explanation, monthLastDay, billMessage;
  final int historyDays;
  final List<(String, double)> recent;
  final List<ForecastDay> days;
  final double monthLitres, monthLow, monthHigh;
  final String? billText, billLow, billHigh;

  double get next7 => days.fold(0, (sum, d) => sum + d.litres);
  String get methodName =>
      method == 'holt_winters' ? 'Holt-Winters (learns the weekly pattern)' : 'Same weekday last week';
}

class AiModelVersion {
  AiModelVersion.fromJson(Map<String, dynamic> j)
    : kind = j['kind'] as String,
      version = j['version'] as int,
      trainedAt = j['trained_at'] as String,
      live = j['live'] as bool,
      note = j['note'] as String,
      metrics = (j['metrics'] as Map).cast<String, dynamic>();
  final String kind, trainedAt, note;
  final int version;
  final bool live;
  final Map<String, dynamic> metrics;

  String get name => kind == 'forecast' ? 'Forecast' : 'Unusual use';
  String get scores {
    String pct(Object? v) => v == null ? '-' : '${((v as num) * 100).round()}%';
    return kind == 'forecast'
        ? 'MAPE ${pct(metrics['method_mape'])} · naive ${pct(metrics['naive_mape'])} · in range ${pct(metrics['coverage'])}'
        : 'doubled days caught ${pct(metrics['doubled_recall'])} · real days flagged ${pct(metrics['false_rate'])}';
  }
}
