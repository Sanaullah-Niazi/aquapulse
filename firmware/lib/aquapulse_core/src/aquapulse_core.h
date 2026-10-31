// AquaPulse device logic in plain C++ (no Arduino), so it is the same on the
// ESP32 and in the tests that run on a computer (make firmware-test).
//
// Everything here follows the device contract in /contracts (FR-10): the
// JSON messages, the command rules (expired, repeated, wrong device ...),
// and the device rules of FR-15 to FR-17 (counter saving, back-off, pH
// calibration, local pump control, filtered tank level).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace aquapulse {

constexpr int CONTRACT_VERSION = 1;
constexpr size_t MAX_PAYLOAD = 2048;  // the broker and server refuse bigger messages
constexpr const char* FW_VERSION = "1.0.0";

// ----- time -----

/// Milliseconds since 1970-01-01 UTC -> "2026-10-30T08:15:20.250Z". out: 25 bytes.
void formatTimestamp(int64_t epochMs, char* out, size_t size);

/// "2026-10-30T08:15:20Z" or with .123Z -> milliseconds since 1970 UTC.
/// Returns false if the text is not a contract timestamp.
bool parseTimestamp(const char* text, int64_t& epochMs);

// ----- ids -----

/// A random UUID v4 from 16 random bytes (esp_random() on the device). out: 37 bytes.
void uuidFromBytes(const uint8_t bytes[16], char* out, size_t size);

/// Is this a UUID in the contract's lower-case 8-4-4-4-12 form?
bool isUuid(const char* text);

// ----- reconnect back-off (FR-17) -----

/// 1 s, 2 s, 4 s ... at most 60 s, plus up to 20% random jitter, so many
/// devices do not all reconnect at the same moment after a broker restart.
class Backoff {
 public:
  /// The wait before the next try, in ms. `random01` is a number in [0, 1).
  uint32_t next(double random01);
  void reset() { attempt_ = 0; }
  int attempts() const { return attempt_; }

 private:
  int attempt_ = 0;
};

// ----- saving the litre counter (FR-17) -----

/// Saves the cumulative counter to flash at most once per 60 s, or sooner
/// after 10 L, so a reboot loses at most 10 L and the flash is not worn out.
class CounterSaver {
 public:
  static constexpr uint32_t MIN_INTERVAL_MS = 60000;
  static constexpr double MAX_UNSAVED_L = 10.0;

  void restored(double totalL, uint32_t nowMs) {
    savedL_ = totalL;
    savedAtMs_ = nowMs;
  }
  /// Should the counter be written to flash now?
  bool shouldSave(double totalL, uint32_t nowMs) const;
  void saved(double totalL, uint32_t nowMs) { restored(totalL, nowMs); }
  double savedL() const { return savedL_; }

 private:
  double savedL_ = 0;
  uint32_t savedAtMs_ = 0;
};

// ----- flow meter -----

/// YF-S201 style sensor: about 7.5 pulses per second for each L/min,
/// so 450 pulses per litre (set per sensor in config.h).
struct FlowMeter {
  double pulsesPerLitre = 450.0;
  double litres(uint32_t pulses) const { return pulses / pulsesPerLitre; }
  double lpm(uint32_t pulses, uint32_t windowMs) const {
    return windowMs == 0 ? 0.0 : litres(pulses) * 60000.0 / windowMs;
  }
};

// ----- pH with two-point calibration (FR-15) -----

/// Two buffers (pH 4.00 and 7.00) give a straight line from probe voltage to
/// pH. The two voltages are measured once and kept in flash.
struct PhCalibration {
  double mv4 = 2032.0;  // probe output in buffer pH 4.00 (typical PH-4502C)
  double mv7 = 1500.0;  // probe output in buffer pH 7.00
  bool valid() const { return mv4 - mv7 > 50.0 || mv7 - mv4 > 50.0; }
  double ph(double mv) const;
};

// ----- tank level (FR-16) -----

/// The median of the last 5 readings: one wrong echo never moves the level.
class Median5 {
 public:
  void add(double value);
  bool ready() const { return count_ >= 5; }
  double value() const;

 private:
  double values_[5] = {0, 0, 0, 0, 0};
  int count_ = 0;
  int next_ = 0;
};

/// Ultrasonic distance (cm, from the sensor at the top) -> % full.
double levelPercent(double distanceCm, double emptyCm, double fullCm);

/// The same pump rules as the server (FR-07), run on the tank node itself so
/// the tank is safe without internet. Auto mode: pump on below lowPct, off at
/// highPct (hysteresis). Never on with an empty sump (dry run) or a full tank.
struct PumpInputs {
  double levelPct;
  bool sensorOk;
  bool sumpOk;
};

constexpr double OVERFLOW_PCT = 98;       // pump always off at or above this (same as the simulator)
constexpr double MIN_THRESHOLD_GAP = 10;  // highPct - lowPct must be at least this
constexpr int SENSOR_FAILURES_BEFORE_STOP = 5;

