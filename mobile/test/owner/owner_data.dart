// Seeded answers for an owner with one building and three homes.
import '../helpers.dart';
import '../resident/resident_data.dart';

Map<String, dynamic> row(
  String id,
  String unit,
  String resident, {
  double today = 100,
  double month = 2000,
  double? ph = 7.2,
  bool online = true,
  String valve = 'open',
  int alerts = 0,
}) => {
  'home_id': id,
  'unit_no': unit,
  'floor': 1,
  'residents': resident.isEmpty ? <String>[] : [resident],
  'litres_today': today,
  'litres_month': month,
  'flow_lpm': 1.0,
  'ph': ph,
  'temp_c': 24.0,
  'reading_at': DateTime.now().toUtc().toIso8601String(),
  'device_id': 'home-$unit',
  'online': online,
  'last_seen_at': DateTime.now().toUtc().toIso8601String(),
  'valve': valve,
  'locked': null,
  'lock_reason': null,
  'open_alerts': alerts,
};

Map<String, dynamic> overviewJson() => {
  'building_id': 'b1',
  'name': 'Demo Building',
  'timezone': 'Asia/Karachi',
  'currency_code': 'PKR',
  'litres_today': 650.0,
  'litres_month': 9000.0,
  'litres_last_month_to_date': 10000.0,
  'flow_lpm': 3.0,
  'homes': 3,
  'homes_online': 2,
  'homes_with_device': 3,
  'valves_closed': 1,
  'valves_locked': 0,
  'alerts': {'critical': 1, 'warning': 2, 'info': 0},
  'tank': {'tank_id': 't1', 'level_pct': 64.0, 'level_l': 6400.0, 'pump_on': false, 'mode': 'auto', 'online': true},
  'unpaid_bills': 2,
  'unpaid_text': 'PKR 2,340.00',
  'ph_safe_low': 6.5,
  'ph_safe_high': 8.5,
  'rows': [
    row('h1', '01', 'Resident One', today: 300, month: 4000),
    row('h2', '02', 'Ayesha Khan', today: 50, month: 1000, ph: 9.2, alerts: 1),
    row('h3', '03', 'Bilal Ahmed', today: 300, month: 4000, online: false, valve: 'closed'),
  ],
};

Map<String, dynamic> tankJson({String mode = 'auto', bool pumpOn = false}) => {
  'id': 't1',
  'capacity_l': 10000.0,
  'low_pct': 30.0,
  'high_pct': 90.0,
  'mode': mode,
  'pump_kw': 0.75,
  'device_id': 'tank-b01',
  'online': true,
  'last_seen_at': DateTime.now().toUtc().toIso8601String(),
  'reading': {
    'time': DateTime.now().toUtc().toIso8601String(),
    'level_pct': 64.0,
    'level_l': 6400.0,
    'pump_on': pumpOn,
    'sump_ok': true,
    'sensor_ok': true,
    'dry_run_protect': false,
    'overflow_protect': false,
    'mode': mode,
  },
  'open_commands': <Object>[],
};

void ownerData(FakeApi api) {
  residentData(api); // home h1's live data, consumption, alerts and bill
  api.json('GET /me', meJson(role: 'owner', homes: [], buildings: ['b1']));
  api.json('GET /buildings', [
    {
      'id': 'b1',
      'name': 'Demo Building',
      'address': '',
      'timezone': 'Asia/Karachi',
      'currency_code': 'PKR',
      'code': 'B01',
    },
  ]);
  api.json('GET /buildings/b1/overview', overviewJson());
  api.json('GET /buildings/b1/tank', tankJson());
  api.json('GET /tanks/t1/history', {
    'levels': [
      {'start': '2026-10-30T00:00:00Z', 'level_pct': 50.0},
      {'start': '2026-10-30T01:00:00Z', 'level_pct': 60.0},
    ],
    'days': [
      {'day': '2026-10-30', 'pump_hours': 2.5, 'energy_kwh': 1.9},
    ],
  });
  api.json('GET /buildings/b1/bills', [
    {...billJson(status: 'draft'), 'id': 'bill1', 'bill_no': null},
  ]);
}
