// The shapes of the API's answers (the same names as in /api/docs).
// Only the fields the app uses are read.

double? _d(Object? v) => v == null ? null : (v as num).toDouble();
double _d0(Object? v) => v == null ? 0 : (v as num).toDouble();
List<String> _strings(Object? v) => (v as List? ?? const []).cast<String>();

class Me {
  Me.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      email = j['email'] as String,
      fullName = j['full_name'] as String? ?? '',
      phone = j['phone'] as String?,
      role = j['role'] as String,
      buildingIds = _strings(j['building_ids']),
      homeIds = _strings(j['home_ids']),
      twoFactor = j['two_factor'] as bool? ?? false;
  final String id;
  final String email;
  final String fullName;
  final String? phone;

  /// "owner", "resident" or "sysadmin". The server decides; the app only
  /// chooses which screens to show (FR-41).
  final String role;
  final List<String> buildingIds;
  final List<String> homeIds;
  final bool twoFactor;

  bool get isOwner => role == 'owner';
  bool get isResident => role == 'resident';
}

class Reading {
  Reading({
    required this.time,
    required this.flowLpm,
    required this.ph,
    required this.tempC,
    required this.valve,
    this.totalL = 0,
  });
  Reading.fromJson(Map<String, dynamic> j)
    : time = j['time'] as String,
      flowLpm = _d0(j['flow_lpm']),
      ph = _d0(j['ph']),
      tempC = _d0(j['temp_c']),
      valve = j['valve'] as String? ?? '',
      totalL = _d0(j['total_l']);
  final String time;
  final double flowLpm;
  final double ph;
  final double tempC;
  final String valve;
  final double totalL;
}

class Command {
  Command.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      action = j['action'] as String?,
      state = j['state'] as String,
      reason = j['result_reason'] as String?;
  final String id;
  final String? action;

  /// queued, pending, succeeded, failed, rejected or timeout
  final String state;
  final String? reason;

  static const finalStates = {'succeeded', 'failed', 'rejected', 'timeout'};
  bool get done => finalStates.contains(state);
}

class ValveLock {
  ValveLock.fromJson(Map<String, dynamic> j)
    : lockedState = j['locked_state'] as String,
      reason = j['reason'] as String? ?? '';
  final String lockedState;
  final String reason;
}

class HomeLive {
  HomeLive.fromJson(Map<String, dynamic> j)
    : homeId = j['home_id'] as String,
      deviceId = j['device_id'] as String?,
      online = j['online'] as bool? ?? false,
      lastSeenAt = j['last_seen_at'] as String?,
      valve = j['valve'] as String?,
      reading = j['reading'] == null ? null : Reading.fromJson(j['reading'] as Map<String, dynamic>),
      lock = j['lock'] == null ? null : ValveLock.fromJson(j['lock'] as Map<String, dynamic>),
      pendingCommand = j['pending_command'] == null
          ? null
          : Command.fromJson(j['pending_command'] as Map<String, dynamic>),
      unitNo = j['unit_no'] as String? ?? '',
      buildingName = j['building_name'] as String? ?? '',
      phSafeLow = _d(j['ph_safe_low']) ?? 6.5,
      phSafeHigh = _d(j['ph_safe_high']) ?? 8.5;

  final String homeId;
  final String? deviceId;
  bool online;
  String? lastSeenAt;
  String? valve;
  Reading? reading;
  final ValveLock? lock;
  final Command? pendingCommand;
  final String unitNo;
  final String buildingName;
  final double phSafeLow;
  final double phSafeHigh;

  /// Put a reading that arrived over the live connection into the screen.
  void applyLive(Map<String, dynamic> e) {
    online = true;
    lastSeenAt = e['time'] as String? ?? lastSeenAt;
    valve = e['valve'] as String? ?? valve;
    reading = Reading(
      time: e['time'] as String? ?? DateTime.now().toUtc().toIso8601String(),
      flowLpm: _d0(e['flowLpm']),
      ph: _d0(e['ph']),
      tempC: _d0(e['tempC']),
      valve: e['valve'] as String? ?? '',
      totalL: _d0(e['totalL']),
    );
  }
}

class Point {
  Point(this.start, this.value);
  final String start;
  final double value;
}

class Consumption {
  Consumption.fromJson(Map<String, dynamic> j)
    : granularity = j['granularity'] as String,
      from = j['from'] as String,
      to = j['to'] as String,
      totalLitres = _d0(j['total_litres']),
      points = [for (final p in j['points'] as List) Point(p['start'] as String, _d0(p['litres']))];
  final String granularity;
  final String from;
  final String to;
  final double totalLitres;
  final List<Point> points;
}

