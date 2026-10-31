// See aquapulse_core.h. Plain C++: builds for the ESP32 and for the tests on a computer.
#include "aquapulse_core.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

namespace aquapulse {

// ----- time -----

namespace {

// Days since 1970-01-01 for a civil date (Howard Hinnant's algorithm).
int64_t daysFromCivil(int64_t y, unsigned m, unsigned d) {
  y -= m <= 2;
  const int64_t era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = static_cast<unsigned>(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + static_cast<int64_t>(doe) - 719468;
}

void civilFromDays(int64_t z, int& y, unsigned& m, unsigned& d) {
  z += 719468;
  const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const unsigned doe = static_cast<unsigned>(z - era * 146097);
  const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  const int64_t yy = static_cast<int64_t>(yoe) + era * 400;
  const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const unsigned mp = (5 * doy + 2) / 153;
  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp + (mp < 10 ? 3 : -9);
  y = static_cast<int>(yy + (m <= 2));
}

bool digits(const char* s, int n, int& value) {
  value = 0;
  for (int i = 0; i < n; i++) {
    if (s[i] < '0' || s[i] > '9') return false;
    value = value * 10 + (s[i] - '0');
  }
  return true;
}

// Appends to a buffer and remembers if it ever ran out of room.
struct Writer {
  char* out;
  size_t size;
  size_t len = 0;
  bool ok = true;

  Writer(char* o, size_t s) : out(o), size(s) {
    if (size > 0) out[0] = '\0';
  }
  void add(const char* fmt, ...) __attribute__((format(printf, 2, 3)));
  size_t done() const { return ok ? len : 0; }
};

void Writer::add(const char* fmt, ...) {
  if (!ok) return;
  va_list args;
  va_start(args, fmt);
  const int n = vsnprintf(out + len, size - len, fmt, args);
  va_end(args);
  if (n < 0 || static_cast<size_t>(n) >= size - len) {
    ok = false;
    return;
  }
  len += static_cast<size_t>(n);
}

}  // namespace

void formatTimestamp(int64_t epochMs, char* out, size_t size) {
  int64_t secs = epochMs / 1000;
  int ms = static_cast<int>(epochMs % 1000);
  if (ms < 0) {
    ms += 1000;
    secs -= 1;
  }
  int64_t days = secs / 86400;
  int64_t rest = secs % 86400;
  if (rest < 0) {
    rest += 86400;
    days -= 1;
  }
  int y;
  unsigned m, d;
  civilFromDays(days, y, m, d);
  snprintf(out, size, "%04d-%02u-%02uT%02d:%02d:%02d.%03dZ", y, m, d, static_cast<int>(rest / 3600),
           static_cast<int>(rest % 3600 / 60), static_cast<int>(rest % 60), ms);
}

bool parseTimestamp(const char* t, int64_t& epochMs) {
  if (t == nullptr) return false;
  const size_t n = strlen(t);
  if (n < 20 || t[4] != '-' || t[7] != '-' || t[10] != 'T' || t[13] != ':' || t[16] != ':') return false;
  int y, mo, d, h, mi, s;
  if (!digits(t, 4, y) || !digits(t + 5, 2, mo) || !digits(t + 8, 2, d) || !digits(t + 11, 2, h) ||
      !digits(t + 14, 2, mi) || !digits(t + 17, 2, s))
    return false;
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 60) return false;
  int ms = 0;
  size_t i = 19;
  if (t[i] == '.') {
    i++;
    int count = 0;
    int frac = 0;
    while (i < n && t[i] >= '0' && t[i] <= '9') {
      if (count < 3) frac = frac * 10 + (t[i] - '0');
      count++;
      i++;
    }
    if (count < 1 || count > 6) return false;
    for (int k = count; k < 3; k++) frac *= 10;
    ms = frac;
  }
  if (i != n - 1 || t[i] != 'Z') return false;
  epochMs = (daysFromCivil(y, static_cast<unsigned>(mo), static_cast<unsigned>(d)) * 86400 + h * 3600 + mi * 60 + s) *
                1000 +
            ms;
  return true;
}

// ----- ids -----

void uuidFromBytes(const uint8_t in[16], char* out, size_t size) {
  uint8_t b[16];
  memcpy(b, in, 16);
  b[6] = static_cast<uint8_t>((b[6] & 0x0F) | 0x40);  // version 4
  b[8] = static_cast<uint8_t>((b[8] & 0x3F) | 0x80);  // variant 10xx
  snprintf(out, size, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", b[0], b[1], b[2], b[3],
           b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
}

bool isUuid(const char* t) {
  if (t == nullptr || strlen(t) != 36) return false;
  for (int i = 0; i < 36; i++) {
    const bool dash = i == 8 || i == 13 || i == 18 || i == 23;
    if (dash) {
      if (t[i] != '-') return false;
    } else if (!((t[i] >= '0' && t[i] <= '9') || (t[i] >= 'a' && t[i] <= 'f'))) {
      return false;
    }
  }
  return true;
}

// ----- back-off -----

uint32_t Backoff::next(double random01) {
  double base = 1000.0;
  for (int i = 0; i < attempt_ && base < 60000.0; i++) base *= 2;
  if (base > 60000.0) base = 60000.0;
  attempt_++;
  if (random01 < 0) random01 = 0;
  if (random01 >= 1) random01 = 0.999;
  return static_cast<uint32_t>(base * (1.0 + 0.2 * random01));
}

// ----- counter -----

bool CounterSaver::shouldSave(double totalL, uint32_t nowMs) const {
  const double unsaved = totalL - savedL_;
  if (unsaved <= 0) return false;
  if (unsaved >= MAX_UNSAVED_L) return true;
  return static_cast<uint32_t>(nowMs - savedAtMs_) >= MIN_INTERVAL_MS;
}

// ----- pH -----

double PhCalibration::ph(double mv) const {
  if (!valid()) return 7.0;
  const double slope = (7.0 - 4.0) / (mv7 - mv4);  // pH per mV (negative for most probes)
  double value = 7.0 + (mv - mv7) * slope;
  if (value < 0) value = 0;
  if (value > 14) value = 14;
  return value;
}

// ----- tank -----

void Median5::add(double v) {
  values_[next_] = v;
  next_ = (next_ + 1) % 5;
  if (count_ < 5) count_++;
}

double Median5::value() const {
  double s[5];
  const int n = count_;
  for (int i = 0; i < n; i++) s[i] = values_[i];
  for (int i = 1; i < n; i++)  // insertion sort: 5 numbers
    for (int j = i; j > 0 && s[j - 1] > s[j]; j--) {
      const double t = s[j];
      s[j] = s[j - 1];
      s[j - 1] = t;
    }
  return n == 0 ? 0 : s[n / 2];
}

double levelPercent(double distanceCm, double emptyCm, double fullCm) {
  if (emptyCm <= fullCm) return 0;
  double pct = (emptyCm - distanceCm) / (emptyCm - fullCm) * 100.0;
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  return pct;
}

PumpDecision decidePump(const PumpRules& r, const PumpInputs& in, bool wasOn, bool manualOn) {
  PumpDecision d{false, false, false};
  if (!in.sumpOk) {
    d.dryRunProtect = true;  // never run the pump dry, in any mode
    return d;
  }
  if (!in.sensorOk) {
    // Without a level we cannot know the tank is not full: stop (safe side).
    return d;
  }
  if (in.levelPct >= r.overflowPct) {
    d.overflowProtect = true;
    return d;
  }
  if (r.manual) {
    d.on = manualOn;
    return d;
  }
  if (in.levelPct <= r.lowPct)
    d.on = true;
  else if (in.levelPct >= r.highPct)
    d.on = false;
  else
    d.on = wasOn;  // between the two: keep going (hysteresis)
  return d;
}

bool thresholdsOk(double lowPct, double highPct) {
  return lowPct >= 5 && lowPct <= 90 && highPct >= 15 && highPct <= 97 && highPct - lowPct >= MIN_THRESHOLD_GAP;
}

const char* checkPumpCommand(const PumpRules& rules, const PumpInputs& in, bool turnOn) {
  if (!rules.manual) return "NOT_MANUAL_MODE";  // in auto mode the tank decides
  if (!turnOn) return nullptr;                  // switching off is always safe
  if (!in.sumpOk) return "UNSAFE_DRY_RUN";
  if (!in.sensorOk || in.levelPct >= rules.overflowPct) return "UNSAFE_OVERFLOW";
  return nullptr;
}

void LevelSensor::add(bool valid, double distanceCm) {
  lastOk_ = valid && distanceCm > 0;
  if (!lastOk_) {
    if (failures_ < SENSOR_FAILURES_BEFORE_STOP) failures_++;
    return;
  }
  failures_ = 0;
  median_.add(distanceCm);
}

// ----- commands -----

bool SeenCommands::contains(const char* id) const {
  for (int i = 0; i < SIZE; i++)
    if (ids_[i][0] != '\0' && strcmp(ids_[i], id) == 0) return true;
  return false;
}

void SeenCommands::add(const char* id) {
  strncpy(ids_[next_], id, 36);
  ids_[next_][36] = '\0';
  next_ = (next_ + 1) % SIZE;
}

namespace {

bool oneOf(const char* s, const char* const* options, int n) {
  for (int i = 0; i < n; i++)
    if (strcmp(s, options[i]) == 0) return true;
  return false;
}

}  // namespace

Verdict checkCommand(const Command& c, DeviceKind kind, int64_t nowMs, const SeenCommands& seen) {
  const Verdict invalid{false, "INVALID"};
  if (!c.parsed || c.unknownField || c.v != CONTRACT_VERSION || !isUuid(c.cmdId)) return invalid;
  static const char* const types[] = {"valve", "pump", "mode", "thresholds", "interval"};
  static const char* const roles[] = {"owner", "resident", "admin", "system"};
  if (!oneOf(c.type, types, 5) || !oneOf(c.by, roles, 4)) return invalid;
  int64_t issued, expires;
  if (!parseTimestamp(c.issuedAt, issued) || !parseTimestamp(c.expiresAt, expires)) return invalid;

  const bool hasAction = c.action[0] != '\0';
  if (hasAction) {
    static const char* const actions[] = {"open", "close", "on", "off", "auto", "manual"};
    if (!oneOf(c.action, actions, 6)) return invalid;
  }
  const char* t = c.type;
  if (strcmp(t, "valve") == 0) {
    static const char* const a[] = {"open", "close"};
    if (!hasAction || !oneOf(c.action, a, 2)) return invalid;
  } else if (strcmp(t, "pump") == 0) {
    static const char* const a[] = {"on", "off"};
    if (!hasAction || !oneOf(c.action, a, 2)) return invalid;
  } else if (strcmp(t, "mode") == 0) {
    static const char* const a[] = {"auto", "manual"};
    if (!hasAction || !oneOf(c.action, a, 2)) return invalid;
  } else if (strcmp(t, "thresholds") == 0) {
    if (!c.hasLowPct || !c.hasHighPct) return invalid;
  } else if (strcmp(t, "interval") == 0) {
    if (!c.hasSeconds) return invalid;
  }
  if (c.hasLowPct && (c.lowPct < 5 || c.lowPct > 90)) return invalid;
  if (c.hasHighPct && (c.highPct < 15 || c.highPct > 97)) return invalid;
  if (c.hasLowPct && c.hasHighPct && c.lowPct >= c.highPct) return invalid;
  if (c.hasSeconds && (c.seconds < 1 || c.seconds > 300)) return invalid;

  if (seen.contains(c.cmdId)) return {false, "DUPLICATE"};
  if (nowMs > 0 && nowMs > expires) return {false, "EXPIRED"};

  const bool homeType = strcmp(t, "valve") == 0;
  const bool tankType = strcmp(t, "pump") == 0 || strcmp(t, "mode") == 0 || strcmp(t, "thresholds") == 0;
  if ((kind == DeviceKind::Home && tankType) || (kind == DeviceKind::Tank && homeType)) return {false, "UNSUPPORTED"};
  return {true, nullptr};
}

// ----- messages -----

namespace {

// Numbers as the contract expects: no exponent, a few decimals, no "nan".
void num(Writer& w, double v, int decimals) {
  if (!isfinite(v)) v = 0;
  w.add("%.*f", decimals, v);
}

}  // namespace

size_t homeTelemetryJson(const HomeTelemetry& t, char* out, size_t size) {
  Writer w(out, size);
  char ts[32];
  formatTimestamp(t.tsMs, ts, sizeof ts);
  w.add("{\"v\":1,\"ts\":\"%s\",\"bootId\":\"%s\",\"seq\":%lu,\"totalL\":", ts, t.bootId,
        static_cast<unsigned long>(t.seq));
  num(w, t.totalL < 0 ? 0 : t.totalL, 2);
  w.add(",\"flowLpm\":");
  num(w, t.flowLpm < 0 ? 0 : (t.flowLpm > 200 ? 200 : t.flowLpm), 2);
  w.add(",\"ph\":");
  num(w, t.ph < 0 ? 0 : (t.ph > 14 ? 14 : t.ph), 2);
  w.add(",\"tempC\":");
  num(w, t.tempC < -10 ? -10 : (t.tempC > 100 ? 100 : t.tempC), 1);
  w.add(",\"valve\":\"%s\",\"rssi\":%d,\"fw\":\"%s\",\"boot\":%s}", t.valve, t.rssi > 0 ? 0 : t.rssi, FW_VERSION,
        t.boot ? "true" : "false");
  return w.done();
}

size_t tankTelemetryJson(const TankTelemetry& t, char* out, size_t size) {
  Writer w(out, size);
  char ts[32];
  formatTimestamp(t.tsMs, ts, sizeof ts);
  w.add("{\"v\":1,\"ts\":\"%s\",\"bootId\":\"%s\",\"seq\":%lu,", ts, t.bootId, static_cast<unsigned long>(t.seq));
  if (t.sensorOk) {
    w.add("\"levelPct\":");
    num(w, t.levelPct, 1);
    w.add(",\"levelL\":");
    num(w, t.levelPct / 100.0 * t.capacityL, 0);
  } else {
    w.add("\"levelPct\":null,\"levelL\":null");
  }
  w.add(",\"capacityL\":");
  num(w, t.capacityL, 0);
  w.add(",\"pumpOn\":%s,\"sumpOk\":%s,\"sensorOk\":%s,\"dryRunProtect\":%s,\"overflowProtect\":%s,\"mode\":\"%s\",",
        t.pumpOn ? "true" : "false", t.sumpOk ? "true" : "false", t.sensorOk ? "true" : "false",
        t.dryRunProtect ? "true" : "false", t.overflowProtect ? "true" : "false", t.manual ? "manual" : "auto");
  w.add("\"lowPct\":");
  num(w, t.lowPct, 1);
  w.add(",\"highPct\":");
  num(w, t.highPct, 1);
  w.add(",\"rssi\":%d,\"fw\":\"%s\",\"boot\":%s}", t.rssi > 0 ? 0 : t.rssi, FW_VERSION, t.boot ? "true" : "false");
  return w.done();
}

size_t statusJson(bool online, char* out, size_t size) {
  Writer w(out, size);
  w.add("{\"v\":1,\"state\":\"%s\"}", online ? "online" : "offline");
  return w.done();
}

size_t homeStateJson(int64_t tsMs, const char* valve, int intervalS, char* out, size_t size) {
  Writer w(out, size);
  char ts[32];
  formatTimestamp(tsMs, ts, sizeof ts);
  w.add("{\"v\":1,\"ts\":\"%s\",\"valve\":\"%s\",\"intervalS\":%d,\"fw\":\"%s\"}", ts, valve, intervalS, FW_VERSION);
  return w.done();
}

size_t tankStateJson(int64_t tsMs, bool pumpOn, bool manual, double lowPct, double highPct, int intervalS, char* out,
                     size_t size) {
  Writer w(out, size);
  char ts[32];
  formatTimestamp(tsMs, ts, sizeof ts);
  w.add("{\"v\":1,\"ts\":\"%s\",\"pumpOn\":%s,\"mode\":\"%s\",\"lowPct\":", ts, pumpOn ? "true" : "false",
        manual ? "manual" : "auto");
  num(w, lowPct, 1);
  w.add(",\"highPct\":");
  num(w, highPct, 1);
  w.add(",\"intervalS\":%d,\"fw\":\"%s\"}", intervalS, FW_VERSION);
  return w.done();
}

size_t ackJson(const char* cmdId, const char* result, const char* reason, const char* state, int64_t tsMs, char* out,
               size_t size) {
  Writer w(out, size);
  char ts[32];
  formatTimestamp(tsMs, ts, sizeof ts);
  w.add("{\"v\":1,");
  if (cmdId != nullptr && isUuid(cmdId))
    w.add("\"cmdId\":\"%s\",", cmdId);
  else
    w.add("\"cmdId\":null,");
  w.add("\"result\":\"%s\",", result);
  if (reason != nullptr)
    w.add("\"reason\":\"%s\",", reason);
  else
    w.add("\"reason\":null,");
  if (state != nullptr)
    w.add("\"state\":\"%s\",", state);
  else
    w.add("\"state\":null,");
  w.add("\"ts\":\"%s\"}", ts);
  return w.done();
}

void topic(const char* deviceId, const char* channel, char* out, size_t size) {
  snprintf(out, size, "aquapulse/v1/dev/%s/%s", deviceId, channel);
}

}  // namespace aquapulse
