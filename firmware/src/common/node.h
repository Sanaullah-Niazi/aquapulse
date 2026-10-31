// What every AquaPulse node does the same way (FR-12, FR-17, contract cmd.v1):
// Wi-Fi, the clock (NTP), the TLS MQTT connection with its Last Will and
// back-off, the topics, reading and checking commands, acks, the "interval"
// command, and the flash memory. The home and tank nodes add their sensors.
#pragma once

#include <Preferences.h>

#include "aquapulse_core.h"

namespace node {

/// Called for an accepted command (already checked: valid, not expired, not
/// repeated, for this kind of device). "interval" is handled here and never
/// reaches it. The strings in cmd are valid only during the call.
using CommandHandler = void (*)(const aquapulse::Command& cmd);
/// Publishes the node's retained state (after connecting and after changes).
using StateSender = void (*)();

extern Preferences flash;  // namespace "aquapulse"
extern int intervalS;      // telemetry interval, changed by the "interval" command

/// Call once at the start of setup(): serial, bootId, topics, flash.
void begin(aquapulse::DeviceKind kind, const char* name);
/// Call at the end of setup(): Wi-Fi, NTP and MQTT start connecting.
void connect(CommandHandler onCommand, StateSender sendState);
/// Call every loop(): keeps Wi-Fi and MQTT up, handles incoming messages.
void loop();

bool connected();
/// UTC milliseconds since 1970, or 0 while NTP has not set the clock yet.
int64_t nowMs();
/// True once per interval: time to send telemetry.
bool telemetryDue();

const char* bootId();
uint32_t seq();
bool isFirstTelemetry();
int rssi();

/// Sends telemetry (QoS 0); counts seq and clears the boot flag if it went out.
void publishTelemetry(const char* json);
/// Sends the retained state (QoS 1).
void publishState(const char* json);
/// result: ok, rejected, failed. reason/state may be nullptr.
void sendAck(const char* cmdId, const char* result, const char* reason, const char* state);

}  // namespace node
