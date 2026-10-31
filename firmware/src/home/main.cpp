// AquaPulse home node (FR-15, FR-17): meters a home's water, measures pH and
// temperature, and opens or closes its valve when the server asks.
//
// The rules (message formats, command checks, counter saving, back-off) are
// in lib/aquapulse_core, which is tested on a computer. This file connects
// them to the real hardware: Wi-Fi, the TLS MQTT connection, the sensors,
// the valve and the flash memory.
#include <Arduino.h>
#include <DallasTemperature.h>
#include <OneWire.h>

#include "aquapulse_core.h"
#include "config.h"
#include "../common/node.h"

using namespace aquapulse;
using node::flash;

namespace {

OneWire oneWire(ONEWIRE_PIN);
DallasTemperature thermometer(&oneWire);

// ----- the litre counter (FR-01, FR-17) -----
volatile uint32_t pulses = 0;  // counted by the interrupt
FlowMeter meter{FLOW_PULSES_PER_LITRE};
CounterSaver saver;
double totalL = 0;
double flowLpm = 0;
uint32_t windowPulses = 0;
uint32_t windowStartMs = 0;

void IRAM_ATTR onPulse() { pulses = pulses + 1; }

// ----- sensors -----
PhCalibration phCal;
double tempC = 20.0;
uint32_t tempRequestedMs = 0;

// ----- the valve -----
enum class Valve { Open, Closed, Moving, Fault };
Valve valve = Valve::Open;
bool valveTarget = true;  // true = open
uint32_t valveStartedMs = 0;
char pendingCmdId[37] = "";

const char* valveName(Valve v) {
  switch (v) {
    case Valve::Open: return "open";
    case Valve::Closed: return "closed";
    case Valve::Moving: return "moving";
    default: return "fault";
  }
}

void sendState() {
  char buf[256];
  if (homeStateJson(node::nowMs(), valveName(valve), node::intervalS, buf, sizeof buf)) node::publishState(buf);
}

void sendAck(const char* cmdId, const char* result, const char* reason, const char* state) {
  node::sendAck(cmdId, result, reason, state);
}

// ----- valve -----

void stopMotor() {
  digitalWrite(VALVE_OPEN_PIN, LOW);
  digitalWrite(VALVE_CLOSE_PIN, LOW);
}

void driveValve(bool open) {
  stopMotor();
  delay(50);  // never both relays at once
  digitalWrite(open ? VALVE_OPEN_PIN : VALVE_CLOSE_PIN, HIGH);
  valveTarget = open;
  valve = Valve::Moving;
  valveStartedMs = millis();
  flash.putBool("valveOpen", open);  // after a power cut, go back to this (FR-17)
}

bool valveArrived() {
#if VALVE_HAS_FEEDBACK
  return digitalRead(valveTarget ? VALVE_OPENED_PIN : VALVE_CLOSED_PIN) == LOW;
#else
  return millis() - valveStartedMs >= VALVE_TRAVEL_MS;
#endif
}

void updateValve() {
  if (valve != Valve::Moving) return;
  if (valveArrived()) {
    stopMotor();
    valve = valveTarget ? Valve::Open : Valve::Closed;
    if (pendingCmdId[0]) sendAck(pendingCmdId, "ok", nullptr, valveName(valve));
  } else if (millis() - valveStartedMs >= VALVE_TIMEOUT_MS) {
    stopMotor();
    valve = Valve::Fault;
    if (pendingCmdId[0]) sendAck(pendingCmdId, "failed", "VALVE_STUCK", "fault");
  } else {
    return;
  }
  pendingCmdId[0] = '\0';
  sendState();
}

// ----- commands (contract cmd.v1; already checked by node) -----

void onCommand(const Command& cmd) {
  // type == "valve" (anything else for a home node was refused as UNSUPPORTED)
  if (valve == Valve::Moving) {
    sendAck(cmd.cmdId, "rejected", "BUSY", "moving");
    return;
  }
  const bool open = strcmp(cmd.action, "open") == 0;
  if ((open && valve == Valve::Open) || (!open && valve == Valve::Closed)) {
    sendAck(cmd.cmdId, "ok", nullptr, valveName(valve));  // already there
    return;
  }
  strncpy(pendingCmdId, cmd.cmdId, sizeof pendingCmdId - 1);
  driveValve(open);  // the ack comes when it gets there (or VALVE_STUCK after 8 s)
  sendState();
}

// ----- sensors -----

double readPh() {
  uint32_t sum = 0;
  for (int i = 0; i < PH_SAMPLES; i++) sum += analogReadMilliVolts(PH_PIN);
  return phCal.ph(sum / static_cast<double>(PH_SAMPLES) * PH_DIVIDER);
}

void readTemperature() {
  if (millis() - tempRequestedMs < 1000) return;  // a 12-bit reading takes 750 ms
  const float t = thermometer.getTempCByIndex(0);
  if (t != DEVICE_DISCONNECTED_C) tempC = t;
  thermometer.requestTemperatures();
  tempRequestedMs = millis();
}

void countWater() {
  noInterrupts();
  const uint32_t n = pulses;
  pulses = 0;
  interrupts();
  windowPulses += n;
  totalL += meter.litres(n);
  if (saver.shouldSave(totalL, millis())) {
    flash.putDouble("totalL", totalL);
    saver.saved(totalL, millis());
  }
}

void sendTelemetry() {
  const uint32_t now = millis();
  flowLpm = meter.lpm(windowPulses, now - windowStartMs);
  windowPulses = 0;
  windowStartMs = now;
  if (valve == Valve::Closed) flowLpm = 0;  // contract: exactly 0 with the valve closed
  const int64_t ts = node::nowMs();
  if (ts == 0) return;  // no valid time yet: wait for NTP
  HomeTelemetry t{ts,     node::bootId(),    node::seq(), totalL, flowLpm, readPh(), tempC, valveName(valve),
                  node::rssi(), node::isFirstTelemetry()};
  char buf[MAX_PAYLOAD];
  if (homeTelemetryJson(t, buf, sizeof buf)) node::publishTelemetry(buf);
}

// ----- calibration from the serial monitor (FR-15: two-point pH) -----

void serialCommands() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  uint32_t sum = 0;
  for (int i = 0; i < 64; i++) sum += analogReadMilliVolts(PH_PIN);
  const double mv = sum / 64.0 * PH_DIVIDER;
  if (line == "cal4") {
    phCal.mv4 = mv;
    flash.putDouble("ph_mv4", mv);
    Serial.printf("pH 4.00 buffer saved: %.0f mV\n", mv);
  } else if (line == "cal7") {
    phCal.mv7 = mv;
    flash.putDouble("ph_mv7", mv);
    Serial.printf("pH 7.00 buffer saved: %.0f mV\n", mv);
  } else if (line == "info") {
    Serial.printf("device %s, fw %s, total %.2f L, valve %s, pH %.2f (%.0f mV), %.1f C, interval %d s\n", node::deviceId(),
                  FW_VERSION, totalL, valveName(valve), phCal.ph(mv), mv, tempC, node::intervalS);
  } else if (line.length()) {
    Serial.println("Commands: cal4 (probe in pH 4.00 buffer), cal7 (probe in pH 7.00 buffer), info");
  }
}

