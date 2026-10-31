// AquaPulse home node (FR-15, FR-17): meters a home's water, measures pH and
// temperature, and opens or closes its valve when the server asks.
//
// The rules (message formats, command checks, counter saving, back-off) are
// in lib/aquapulse_core, which is tested on a computer. This file connects
// them to the real hardware: Wi-Fi, the TLS MQTT connection, the sensors,
// the valve and the flash memory.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <DallasTemperature.h>
#include <MQTT.h>
#include <OneWire.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_random.h>
#include <time.h>

#include "aquapulse_core.h"
#include "config.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#pragma message "include/secrets.h not found: building with the example values (fine for CI, not for a real device)"
#include "secrets.example.h"
#endif

using namespace aquapulse;

namespace {

WiFiClientSecure net;
MQTTClient mqtt(MAX_PAYLOAD + 256);
Preferences flash;
OneWire oneWire(ONEWIRE_PIN);
DallasTemperature thermometer(&oneWire);

char bootId[37];
char topicTelemetry[64], topicStatus[64], topicState[64], topicCmd[64], topicAck[64];
uint32_t seq = 0;
bool firstTelemetry = true;
int intervalS = DEFAULT_INTERVAL_S;

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

SeenCommands seen;
Backoff backoff;
uint32_t nextConnectMs = 0;
uint32_t lastTelemetryMs = 0;

const char* valveName(Valve v) {
  switch (v) {
    case Valve::Open: return "open";
    case Valve::Closed: return "closed";
    case Valve::Moving: return "moving";
    default: return "fault";
  }
}

int64_t nowMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) return 0;  // NTP has not set the clock yet
  return static_cast<int64_t>(tv.tv_sec) * 1000 + tv.tv_usec / 1000;
}

double random01() { return esp_random() / 4294967296.0; }

void publish(const char* topic, const char* payload, bool retained, int qos) {
  if (mqtt.connected()) mqtt.publish(topic, payload, static_cast<int>(strlen(payload)), retained, qos);
}

void sendState() {
  char buf[256];
  if (homeStateJson(nowMs(), valveName(valve), intervalS, buf, sizeof buf)) publish(topicState, buf, true, 1);
}

