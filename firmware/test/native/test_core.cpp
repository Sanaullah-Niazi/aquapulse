// Tests for the firmware's device logic, run on a computer (no ESP32 needed):
//   make firmware-test
// With --dump, it prints one example of every message instead, so
// check_contract.py can validate them against the /contracts schemas.
#include <math.h>
#include <stdio.h>
#include <string.h>

#include <initializer_list>

#include "aquapulse_core.h"

using namespace aquapulse;

static int failures = 0;
static int checks = 0;

#define CHECK(cond)                                                \
  do {                                                             \
    checks++;                                                      \
    if (!(cond)) {                                                 \
      failures++;                                                  \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);       \
    }                                                              \
  } while (0)
#define CHECK_STR(a, b) CHECK(strcmp((a), (b)) == 0)
#define CHECK_NEAR(a, b, eps) CHECK(fabs((a) - (b)) <= (eps))

static const char* UUID1 = "0d6a6a2e-8f0e-4a8e-bb2a-4b9f2b7e0c11";
static const char* UUID2 = "6f1c2b8e-3a0d-4f43-9a51-0e8f2a7c1d55";
static const int64_t NOW = 1793434520000LL;  // 2026-10-31T08:15:20Z

static void testTimestamps() {
  char buf[32];
  formatTimestamp(0, buf, sizeof buf);
  CHECK_STR(buf, "1970-01-01T00:00:00.000Z");
  formatTimestamp(NOW + 250, buf, sizeof buf);
  CHECK_STR(buf, "2026-10-31T08:15:20.250Z");
  formatTimestamp(951782400000LL, buf, sizeof buf);  // leap day 2000
  CHECK_STR(buf, "2000-02-29T00:00:00.000Z");

  int64_t ms = 0;
  CHECK(parseTimestamp("2026-10-31T08:15:20Z", ms) && ms == NOW);
  CHECK(parseTimestamp("2026-10-31T08:15:20.25Z", ms) && ms == NOW + 250);
  CHECK(parseTimestamp("2026-10-31T08:15:20.123456Z", ms) && ms == NOW + 123);
  CHECK(!parseTimestamp("2026-10-31 08:15:20Z", ms));
  CHECK(!parseTimestamp("2026-10-31T08:15:20+05:00", ms));
  CHECK(!parseTimestamp("2026-13-01T00:00:00Z", ms));
  CHECK(!parseTimestamp("", ms));
  // Round trip.
  formatTimestamp(NOW + 7, buf, sizeof buf);
  CHECK(parseTimestamp(buf, ms) && ms == NOW + 7);
}

static void testUuids() {
  uint8_t bytes[16];
  for (int i = 0; i < 16; i++) bytes[i] = static_cast<uint8_t>(0xF0 + i);
  char id[40];
  uuidFromBytes(bytes, id, sizeof id);
  CHECK(isUuid(id));
  CHECK(id[14] == '4');                                     // version 4
  CHECK(id[19] == '8' || id[19] == '9' || id[19] == 'a' || id[19] == 'b');  // variant
  CHECK(isUuid(UUID1));
  CHECK(!isUuid("0D6A6A2E-8F0E-4A8E-BB2A-4B9F2B7E0C11"));  // the contract wants lower case
  CHECK(!isUuid("not-a-uuid"));
}

static void testBackoff() {
  // FR-17: 1 s, 2 s, 4 s ... at most 60 s, plus up to 20% jitter.
  Backoff b;
  const uint32_t expected[] = {1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000};
  for (uint32_t e : expected) CHECK(b.next(0.0) == e);
  b.reset();
  const uint32_t first = b.next(0.5);
  CHECK(first == 1100);
  Backoff c;
  for (int i = 0; i < 20; i++) CHECK(c.next(0.999) <= 72000);
}

static void testCounterSaver() {
  // FR-17: at most once per 60 s, or after 10 L; a reboot loses at most 10 L.
  CounterSaver s;
  s.restored(1000.0, 0);
  CHECK(!s.shouldSave(1000.0, 120000));  // nothing new: no write
  CHECK(!s.shouldSave(1005.0, 30000));   // 5 L after 30 s: wait
  CHECK(s.shouldSave(1005.0, 60000));    // 60 s later: save
  CHECK(s.shouldSave(1010.0, 1000));     // 10 L: save at once
  s.saved(1010.0, 1000);
  CHECK(!s.shouldSave(1019.9, 2000));
  CHECK(s.savedL() == 1010.0);
  // millis() wraps after 49 days: still works.
  CounterSaver w;
  w.restored(0, 0xFFFFF000u);
  CHECK(w.shouldSave(1.0, 0xFFFFF000u + 60000u));
}

