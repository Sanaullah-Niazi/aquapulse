// Pins and settings for the home node (FR-15) and the tank node (FR-16). Change these to match
// your wiring; docs/hardware/home-node.md has the wiring diagram.
#pragma once

// ----- pins (ESP32 DevKit v1) -----
#define FLOW_PIN 27          // flow meter pulse output (YF-S201, via a 10k/20k divider: it is a 5 V sensor)
#define PH_PIN 34            // pH board analog output (PH-4502C, via a divider to stay under 3.3 V)
#define ONEWIRE_PIN 4        // DS18B20 data (4.7k pull-up to 3.3 V)
#define VALVE_OPEN_PIN 25    // relay 1: drives the valve motor to OPEN
#define VALVE_CLOSE_PIN 26   // relay 2: drives the valve motor to CLOSE
#define VALVE_OPENED_PIN 32  // valve feedback: LOW when fully open   (INPUT_PULLUP)
#define VALVE_CLOSED_PIN 33  // valve feedback: LOW when fully closed (INPUT_PULLUP)
#define STATUS_LED_PIN 2     // the DevKit's blue LED: on = connected to the broker

// ----- sensors -----
#define FLOW_PULSES_PER_LITRE 450.0  // YF-S201: F (Hz) = 7.5 x Q (L/min)
#define PH_DIVIDER 1.5               // the probe board gives up to 5 V: 10k/20k divider -> x1.5
#define PH_SAMPLES 16                // average this many readings

// ----- valve -----
#define VALVE_TIMEOUT_MS 8000  // contract: VALVE_STUCK if it is not there after 8 s
// Valves without feedback wires: 0, and the position is assumed after VALVE_TRAVEL_MS.
#ifndef VALVE_HAS_FEEDBACK
#define VALVE_HAS_FEEDBACK 1
#endif
#define VALVE_TRAVEL_MS 3000

// ----- telemetry -----
#define DEFAULT_INTERVAL_S 10  // the server can change it with an "interval" command

// ----- Wokwi only (built with -DAQUAPULSE_WOKWI): a knob sets the water flow -----
// The ESP32 makes the flow meter's pulses itself on WOKWI_PULSE_PIN, which is
// wired to FLOW_PIN in diagram.json, so the counting code is the real one.
#define WOKWI_PULSE_PIN 18
#define WOKWI_PULSE_CHANNEL 0  // LEDC channel (Arduino core 2.x)
#define WOKWI_FLOW_POT_PIN 35  // 0..30 L/min
#define WOKWI_MAX_LPM 30.0

// ===================== tank node (FR-16) =====================
// docs/hardware/tank-node.md has the wiring diagram.
#define TANK_TRIG_PIN 5    // ultrasonic sensor trigger (JSN-SR04T, waterproof)
#define TANK_ECHO_PIN 19   // ultrasonic echo (via a 10k/20k divider: the sensor runs on 5 V)
#define SUMP_PIN 21        // float switch in the sump: LOW = water there (INPUT_PULLUP)
#define PUMP_RELAY_PIN 23  // relay for the pump contactor: HIGH = pump on
#define TANK_EMPTY_CM 150.0   // sensor to water when the tank is empty
#define TANK_FULL_CM 25.0     // sensor to water when the tank is full (keep >= 25 cm: the sensor's dead zone)
#define TANK_CAPACITY_L 5000.0
#define LEVEL_READ_MS 1000    // one level reading per second
