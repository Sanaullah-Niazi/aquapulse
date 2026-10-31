// AquaPulse tank node (FR-16, FR-17): measures the overhead tank's level and
// runs the pump by itself, so the tank is safe even with no internet or
// server (NFR-20). The server can switch auto/manual mode, change the
// thresholds (kept in flash) and, in manual mode only, switch the pump.
//
// The rules (pump hysteresis, dry-run and overflow protection, the filtered
// level, command checks) are in lib/aquapulse_core and tested on a computer.
#include <Arduino.h>

#include "aquapulse_core.h"
#include "config.h"
#include "../common/node.h"

using namespace aquapulse;
using node::flash;

namespace {

LevelSensor level(TANK_EMPTY_CM, TANK_FULL_CM);
PumpRules rules;
bool pumpOn = false;
bool manualOn = false;  // what the owner asked for in manual mode
PumpDecision last{false, false, false};
uint32_t lastReadMs = 0;

const char* pumpWord() { return pumpOn ? "on" : "off"; }

PumpInputs inputs() {
  const bool sumpOk = digitalRead(SUMP_PIN) == LOW;
  return PumpInputs{level.ok() ? level.percent() : 0.0, level.ok(), sumpOk};
}

void sendState() {
  char buf[256];
  if (tankStateJson(node::nowMs(), pumpOn, rules.manual, rules.lowPct, rules.highPct, node::intervalS, buf,
                    sizeof buf))
    node::publishState(buf);
}

void setPump(bool on) {
  if (on == pumpOn) return;
  pumpOn = on;
  digitalWrite(PUMP_RELAY_PIN, on ? HIGH : LOW);
  Serial.printf("Pump %s (level %.1f%%)\n", pumpWord(), level.ok() ? level.percent() : -1.0);
  sendState();
}

/// Runs after every level reading and every command: the local automation.
void control() {
  last = decidePump(rules, inputs(), pumpOn, manualOn);
  if (last.dryRunProtect || last.overflowProtect) manualOn = false;  // a safety stop ends a manual run
  setPump(last.on);
}

// The ultrasonic sensor: the echo time is the distance to the water (and back).
void readLevel() {
  if (millis() - lastReadMs < LEVEL_READ_MS) return;
  lastReadMs = millis();
  digitalWrite(TANK_TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TANK_TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TANK_TRIG_PIN, LOW);
  const unsigned long us = pulseIn(TANK_ECHO_PIN, HIGH, 30000);  // 30 ms: about 5 m
  const double cm = us * 0.0343 / 2.0;
  level.add(us > 0 && cm < 450, cm);
  control();
}

void onCommand(const Command& cmd) {
  if (strcmp(cmd.type, "mode") == 0) {
    rules.manual = strcmp(cmd.action, "manual") == 0;
    manualOn = rules.manual && pumpOn;  // switching to manual keeps the pump as it is
    flash.putBool("manual", rules.manual);
    node::sendAck(cmd.cmdId, "ok", nullptr, cmd.action);
    control();  // back in auto: apply the thresholds straight away
    sendState();
  } else if (strcmp(cmd.type, "thresholds") == 0) {
    if (!thresholdsOk(cmd.lowPct, cmd.highPct)) {
      node::sendAck(cmd.cmdId, "rejected", "INVALID", nullptr);
      return;
    }
    rules.lowPct = cmd.lowPct;
    rules.highPct = cmd.highPct;
    flash.putDouble("lowPct", rules.lowPct);  // kept after a reboot (FR-16 AC3)
    flash.putDouble("highPct", rules.highPct);
    node::sendAck(cmd.cmdId, "ok", nullptr, nullptr);
    control();
    sendState();
  } else {  // "pump" (valve commands were refused as UNSUPPORTED)
    const bool on = strcmp(cmd.action, "on") == 0;
    const char* reason = checkPumpCommand(rules, inputs(), on);
    if (reason != nullptr) {
      node::sendAck(cmd.cmdId, "rejected", reason, pumpWord());
      return;
    }
    manualOn = on;
    control();
    node::sendAck(cmd.cmdId, "ok", nullptr, pumpWord());
  }
}

void sendTelemetry() {
  const int64_t ts = node::nowMs();
  if (ts == 0) return;  // no valid time yet: wait for NTP
  const PumpInputs in = inputs();
  TankTelemetry t{ts,          node::bootId(),     node::seq(),   level.ok() ? level.percent() : 0.0,
                  TANK_CAPACITY_L, pumpOn,          in.sumpOk,     level.ok(),
                  last.dryRunProtect, last.overflowProtect, rules.manual, rules.lowPct,
                  rules.highPct, node::rssi(),     node::isFirstTelemetry()};
  char buf[MAX_PAYLOAD];
  if (tankTelemetryJson(t, buf, sizeof buf)) node::publishTelemetry(buf);
}

void serialCommands() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line == "info") {
    Serial.printf("device %s, fw %s, level %s%.1f%%, pump %s, mode %s, thresholds %.0f/%.0f%%, sump %s\n",
                  DEVICE_ID, FW_VERSION, level.ok() ? "" : "(sensor failed) ", level.ok() ? level.percent() : 0.0,
                  pumpWord(), rules.manual ? "manual" : "auto", rules.lowPct, rules.highPct,
                  inputs().sumpOk ? "water" : "EMPTY");
  } else if (line.length()) {
    Serial.println("Commands: info");
  }
}

}  // namespace

void setup() {
  node::begin(DeviceKind::Tank, "tank node");

  // Restore the owner's settings (FR-16 AC3). The pump always starts off and
  // the automation decides after the first 5 level readings.
  rules.manual = flash.getBool("manual", false);
  rules.lowPct = flash.getDouble("lowPct", rules.lowPct);
  rules.highPct = flash.getDouble("highPct", rules.highPct);
  if (!thresholdsOk(rules.lowPct, rules.highPct)) rules = PumpRules{};
  Serial.printf("Restored: mode %s, thresholds %.0f/%.0f%%, interval %d s\n", rules.manual ? "manual" : "auto",
                rules.lowPct, rules.highPct, node::intervalS);

  pinMode(PUMP_RELAY_PIN, OUTPUT);
  digitalWrite(PUMP_RELAY_PIN, LOW);
  pinMode(TANK_TRIG_PIN, OUTPUT);
  pinMode(TANK_ECHO_PIN, INPUT);
  pinMode(SUMP_PIN, INPUT_PULLUP);

  node::connect(onCommand, sendState);
}

void loop() {
  node::loop();  // the automation below runs even when this cannot connect
  readLevel();
  serialCommands();
  if (node::telemetryDue()) sendTelemetry();
  delay(10);
}