class Comparison {
  Comparison.fromJson(Map<String, dynamic> j)
    : litres = _d0(j['litres']),
      previousMonthLitres = _d(j['previous_month_litres']),
      peopleInHome = (j['people_in_home'] as num? ?? 0).toInt(),
      litresPerPerson = _d(j['litres_per_person']),
      buildingLitresPerPerson = _d(j['building_litres_per_person']),
      occupiedHomes = (j['occupied_homes'] as num? ?? 0).toInt();
  final double litres;
  final double? previousMonthLitres;
  final int peopleInHome;
  final double? litresPerPerson;

  /// Only sent when at least 3 homes are occupied (FR-49).
  final double? buildingLitresPerPerson;
  final int occupiedHomes;
}

class BillEstimate {
  BillEstimate.fromJson(Map<String, dynamic> j)
    : totalText = j['total_text'] as String?,
      message = j['message'] as String? ?? '';
  final String? totalText;
  final String message;
}

class BillSummary {
  BillSummary.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      homeId = j['home_id'] as String,
      billNo = j['bill_no'] as String?,
      unitNo = j['unit_no'] as String? ?? '',
      billTo = j['bill_to'] as String? ?? '',
      month = j['month'] as String,
      currency = j['currency_code'] as String,
      litres = j['litres'] as String? ?? '0',
      total = j['total'] as String,
      amountPaid = j['amount_paid'] as String? ?? '0',
      outstanding = j['outstanding'] as String? ?? '0',
      totalText = j['total_text'] as String,
      status = j['status'] as String,
      dueDate = j['due_date'] as String?;
  final String id;
  final String homeId;
  final String? billNo;
  final String unitNo;
  final String billTo;
  final String month;
  final String currency;
  final String litres;

  /// Exact decimals arrive as text, so money is never rounded wrongly.
  final String total;
  final String amountPaid;
  final String outstanding;
  final String totalText;

  /// draft, issued, partially_paid, paid, overdue or void
  final String status;
  final String? dueDate;
}

class BillLine {
  BillLine.fromJson(Map<String, dynamic> j)
    : kind = j['kind'] as String,
      description = j['description'] as String,
      amount = j['amount'] as String;
  final String kind;
  final String description;
  final String amount;
}

class Payment {
  Payment.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      amount = j['amount'] as String,
      method = j['method'] as String,
      reference = j['reference'] as String? ?? '',
      receivedOn = j['received_on'] as String,
      reversedAt = j['reversed_at'] as String?;
  final String id;
  final String amount;
  final String method;
  final String reference;
  final String receivedOn;
  final String? reversedAt;
}

class Bill extends BillSummary {
  Bill.fromJson(super.j)
    : lines = [for (final l in j['lines'] as List? ?? const []) BillLine.fromJson(l as Map<String, dynamic>)],
      payments = [for (final p in j['payments'] as List? ?? const []) Payment.fromJson(p as Map<String, dynamic>)],
      subtotal = j['subtotal'] as String? ?? '',
      taxTotal = j['tax_total'] as String? ?? '',
      voidReason = j['void_reason'] as String?,
      canPayOnline = j['can_pay_online'] as bool? ?? false,
      super.fromJson();
  final List<BillLine> lines;
  final List<Payment> payments;
  final String subtotal;
  final String taxTotal;
  final String? voidReason;
  final bool canPayOnline;
}

class Alert {
  Alert.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      rule = j['rule'] as String,
      severity = j['severity'] as String,
      state = j['state'] as String,
      homeId = j['home_id'] as String?,
      title = j['title'] as String,
      message = j['message'] as String? ?? '',
      openedAt = j['opened_at'] as String,
      seenCount = (j['seen_count'] as num? ?? 1).toInt();
  final String id;
  final String rule;

  /// info, warning or critical
  final String severity;

  /// open, acknowledged, resolved or auto_resolved
  final String state;
  final String? homeId;
  final String title;
  final String message;
  final String openedAt;
  final int seenCount;

  bool get isOpen => state == 'open' || state == 'acknowledged';
}

class NotificationPrefs {
  NotificationPrefs.fromJson(Map<String, dynamic> j)
    : emailMin = j['alert_email_min'] as String,
      mutedRules = _strings(j['email_muted_rules']).toSet(),
      quietFrom = j['quiet_from'] as String?,
      quietUntil = j['quiet_until'] as String?,
      pushMin = j['push_min'] as String? ?? 'warning',
      pushMutedRules = _strings(j['push_muted_rules']).toSet(),
      pushAvailable = j['push_available'] as bool? ?? false;
  String emailMin;
  Set<String> mutedRules;
  String? quietFrom;
  String? quietUntil;

  /// M9: push to this phone from this severity up, and types never pushed.
  String pushMin;
  Set<String> pushMutedRules;

  /// Is push set up on the server (a Firebase project)?
  final bool pushAvailable;

  Map<String, dynamic> toJson() => {
    'alert_email_min': emailMin,
    'email_muted_rules': mutedRules.toList()..sort(),
    'quiet_from': quietFrom,
    'quiet_until': quietUntil,
    'push_min': pushMin,
    'push_muted_rules': pushMutedRules.toList()..sort(),
  };
}
