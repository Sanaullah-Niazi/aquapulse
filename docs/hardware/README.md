# Hardware (M11)

You do not need any hardware for AquaPulse: the simulator and Wokwi do the same job. This folder is for when you want a real device.

| Node | One per | What it does | Guide |
|---|---|---|---|
| Home node | home | Counts the water, measures pH and temperature, opens and closes the home's valve | [home-node.md](home-node.md) |
| Tank node | building | Measures the overhead tank's level, runs the pump by itself, protects it from running dry or overflowing | [tank-node.md](tank-node.md) |

Prices are rough online prices in 2026 (USD), to give an idea. They change a lot by country and shop.

## Safety first

- **Mains power (230 V or 120 V) can kill.** The pump and the 12 V valve power supply must be wired by an electrician, in a closed box. The ESP32 side is low voltage (5 V and 3.3 V) only.
- The ESP32's pins take **3.3 V at most**. The flow meter, the pH board and the ultrasonic sensor run on 5 V, so their signal goes through a voltage divider (10 kΩ and 20 kΩ) before the ESP32.
- Use a relay **module** with an optocoupler, not a bare relay. The relay switches a **contactor** for the pump, never the pump directly.
- Water and electronics: keep the electronics box above the pipes, with a drip loop on every cable.

## Flashing a board

```bash
make firmware-secrets DEVICE=home-b01-04 PASSWORD='...' HOST=<a name in the server certificate> WIFI="your Wi-Fi" WIFI_PASSWORD="..."
make firmware-upload             # home node
make firmware-upload NODE=tank   # tank node (use DEVICE=tank-b01-01 above)
```

The password comes from the web app (**Devices** → **Register** or **New password**). It is shown only once.
