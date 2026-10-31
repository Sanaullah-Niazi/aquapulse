// Copy this file to secrets.h (git-ignored) and fill it in. Never commit secrets.h.
//   cp firmware/include/secrets.example.h firmware/include/secrets.h
// In Wokwi, keep the Wi-Fi values below and use host.wokwi.internal as the broker.
#pragma once

// ----- Wi-Fi -----
#define WIFI_SSID "Wokwi-GUEST"  // your Wi-Fi name (Wokwi's built-in network in the simulator)
#define WIFI_PASSWORD ""

// ----- MQTT broker (TLS only, port 8883) -----
// On your Mac with Wokwi: host.wokwi.internal. On real hardware: your Mac's IP
// address is not in the certificate, so use a name that is (see docs/milestones/M11).
#define MQTT_HOST "host.wokwi.internal"
#define MQTT_PORT 8883

// ----- This device (from the web app: Devices -> Register, or `make device`) -----
#define DEVICE_ID "home-b01-01"
#define DEVICE_PASSWORD "paste-the-one-time-password-here"

// ----- The AquaPulse CA certificate (infra/certs/public/ca.crt) -----
// `make firmware-secrets` fills this in for you.
#define CA_CERT                          \
  "-----BEGIN CERTIFICATE-----\n"        \
  "paste infra/certs/public/ca.crt here\n" \
  "-----END CERTIFICATE-----\n"