void sendAck(const char* cmdId, const char* result, const char* reason, const char* state) {
  char buf[256];
  if (ackJson(cmdId, result, reason, state, nowMs(), buf, sizeof buf)) publish(topicAck, buf, false, 1);
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

// ----- commands (contract cmd.v1) -----

void onCommand(String& /*topic*/, String& payload) {
  JsonDocument doc;
  Command cmd;
  const bool parsed = payload.length() <= MAX_PAYLOAD && !deserializeJson(doc, payload) && doc.is<JsonObject>();
  cmd.parsed = parsed;
  if (parsed) {
    static const char* const known[] = {"v", "cmdId", "type", "action", "lowPct", "highPct", "seconds",
                                        "issuedAt", "expiresAt", "by"};
    for (JsonPair kv : doc.as<JsonObject>()) {
      bool ok = false;
      for (const char* k : known) ok = ok || strcmp(kv.key().c_str(), k) == 0;
      cmd.unknownField = cmd.unknownField || !ok;
    }
    cmd.v = doc["v"].is<int>() ? doc["v"].as<int>() : 0;
    cmd.cmdId = doc["cmdId"] | "";
    cmd.type = doc["type"] | "";
    cmd.action = doc["action"] | "";
    cmd.issuedAt = doc["issuedAt"] | "";
    cmd.expiresAt = doc["expiresAt"] | "";
    cmd.by = doc["by"] | "";
    if ((cmd.hasLowPct = doc["lowPct"].is<double>())) cmd.lowPct = doc["lowPct"];
    if ((cmd.hasHighPct = doc["highPct"].is<double>())) cmd.highPct = doc["highPct"];
    if ((cmd.hasSeconds = doc["seconds"].is<long>())) cmd.seconds = doc["seconds"];
  }
  const Verdict verdict = checkCommand(cmd, DeviceKind::Home, nowMs(), seen);
  const char* id = parsed ? cmd.cmdId : nullptr;
  if (!verdict.accept) {
    sendAck(id, "rejected", verdict.reason, valveName(valve));
    return;
  }
  seen.add(cmd.cmdId);

  if (strcmp(cmd.type, "interval") == 0) {
    intervalS = static_cast<int>(cmd.seconds);
    flash.putInt("intervalS", intervalS);
    sendAck(cmd.cmdId, "ok", nullptr, nullptr);
    sendState();
    return;
  }
  // type == "valve"
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

// ----- connection (FR-12, FR-17) -----

void connectMqtt() {
  if (WiFi.status() != WL_CONNECTED || millis() < nextConnectMs) return;
  char lastWill[64];
  statusJson(false, lastWill, sizeof lastWill);
  mqtt.setWill(topicStatus, lastWill, true, 1);  // the broker says "offline" for us if we vanish
  Serial.printf("MQTT: connecting to %s:%d as %s ...\n", MQTT_HOST, MQTT_PORT, DEVICE_ID);
  if (!mqtt.connect(DEVICE_ID, DEVICE_ID, DEVICE_PASSWORD)) {
    const uint32_t wait = backoff.next(random01());
    Serial.printf("MQTT: failed (error %d, return code %d), next try in %lu ms\n", mqtt.lastError(),
                  mqtt.returnCode(), static_cast<unsigned long>(wait));
    nextConnectMs = millis() + wait;
    return;
  }
  backoff.reset();
  Serial.println("MQTT: connected");
  mqtt.subscribe(topicCmd, 1);
  char buf[64];
  statusJson(true, buf, sizeof buf);
  publish(topicStatus, buf, true, 1);
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
  const int64_t ts = nowMs();
  if (ts == 0) return;  // no valid time yet: wait for NTP
  HomeTelemetry t{ts, bootId, seq, totalL, flowLpm, readPh(), tempC, valveName(valve), WiFi.RSSI(), firstTelemetry};
  char buf[MAX_PAYLOAD];
  if (homeTelemetryJson(t, buf, sizeof buf) && mqtt.connected()) {
    publish(topicTelemetry, buf, false, 0);
    seq++;
    firstTelemetry = false;
  }
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
    Serial.printf("device %s, fw %s, total %.2f L, valve %s, pH %.2f (%.0f mV), %.1f C, interval %d s\n", DEVICE_ID,
                  FW_VERSION, totalL, valveName(valve), phCal.ph(mv), mv, tempC, intervalS);
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
  Serial.begin(115200);
  Serial.printf("\nAquaPulse home node %s, fw %s\n", DEVICE_ID, FW_VERSION);

  uint8_t raw[16];
  esp_fill_random(raw, sizeof raw);
  uuidFromBytes(raw, bootId, sizeof bootId);  // a new bootId tells the server we restarted
  topic(DEVICE_ID, "telemetry", topicTelemetry, sizeof topicTelemetry);
  topic(DEVICE_ID, "status", topicStatus, sizeof topicStatus);
  topic(DEVICE_ID, "state", topicState, sizeof topicState);
  topic(DEVICE_ID, "cmd", topicCmd, sizeof topicCmd);
  topic(DEVICE_ID, "ack", topicAck, sizeof topicAck);

  // Restore what was saved before the reboot (FR-17).
  flash.begin("aquapulse", false);
  totalL = flash.getDouble("totalL", 0.0);
  saver.restored(totalL, millis());
  phCal.mv4 = flash.getDouble("ph_mv4", phCal.mv4);
  phCal.mv7 = flash.getDouble("ph_mv7", phCal.mv7);
  intervalS = flash.getInt("intervalS", DEFAULT_INTERVAL_S);
  const bool wasOpen = flash.getBool("valveOpen", true);
  Serial.printf("Restored: %.2f L, valve %s, interval %d s\n", totalL, wasOpen ? "open" : "closed", intervalS);

  pinMode(STATUS_LED_PIN, OUTPUT);
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

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC, for timestamps and command expiry
  net.setCACert(CA_CERT);  // only our own CA is trusted (FR-12)
  mqtt.begin(MQTT_HOST, MQTT_PORT, net);
  mqtt.setKeepAlive(30);
  mqtt.setCleanSession(true);
  mqtt.onMessage(onCommand);
  windowStartMs = millis();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 10000) {  // the ESP32 also reconnects by itself
      lastTry = millis();
      WiFi.reconnect();
    }
  } else if (!mqtt.connected()) {
    connectMqtt();
  }
  mqtt.loop();
  digitalWrite(STATUS_LED_PIN, mqtt.connected() ? HIGH : LOW);

  countWater();
  updateValve();
  readTemperature();
  serialCommands();
#ifdef AQUAPULSE_WOKWI
  wokwiFlow();
#endif
  if (millis() - lastTelemetryMs >= static_cast<uint32_t>(intervalS) * 1000) {
    lastTelemetryMs = millis();
    sendTelemetry();
  }
  delay(10);
}