#ifdef AQUAPULSE_WOKWI
// Wokwi has no flow meter part: the knob sets a flow, and the ESP32 makes
// the meter's pulses on WOKWI_PULSE_PIN (wired to FLOW_PIN).
void wokwiFlow() {
  static uint32_t lastMs = 0;
  if (millis() - lastMs < 500) return;
  lastMs = millis();
  const double lpm = analogRead(WOKWI_FLOW_POT_PIN) / 4095.0 * WOKWI_MAX_LPM;
  const bool flowing = valve != Valve::Closed && lpm > 0.2;
  const uint32_t hz = flowing ? static_cast<uint32_t>(lpm * FLOW_PULSES_PER_LITRE / 60.0) : 0;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWriteTone(WOKWI_PULSE_PIN, hz);
#else
  ledcWriteTone(WOKWI_PULSE_CHANNEL, hz);
#endif
}
#endif

}  // namespace

void setup() {
  node::begin(DeviceKind::Home, "home node");

  // Restore what was saved before the reboot (FR-17).
  totalL = flash.getDouble("totalL", 0.0);
  saver.restored(totalL, millis());
  phCal.mv4 = flash.getDouble("ph_mv4", phCal.mv4);
  phCal.mv7 = flash.getDouble("ph_mv7", phCal.mv7);
  const bool wasOpen = flash.getBool("valveOpen", true);
  Serial.printf("Restored: %.2f L, valve %s, interval %d s\n", totalL, wasOpen ? "open" : "closed", node::intervalS);

  pinMode(VALVE_OPEN_PIN, OUTPUT);
  pinMode(VALVE_CLOSE_PIN, OUTPUT);
  pinMode(VALVE_OPENED_PIN, INPUT_PULLUP);
  pinMode(VALVE_CLOSED_PIN, INPUT_PULLUP);
  stopMotor();
  driveValve(wasOpen);  // back to the last commanded state after a power cut

  pinMode(FLOW_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), onPulse, FALLING);
#ifdef AQUAPULSE_WOKWI
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(WOKWI_PULSE_PIN, 1000, 8);
#else
  ledcSetup(WOKWI_PULSE_CHANNEL, 1000, 8);
  ledcAttachPin(WOKWI_PULSE_PIN, WOKWI_PULSE_CHANNEL);
#endif
#endif
  analogSetPinAttenuation(PH_PIN, ADC_11db);
  thermometer.begin();
  thermometer.setWaitForConversion(false);
  thermometer.requestTemperatures();
  tempRequestedMs = millis();
  windowStartMs = millis();

  node::connect(onCommand, sendState);
}

void loop() {
  node::loop();
  countWater();
  updateValve();
  readTemperature();
  serialCommands();
#ifdef AQUAPULSE_WOKWI
  wokwiFlow();
#endif
  if (node::telemetryDue()) sendTelemetry();
  delay(10);
}
