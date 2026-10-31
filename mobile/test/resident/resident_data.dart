// Seeded answers for one resident in unit 01 (like the demo data).
import '../helpers.dart';

Map<String, dynamic> liveJson({
  String valve = 'open',
  Map<String, dynamic>? lock,
  bool online = true,
  double ph = 7.2,
}) => {
  'home_id': 'h1',
  'device_id': 'home-b01-01',
  'online': online,
  'last_seen_at': DateTime.now().toUtc().toIso8601String(),
  'valve': valve,
  'reading': {
    'time': DateTime.now().toUtc().toIso8601String(),
    'total_l': 12000.0,
    'delta_l': 0.5,
    'flow_lpm': 3.2,
    'ph': ph,
    'temp_c': 24.5,
    'valve': valve,
    'rssi': -60,
  },
  'lock': lock,
  'pending_command': null,
  'unit_no': '01',
  'building_name': 'Demo Building',
  'ph_safe_low': 6.5,
  'ph_safe_high': 8.5,
};

void residentData(FakeApi api, {Map<String, dynamic>? live}) {
  api.json('GET /me', meJson());
  api.json('GET /homes/h1/live', live ?? liveJson());
  final today = DateTime.now();
  final day = '${today.year}-${today.month.toString().padLeft(2, '0')}-${today.day.toString().padLeft(2, '0')}';
  api.json('GET /homes/h1/consumption', {
    'granularity': 'day',
    'timezone': 'Asia/Karachi',
    'from': '${day.substring(0, 8)}01',
    'to': day,
    'total_litres': 4321.0,
    'points': [
      {'start': '${day}T00:00:00+05:00', 'litres': 312.0},
    ],
  });
  api.json('GET /homes/h1/bill-estimate', {
    'month': '${day.substring(0, 8)}01',
    'from': '${day.substring(0, 8)}01',
    'to': day,
    'currency_code': 'PKR',
    'litres': 4321.0,
    'total_text': 'PKR 1,234.00',
    'message': 'Not a bill: the real bill comes after the month ends.',
  });
  api.json('GET /alerts', [
    {
      'id': 'a1',
      'rule': 'high_usage',
      'severity': 'info',
      'state': 'open',
      'building_id': 'b1',
      'home_id': 'h1',
      'device_id': 'home-b01-01',
      'title': 'High daily use',
      'message': 'Today is 160% of your usual use.',
      'seen_count': 1,
      'opened_at': DateTime.now().toUtc().toIso8601String(),
      'last_seen_at': DateTime.now().toUtc().toIso8601String(),
    },
  ]);
  api.json('GET /homes/h1/comparison', {
    'month': '${day.substring(0, 8)}01',
    'until': day,
    'litres': 4321.0,
    'previous_month_litres': 4000.0,
    'people_in_home': 3,
    'litres_per_person': 1440.3,
    'building_litres_per_person': null,
    'occupied_homes': 2,
  });
  api.json('GET /homes/h1/telemetry', [
    {'time': DateTime.now().toUtc().subtract(const Duration(hours: 1)).toIso8601String(), 'ph': 7.1, 'temp_c': 24.0},
    {'time': DateTime.now().toUtc().toIso8601String(), 'ph': 7.2, 'temp_c': 24.5},
  ]);
  api.json('GET /me/bills', [billJson()]);
  api.json('GET /bills/bill1', {
    ...billJson(),
    'month_days': 30,
    'subtotal': '1000.00',
    'tax_total': '170.00',
    'lines': [
      {
        'kind': 'slab',
        'description': '0 to 10,000 L at PKR 100.00 per 1,000 L',
        'quantity_l': '4321',
        'rate': '100.00',
        'amount': '1000.00',
      },
      {'kind': 'tax', 'description': 'Sales tax 17%', 'quantity_l': null, 'rate': null, 'amount': '170.00'},
    ],
    'payments': [],
    'void_reason': null,
    'can_pay_online': true,
  });
  api.json('GET /me/notifications', {
    'alert_email_min': 'warning',
    'email_muted_rules': <String>[],
    'quiet_from': null,
    'quiet_until': null,
  });
}

Map<String, dynamic> billJson({String status = 'issued'}) => {
  'id': 'bill1',
  'home_id': 'h1',
  'bill_no': 'B01-2026-000001',
  'unit_no': '01',
  'bill_to': 'Resident One',
  'month': '2026-09-01',
  'period_start': '2026-09-01',
  'period_end': '2026-09-30',
  'currency_code': 'PKR',
  'litres': '4321',
  'total': '1170.00',
  'amount_paid': '0.00',
  'outstanding': '1170.00',
  'total_text': 'PKR 1,170.00',
  'status': status,
  'due_date': '2026-10-15',
};