static void testFlowMeter() {
  FlowMeter f;  // 450 pulses per litre
  CHECK_NEAR(f.litres(450), 1.0, 1e-9);
  CHECK_NEAR(f.lpm(75, 10000), 1.0, 1e-9);  // 7.5 Hz = 1 L/min
  CHECK(f.lpm(10, 0) == 0.0);
}

static void testPh() {
  // FR-15: two-point calibration with pH 4.00 and 7.00 buffers.
  PhCalibration cal;
  cal.mv4 = 2030;
  cal.mv7 = 1500;
  CHECK_NEAR(cal.ph(1500), 7.0, 1e-9);
  CHECK_NEAR(cal.ph(2030), 4.0, 1e-9);
  CHECK_NEAR(cal.ph(1323.33), 8.0, 0.01);  // straight line beyond the buffers
  CHECK(cal.ph(10000) == 0.0);              // clamped to 0..14
  PhCalibration bad;
  bad.mv4 = bad.mv7 = 1500;  // not calibrated: report neutral, never nonsense
  CHECK(!bad.valid() && bad.ph(1234) == 7.0);
}

static void testMedianAndLevel() {
  // FR-16: one wrong echo does not move the level.
  Median5 m;
  for (double v : {50.0, 51.0, 49.0, 200.0, 50.5}) m.add(v);
  CHECK(m.ready());
  CHECK_NEAR(m.value(), 50.5, 1e-9);
  m.add(0.0);  // another wrong one replaces the oldest
  CHECK_NEAR(m.value(), 50.5, 1e-9);
  CHECK_NEAR(levelPercent(120, 120, 20), 0, 1e-9);
  CHECK_NEAR(levelPercent(20, 120, 20), 100, 1e-9);
  CHECK_NEAR(levelPercent(70, 120, 20), 50, 1e-9);
  CHECK_NEAR(levelPercent(5, 120, 20), 100, 1e-9);  // closer than "full": clamped
}

static void testPump() {
  // FR-16 / FR-07: the same hysteresis as the server, run locally.
  PumpRules r;  // 25 / 95, overflow 98
  PumpInputs in{20, true, true};
  CHECK(decidePump(r, in, false, false).on);    // below low: on
  in.levelPct = 60;
  CHECK(decidePump(r, in, true, false).on);     // between: keeps running
  CHECK(!decidePump(r, in, false, false).on);   // between: stays off
  in.levelPct = 95;
  CHECK(!decidePump(r, in, true, false).on);    // high: off
  in.levelPct = 20;
  in.sumpOk = false;
  PumpDecision d = decidePump(r, in, true, false);
  CHECK(!d.on && d.dryRunProtect);              // never run dry
  in.sumpOk = true;
  in.levelPct = 98;
  r.manual = true;
  d = decidePump(r, in, false, true);
  CHECK(!d.on && d.overflowProtect);            // not even in manual mode
  in.levelPct = 50;
  CHECK(decidePump(r, in, false, true).on);     // manual: what the owner asked
  CHECK(!decidePump(r, in, true, false).on);
  in.sensorOk = false;
  r.manual = false;
  CHECK(!decidePump(r, in, true, false).on);    // no level: stop, safe side
  in = PumpInputs{97, true, true};
  CHECK(!decidePump(r, in, true, false).overflowProtect);  // 97 is allowed (max highPct)
}

static void testTankRules() {
  // Thresholds: the contract's range, and at least 10 points apart (like the simulator).
  CHECK(thresholdsOk(25, 95));
  CHECK(thresholdsOk(5, 15));
  CHECK(!thresholdsOk(30, 35));
  CHECK(!thresholdsOk(4, 50));
  CHECK(!thresholdsOk(20, 98));
  CHECK(!thresholdsOk(60, 40));

  // Manual pump commands (FR-07 AC4): the same reasons as the simulator.
  PumpRules r;
  PumpInputs in{50, true, true};
  CHECK_STR(checkPumpCommand(r, in, true), "NOT_MANUAL_MODE");
  r.manual = true;
  CHECK(checkPumpCommand(r, in, true) == nullptr);
  in.sumpOk = false;
  CHECK_STR(checkPumpCommand(r, in, true), "UNSAFE_DRY_RUN");
  CHECK(checkPumpCommand(r, in, false) == nullptr);  // off is always allowed
  in.sumpOk = true;
  in.levelPct = 98;
  CHECK_STR(checkPumpCommand(r, in, true), "UNSAFE_OVERFLOW");
  in.levelPct = 50;
  in.sensorOk = false;
  CHECK_STR(checkPumpCommand(r, in, true), "UNSAFE_OVERFLOW");

  // Level sensor (FR-16 AC2): one wrong echo does not move the level.
  LevelSensor s(120, 20);  // empty at 120 cm, full at 20 cm
  for (int i = 0; i < 4; i++) s.add(true, 70);
  CHECK(!s.ok());          // fewer than 5 readings yet
  s.add(true, 70);
  CHECK(s.ok());
  CHECK_NEAR(s.percent(), 50, 1e-9);
  s.add(true, 20);         // one bad echo says "full"
  CHECK_NEAR(s.percent(), 50, 1e-9);
  CHECK(s.ok());
  // Lost echoes: still ok (last level kept) until 5 in a row.
  for (int i = 0; i < 4; i++) s.add(false, 0);
  CHECK(!s.readingOk());
  CHECK(s.ok());
  s.add(false, 0);
  CHECK(!s.ok());          // 5 in a row: the pump stops (safe side)
  s.add(true, 70);
  CHECK(s.ok() && s.readingOk());
}

