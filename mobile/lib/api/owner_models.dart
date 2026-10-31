// The owner's API answers (M9 part 2). Only the fields the app uses are read.
double? _d(Object? v) => v == null ? null : (v as num).toDouble();
double _d0(Object? v) => v == null ? 0 : (v as num).toDouble();

class Building {
  Building.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      name = j['name'] as String,
      currency = j['currency_code'] as String? ?? '',
      timezone = j['timezone'] as String? ?? '';
  final String id;
  final String name;
  final String currency;
  final String timezone;
}

class OverviewRow {
  OverviewRow.fromJson(Map<String, dynamic> j)
    : homeId = j['home_id'] as String,
      unitNo = j['unit_no'] as String,
      floor = (j['floor'] as num? ?? 0).toInt(),
      residents = (j['residents'] as List? ?? const []).cast<String>(),
      litresToday = _d0(j['litres_today']),
      litresMonth = _d0(j['litres_month']),
      flowLpm = _d0(j['flow_lpm']),
      ph = _d(j['ph']),
      tempC = _d(j['temp_c']),
      deviceId = j['device_id'] as String?,
      online = j['online'] as bool? ?? false,
      lastSeenAt = j['last_seen_at'] as String?,
      valve = j['valve'] as String?,
      locked = j['locked'] as String?,
      lockReason = j['lock_reason'] as String?,
      openAlerts = (j['open_alerts'] as num? ?? 0).toInt();
  final String homeId;
  final String unitNo;
  final int floor;
  final List<String> residents;
  double litresToday;
  double litresMonth;
  double flowLpm;
  double? ph;
  double? tempC;
  final String? deviceId;
  bool online;
  String? lastSeenAt;
  String? valve;
  final String? locked;
  final String? lockReason;
  final int openAlerts;

  String get residentNames => residents.isEmpty ? 'Empty' : residents.join(', ');
}

class TankSummary {
  TankSummary.fromJson(Map<String, dynamic> j)
    : tankId = j['tank_id'] as String,
      levelPct = _d(j['level_pct']),
      levelL = _d(j['level_l']),
      pumpOn = j['pump_on'] as bool?,
      mode = j['mode'] as String? ?? 'auto',
      online = j['online'] as bool? ?? false;
  final String tankId;
  double? levelPct;
  double? levelL;
  bool? pumpOn;
  String mode;
  bool online;
}

class Overview {
  Overview.fromJson(Map<String, dynamic> j)
    : buildingId = j['building_id'] as String,
      name = j['name'] as String,
      currency = j['currency_code'] as String? ?? '',
      litresToday = _d0(j['litres_today']),
      litresMonth = _d0(j['litres_month']),
      litresLastMonthToDate = _d0(j['litres_last_month_to_date']),
      flowLpm = _d0(j['flow_lpm']),
      homes = (j['homes'] as num? ?? 0).toInt(),
      homesOnline = (j['homes_online'] as num? ?? 0).toInt(),
      homesWithDevice = (j['homes_with_device'] as num? ?? 0).toInt(),
      valvesClosed = (j['valves_closed'] as num? ?? 0).toInt(),
      valvesLocked = (j['valves_locked'] as num? ?? 0).toInt(),
      alerts = {
        for (final e in (j['alerts'] as Map<String, dynamic>? ?? const {}).entries) e.key: (e.value as num).toInt(),
      },
      tank = j['tank'] == null ? null : TankSummary.fromJson(j['tank'] as Map<String, dynamic>),
      unpaidBills = (j['unpaid_bills'] as num? ?? 0).toInt(),
      unpaidText = j['unpaid_text'] as String? ?? '',
      phSafeLow = _d(j['ph_safe_low']) ?? 6.5,
      phSafeHigh = _d(j['ph_safe_high']) ?? 8.5,
      rows = [for (final r in j['rows'] as List? ?? const []) OverviewRow.fromJson(r as Map<String, dynamic>)];

  final String buildingId;
  final String name;
  final String currency;
  double litresToday;
  double litresMonth;
  final double litresLastMonthToDate;
  double flowLpm;
  final int homes;
  int homesOnline;
  final int homesWithDevice;
  int valvesClosed;
  final int valvesLocked;
  final Map<String, int> alerts;
  final TankSummary? tank;
  final int unpaidBills;
  final String unpaidText;
  final double phSafeLow;
  final double phSafeHigh;
  final List<OverviewRow> rows;

