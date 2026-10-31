#include "node.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <MQTT.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_random.h>
#include <sys/time.h>

#include "config.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#pragma message "include/secrets.h not found: building with the example values (fine for CI, not for a real device)"
#include "secrets.example.h"
#endif

using namespace aquapulse;

namespace node {

Preferences flash;
int intervalS = DEFAULT_INTERVAL_S;

namespace {

WiFiClientSecure net;
MQTTClient mqtt(MAX_PAYLOAD + 256);
DeviceKind deviceKind = DeviceKind::Home;
char boot[37];
char topicTelemetry[64], topicStatus[64], topicState[64], topicCmd[64], topicAck[64];
uint32_t seqNo = 0;
bool firstTelemetry = true;
SeenCommands seen;
Backoff backoff;
uint32_t nextConnectMs = 0;
uint32_t lastTelemetryMs = 0;
CommandHandler handler = nullptr;
StateSender stateSender = nullptr;

double random01() { return esp_random() / 4294967296.0; }

void publish(const char* topic, const char* payload, bool retained, int qos) {
  if (mqtt.connected()) mqtt.publish(topic, payload, static_cast<int>(strlen(payload)), retained, qos);
}

void onMessage(String& /*topic*/, String& payload) {
  JsonDocument doc;
  Command cmd;
  const bool parsed = payload.length() <= MAX_PAYLOAD && !deserializeJson(doc, payload) && doc.is<JsonObject>();
  cmd.parsed = parsed;
  if (parsed) {
    static const char* const known[] = {"v",       "cmdId",    "type",      "action", "lowPct",
                                        "highPct", "seconds", "issuedAt", "expiresAt", "by"};
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
  const Verdict verdict = checkCommand(cmd, deviceKind, nowMs(), seen);
  if (!verdict.accept) {
    sendAck(parsed && isUuid(cmd.cmdId) ? cmd.cmdId : nullptr, "rejected", verdict.reason, nullptr);
    return;
  }
  seen.add(cmd.cmdId);
  if (strcmp(cmd.type, "interval") == 0) {
    intervalS = static_cast<int>(cmd.seconds);
    flash.putInt("intervalS", intervalS);
    sendAck(cmd.cmdId, "ok", nullptr, nullptr);
    if (stateSender) stateSender();
    return;
  }
  if (handler) handler(cmd);
}

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
  if (stateSender) stateSender();
}

}  // namespace

void begin(DeviceKind kind, const char* name) {
  Serial.begin(115200);
  Serial.printf("\nAquaPulse %s %s, fw %s\n", name, DEVICE_ID, FW_VERSION);
  deviceKind = kind;
  uint8_t raw[16];
  esp_fill_random(raw, sizeof raw);
  uuidFromBytes(raw, boot, sizeof boot);  // a new bootId tells the server we restarted
  topic(DEVICE_ID, "telemetry", topicTelemetry, sizeof topicTelemetry);
  topic(DEVICE_ID, "status", topicStatus, sizeof topicStatus);
  topic(DEVICE_ID, "state", topicState, sizeof topicState);
  topic(DEVICE_ID, "cmd", topicCmd, sizeof topicCmd);
  topic(DEVICE_ID, "ack", topicAck, sizeof topicAck);
  flash.begin("aquapulse", false);
  intervalS = flash.getInt("intervalS", DEFAULT_INTERVAL_S);
  pinMode(STATUS_LED_PIN, OUTPUT);
}

void connect(CommandHandler onCommand, StateSender sendState) {
  handler = onCommand;
  stateSender = sendState;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");  // UTC, for timestamps and command expiry
  net.setCACert(CA_CERT);                                // only our own CA is trusted (FR-12)
  mqtt.begin(MQTT_HOST, MQTT_PORT, net);
  mqtt.setKeepAlive(30);
  mqtt.setCleanSession(true);
  mqtt.onMessage(onMessage);
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
}

bool connected() { return mqtt.connected(); }

int64_t nowMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  if (tv.tv_sec < 1700000000) return 0;  // NTP has not set the clock yet
  return static_cast<int64_t>(tv.tv_sec) * 1000 + tv.tv_usec / 1000;
}

bool telemetryDue() {
  if (millis() - lastTelemetryMs < static_cast<uint32_t>(intervalS) * 1000) return false;
  lastTelemetryMs = millis();
  return true;
}

const char* bootId() { return boot; }
uint32_t seq() { return seqNo; }
bool isFirstTelemetry() { return firstTelemetry; }
int rssi() { return WiFi.RSSI(); }

void publishTelemetry(const char* json) {
  if (!mqtt.connected()) return;
  publish(topicTelemetry, json, false, 0);
  seqNo++;
  firstTelemetry = false;
}

void publishState(const char* json) { publish(topicState, json, true, 1); }

void sendAck(const char* cmdId, const char* result, const char* reason, const char* state) {
  char buf[256];
  if (ackJson(cmdId, result, reason, state, nowMs(), buf, sizeof buf)) publish(topicAck, buf, false, 1);
}

}  // namespace node