struct PumpRules {
  double lowPct = 25;
  double highPct = 95;
  double overflowPct = OVERFLOW_PCT;
  bool manual = false;
};

/// New thresholds from a command: in the contract's range and at least 10 points apart.
bool thresholdsOk(double lowPct, double highPct);

/// A manual "pump on/off" command (FR-07 AC4). nullptr: do it. Otherwise the
/// ack reason: NOT_MANUAL_MODE, UNSAFE_DRY_RUN or UNSAFE_OVERFLOW.
const char* checkPumpCommand(const PumpRules& rules, const PumpInputs& in, bool turnOn);

/// The tank's level sensor (FR-16): one reading per second, the median of
/// the last 5, and "failed" only after 5 bad readings in a row, so one lost
/// echo neither moves the level nor stops the pump.
class LevelSensor {
 public:
  LevelSensor(double emptyCm, double fullCm) : emptyCm_(emptyCm), fullCm_(fullCm) {}
  /// valid = false when the sensor gave no echo (or an impossible distance).
  void add(bool valid, double distanceCm);
  /// The last reading was good (what telemetry reports as sensorOk).
  bool readingOk() const { return lastOk_; }
  /// Good enough to control the pump: 5 readings seen and fewer than 5 failures in a row.
  bool ok() const { return median_.ready() && failures_ < SENSOR_FAILURES_BEFORE_STOP; }
  double percent() const { return levelPercent(median_.value(), emptyCm_, fullCm_); }

 private:
  double emptyCm_, fullCm_;
  Median5 median_;
  int failures_ = 0;
  bool lastOk_ = false;
};

struct PumpDecision {
  bool on;
  bool dryRunProtect;
  bool overflowProtect;
};

/// The pump state after this reading. `wasOn` is the current state;
/// `manualOn` is what the owner asked for in manual mode.
PumpDecision decidePump(const PumpRules& rules, const PumpInputs& in, bool wasOn, bool manualOn);

// ----- commands (contract cmd.v1, FR-04, FR-13) -----

enum class DeviceKind { Home, Tank };

/// A command as read from JSON by the firmware (ArduinoJson). Strings are
/// empty when the field is missing.
struct Command {
  int v = 0;
  const char* cmdId = "";
  const char* type = "";
  const char* action = "";
  const char* issuedAt = "";
  const char* expiresAt = "";
  const char* by = "";
  bool hasLowPct = false, hasHighPct = false, hasSeconds = false;
  double lowPct = 0, highPct = 0;
  long seconds = 0;
  bool unknownField = false;  // additionalProperties: false
  bool parsed = true;         // false: not JSON at all
};

/// What the device does with it. reason is nullptr for "ok".
struct Verdict {
  bool accept;
  const char* reason;  // INVALID, EXPIRED, DUPLICATE, UNSUPPORTED
};

/// Remembers the last command IDs, so a repeated command is refused.
class SeenCommands {
 public:
  static constexpr int SIZE = 16;
  bool contains(const char* cmdId) const;
  void add(const char* cmdId);

 private:
  char ids_[SIZE][37] = {};
  int next_ = 0;
};

/// The contract checks every device does before acting on a command.
/// `nowMs` is UTC epoch ms (from NTP); 0 if the clock is not set yet, then
/// expiry cannot be checked and the command is accepted if otherwise valid.
Verdict checkCommand(const Command& cmd, DeviceKind kind, int64_t nowMs, const SeenCommands& seen);

// ----- messages (contract schemas) -----

struct HomeTelemetry {
  int64_t tsMs;
  const char* bootId;
  uint32_t seq;
  double totalL;
  double flowLpm;
  double ph;
  double tempC;
  const char* valve;  // open, closed, moving, fault
  int rssi;
  bool boot;
};

struct TankTelemetry {
  int64_t tsMs;
  const char* bootId;
  uint32_t seq;
  double levelPct;
  double capacityL;
  bool pumpOn, sumpOk, sensorOk, dryRunProtect, overflowProtect, manual;
  double lowPct, highPct;
  int rssi;
  bool boot;
};

/// Each returns the JSON length, or 0 if it did not fit in `size`.
size_t homeTelemetryJson(const HomeTelemetry& t, char* out, size_t size);
size_t tankTelemetryJson(const TankTelemetry& t, char* out, size_t size);
size_t statusJson(bool online, char* out, size_t size);
size_t homeStateJson(int64_t tsMs, const char* valve, int intervalS, char* out, size_t size);
size_t tankStateJson(int64_t tsMs, bool pumpOn, bool manual, double lowPct, double highPct, int intervalS,
                     char* out, size_t size);
/// result: ok, rejected, failed. reason: nullptr or a contract reason. state: e.g. "closed" (may be nullptr).
size_t ackJson(const char* cmdId, const char* result, const char* reason, const char* state, int64_t tsMs,
               char* out, size_t size);

/// "aquapulse/v1/dev/home-b01-03/telemetry". out: 64 bytes is plenty.
void topic(const char* deviceId, const char* channel, char* out, size_t size);

}  // namespace aquapulse