  /// A home's new reading from the live connection: change its row and the
  /// building totals on the spot, without asking the API.
  void applyTelemetry(Map<String, dynamic> e) {
    final matches = rows.where((r) => r.homeId == e['homeId']);
    if (matches.isEmpty) return;
    final row = matches.first;
    final delta = _d0(e['deltaL']);
    row
      ..online = true
      ..ph = _d(e['ph']) ?? row.ph
      ..tempC = _d(e['tempC']) ?? row.tempC
      ..flowLpm = _d(e['flowLpm']) ?? row.flowLpm
      ..valve = e['valve'] as String? ?? row.valve
      ..lastSeenAt = e['time'] as String? ?? row.lastSeenAt
      ..litresToday += delta
      ..litresMonth += delta;
    flowLpm = rows.fold(0.0, (sum, r) => sum + r.flowLpm);
    litresToday = rows.fold(0.0, (sum, r) => sum + r.litresToday);
    litresMonth = rows.fold(0.0, (sum, r) => sum + r.litresMonth);
    homesOnline = rows.where((r) => r.online).length;
    valvesClosed = rows.where((r) => r.valve == 'closed').length;
  }

  void applyTank(Map<String, dynamic> e) {
    final t = tank;
    if (t == null) return;
    t
      ..online = true
      ..levelPct = _d(e['levelPct']) ?? t.levelPct
      ..pumpOn = e['pumpOn'] as bool? ?? t.pumpOn
      ..mode = e['mode'] as String? ?? t.mode;
  }
}

class TankReading {
  TankReading.fromJson(Map<String, dynamic> j)
    : time = j['time'] as String,
      levelPct = _d(j['level_pct']),
      levelL = _d(j['level_l']),
      pumpOn = j['pump_on'] as bool? ?? false,
      dryRunProtect = j['dry_run_protect'] as bool? ?? false,
      overflowProtect = j['overflow_protect'] as bool? ?? false,
      sumpOk = j['sump_ok'] as bool? ?? true;
  String time;
  double? levelPct;
  double? levelL;
  bool pumpOn;
  bool dryRunProtect;
  bool overflowProtect;
  bool sumpOk;
}

class Tank {
  Tank.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      capacityL = _d0(j['capacity_l']),
      lowPct = _d0(j['low_pct']),
      highPct = _d0(j['high_pct']),
      mode = j['mode'] as String? ?? 'auto',
      pumpKw = _d0(j['pump_kw']),
      deviceId = j['device_id'] as String?,
      online = j['online'] as bool? ?? false,
      lastSeenAt = j['last_seen_at'] as String?,
      reading = j['reading'] == null ? null : TankReading.fromJson(j['reading'] as Map<String, dynamic>),
      waiting = [
        for (final c in j['open_commands'] as List? ?? const [])
          if (c['state'] == 'queued' || c['state'] == 'pending') c['id'] as String,
      ];
  final String id;
  final double capacityL;
  final double lowPct;
  final double highPct;
  String mode;
  final double pumpKw;
  final String? deviceId;
  bool online;
  final String? lastSeenAt;
  TankReading? reading;

  /// Commands sent to the tank device that it has not answered yet.
  final List<String> waiting;

  void applyLive(Map<String, dynamic> e) {
    online = true;
    mode = e['mode'] as String? ?? mode;
    final r = reading;
    if (r == null) return;
    final pct = _d(e['levelPct']);
    r
      ..time = e['time'] as String? ?? r.time
      ..levelPct = pct
      ..levelL = pct == null ? null : (pct / 100 * capacityL).roundToDouble()
      ..pumpOn = e['pumpOn'] as bool? ?? r.pumpOn
      ..dryRunProtect = e['dryRunProtect'] as bool? ?? r.dryRunProtect
      ..overflowProtect = e['overflowProtect'] as bool? ?? r.overflowProtect
      ..sumpOk = e['sumpOk'] as bool? ?? r.sumpOk;
  }
}

class TankHistory {
  TankHistory.fromJson(Map<String, dynamic> j)
    : levels = [
        for (final l in j['levels'] as List? ?? const [])
          if (l['level_pct'] != null) (DateTime.parse(l['start'] as String), _d0(l['level_pct'])),
      ],
      days = [
        for (final d in j['days'] as List? ?? const [])
          (d['day'] as String, _d0(d['pump_hours']), _d0(d['energy_kwh'])),
      ];
  final List<(DateTime, double)> levels;
  final List<(String, double, double)> days;
}