static Command valveCmd(const char* id = UUID1) {
  Command c;
  c.v = 1;
  c.cmdId = id;
  c.type = "valve";
  c.action = "close";
  c.issuedAt = "2026-10-31T08:15:00Z";
  c.expiresAt = "2026-10-31T08:16:00Z";
  c.by = "resident";
  return c;
}

static void testCommands() {
  SeenCommands seen;
  Verdict v = checkCommand(valveCmd(), DeviceKind::Home, NOW, seen);
  CHECK(v.accept && v.reason == nullptr);

  // Expired: arrived after expiresAt.
  v = checkCommand(valveCmd(), DeviceKind::Home, NOW + 60000, seen);
  CHECK(!v.accept && strcmp(v.reason, "EXPIRED") == 0);
  // The clock is not set yet: cannot tell, so accept.
  CHECK(checkCommand(valveCmd(), DeviceKind::Home, 0, seen).accept);

  // Duplicate.
  seen.add(UUID1);
  v = checkCommand(valveCmd(), DeviceKind::Home, NOW, seen);
  CHECK(!v.accept && strcmp(v.reason, "DUPLICATE") == 0);
  CHECK(checkCommand(valveCmd(UUID2), DeviceKind::Home, NOW, seen).accept);
  // Only the last 16 are remembered.
  SeenCommands many;
  many.add(UUID1);
  for (int i = 0; i < SeenCommands::SIZE; i++) many.add(UUID2);
  CHECK(!many.contains(UUID1) && many.contains(UUID2));

  // Wrong device.
  v = checkCommand(valveCmd(UUID2), DeviceKind::Tank, NOW, SeenCommands());
  CHECK(!v.accept && strcmp(v.reason, "UNSUPPORTED") == 0);
  Command pump = valveCmd(UUID2);
  pump.type = "pump";
  pump.action = "on";
  v = checkCommand(pump, DeviceKind::Home, NOW, SeenCommands());
  CHECK(!v.accept && strcmp(v.reason, "UNSUPPORTED") == 0);
  CHECK(checkCommand(pump, DeviceKind::Tank, NOW, SeenCommands()).accept);

  // Invalid in many ways.
  SeenCommands none;
  Command c = valveCmd();
  c.action = "on";
  CHECK(strcmp(checkCommand(c, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);
  c = valveCmd();
  c.v = 2;
  CHECK(strcmp(checkCommand(c, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);
  c = valveCmd("ABC");
  CHECK(strcmp(checkCommand(c, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);
  c = valveCmd();
  c.unknownField = true;
  CHECK(strcmp(checkCommand(c, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);
  c = valveCmd();
  c.parsed = false;
  CHECK(strcmp(checkCommand(c, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);
  c = valveCmd();
  c.by = "hacker";
  CHECK(strcmp(checkCommand(c, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);
  c = valveCmd();
  c.expiresAt = "soon";
  CHECK(strcmp(checkCommand(c, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);

  Command th = valveCmd();
  th.type = "thresholds";
  th.action = "";
  th.hasLowPct = th.hasHighPct = true;
  th.lowPct = 30;
  th.highPct = 90;
  CHECK(checkCommand(th, DeviceKind::Tank, NOW, none).accept);
  th.lowPct = 95;  // out of range and not below high
  CHECK(strcmp(checkCommand(th, DeviceKind::Tank, NOW, none).reason, "INVALID") == 0);
  th.hasHighPct = false;
  th.lowPct = 30;
  CHECK(strcmp(checkCommand(th, DeviceKind::Tank, NOW, none).reason, "INVALID") == 0);

  Command iv = valveCmd();
  iv.type = "interval";
  iv.action = "";
  iv.hasSeconds = true;
  iv.seconds = 30;
  CHECK(checkCommand(iv, DeviceKind::Home, NOW, none).accept);  // any device
  CHECK(checkCommand(iv, DeviceKind::Tank, NOW, none).accept);
  iv.seconds = 301;
  CHECK(strcmp(checkCommand(iv, DeviceKind::Home, NOW, none).reason, "INVALID") == 0);
}

static void testMessages() {
  char buf[MAX_PAYLOAD];
  HomeTelemetry h{NOW, UUID2, 4821, 128734.62, 7.85, 7.21, 24.6, "open", -61, false};
  size_t n = homeTelemetryJson(h, buf, sizeof buf);
  CHECK(n > 0 && n == strlen(buf));
  CHECK(strstr(buf, "\"totalL\":128734.62") != nullptr);
  CHECK(strstr(buf, "\"boot\":false}") != nullptr);
  CHECK(strstr(buf, "\"ts\":\"2026-10-31T08:15:20.000Z\"") != nullptr);
  // Too small a buffer: 0, never a cut-off message.
  char tiny[40];
  CHECK(homeTelemetryJson(h, tiny, sizeof tiny) == 0);
  // Out-of-range sensor values are clamped to what the contract allows.
  HomeTelemetry odd{NOW, UUID2, 0, -5, 999, 15, 150, "fault", 3, true};
  homeTelemetryJson(odd, buf, sizeof buf);
  CHECK(strstr(buf, "\"totalL\":0.00,\"flowLpm\":200.00,\"ph\":14.00,\"tempC\":100.0") != nullptr);
  CHECK(strstr(buf, "\"rssi\":0") != nullptr);

  TankTelemetry t{NOW, UUID2, 1, 0, 5000, false, true, false, false, false, false, 25, 95, -58, false};
  tankTelemetryJson(t, buf, sizeof buf);
  CHECK(strstr(buf, "\"levelPct\":null,\"levelL\":null") != nullptr);

  ackJson(UUID1, "rejected", "EXPIRED", "open", NOW, buf, sizeof buf);
  CHECK(strstr(buf, "\"reason\":\"EXPIRED\"") != nullptr);
  ackJson("garbage", "rejected", "INVALID", nullptr, NOW, buf, sizeof buf);
  CHECK(strstr(buf, "\"cmdId\":null") != nullptr && strstr(buf, "\"state\":null") != nullptr);

  char t1[64];
  topic("home-b01-03", "telemetry", t1, sizeof t1);
  CHECK_STR(t1, "aquapulse/v1/dev/home-b01-03/telemetry");
}

// One example of every message, as "schema<TAB>json" lines.
static void dump() {
  char buf[MAX_PAYLOAD];
  HomeTelemetry h{NOW, UUID2, 4821, 128734.62, 7.85, 7.21, 24.6, "open", -61, false};
  homeTelemetryJson(h, buf, sizeof buf);
  printf("telemetry-home.v1\t%s\n", buf);
  HomeTelemetry boot{NOW, UUID2, 0, 0, 0, 0, -20, "moving", -120, true};
  homeTelemetryJson(boot, buf, sizeof buf);
  printf("telemetry-home.v1\t%s\n", buf);
  TankTelemetry t{NOW, UUID2, 912, 63.4, 5000, false, true, true, false, false, false, 25, 95, -58, false};
  tankTelemetryJson(t, buf, sizeof buf);
  printf("telemetry-tank.v1\t%s\n", buf);
  t.sensorOk = false;
  t.manual = true;
  tankTelemetryJson(t, buf, sizeof buf);
  printf("telemetry-tank.v1\t%s\n", buf);
  statusJson(true, buf, sizeof buf);
  printf("status.v1\t%s\n", buf);
  statusJson(false, buf, sizeof buf);
  printf("status.v1\t%s\n", buf);
  homeStateJson(NOW, "closed", 10, buf, sizeof buf);
  printf("state-home.v1\t%s\n", buf);
  tankStateJson(NOW, true, false, 25, 95, 10, buf, sizeof buf);
  printf("state-tank.v1\t%s\n", buf);
  ackJson(UUID1, "ok", nullptr, "closed", NOW, buf, sizeof buf);
  printf("ack.v1\t%s\n", buf);
  ackJson(UUID1, "failed", "VALVE_STUCK", "fault", NOW, buf, sizeof buf);
  printf("ack.v1\t%s\n", buf);
  ackJson("x", "rejected", "INVALID", nullptr, NOW, buf, sizeof buf);
  printf("ack.v1\t%s\n", buf);
}

int main(int argc, char** argv) {
  if (argc > 1 && strcmp(argv[1], "--dump") == 0) {
    dump();
    return 0;
  }
  testTimestamps();
  testUuids();
  testBackoff();
  testCounterSaver();
  testFlowMeter();
  testPh();
  testMedianAndLevel();
  testPump();
  testTankRules();
  testCommands();
  testMessages();
  printf("%d checks, %d failed\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
