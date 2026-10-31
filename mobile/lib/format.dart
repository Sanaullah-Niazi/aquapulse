// Numbers and dates for people. One place, so every screen looks the same.
import 'package:intl/intl.dart';

String litres(num? value) => value == null ? '-' : '${NumberFormat.decimalPattern().format(value.round())} L';

String number(num? value, [int digits = 1]) => value == null ? '-' : value.toStringAsFixed(digits);

/// "2026-10-30" is a calendar day: show it as it is, whatever the phone's time zone.
String date(String? iso) {
  if (iso == null) return '-';
  final p = iso.substring(0, 10).split('-').map(int.parse).toList();
  return DateFormat.yMMMd().format(DateTime(p[0], p[1], p[2]));
}

String dateTime(String? iso) => iso == null ? '-' : DateFormat.yMMMd().add_Hm().format(DateTime.parse(iso).toLocal());

String month(String iso) {
  final p = iso.substring(0, 7).split('-').map(int.parse).toList();
  return DateFormat.yMMMM().format(DateTime(p[0], p[1]));
}

/// "3 min ago", "2 h ago"
String ago(String? iso, [DateTime? now]) {
  if (iso == null) return '-';
  final seconds = (now ?? DateTime.now()).difference(DateTime.parse(iso)).inSeconds;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return '${seconds ~/ 60} min ago';
  if (seconds < 86400) return '${seconds ~/ 3600} h ago';
  return '${seconds ~/ 86400} days ago';
}

/// Money from the API (exact text like "1234.500") with the bill's own decimals.
String money(String amount, String sample) {
  final decimals = sample.contains('.') ? sample.split('.')[1].length : 0;
  return NumberFormat.decimalPatternDigits(decimalDigits: decimals).format(double.parse(amount));
}

String isoDay(DateTime d) => DateFormat('yyyy-MM-dd').format(d);

enum Quality { safe, check, unsafe, unknown }

/// Water quality label from pH (FR-48). The limits are the building's own
/// alert settings (default: safe from 6.5 to 8.5). "Check" = within 0.3 of a limit.
Quality phLabel(double? ph, [double low = 6.5, double high = 8.5]) {
  if (ph == null) return Quality.unknown;
  if (ph < low || ph > high) return Quality.unsafe;
  if (ph < low + 0.3 || ph > high - 0.3) return Quality.check;
  return Quality.safe;
}
