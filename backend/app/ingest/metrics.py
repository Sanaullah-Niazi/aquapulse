"""Prometheus counters for the ingestor (FR-31). Served on port 9100 inside Docker only."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

RECEIVED = Counter("ingest_messages_total", "Device messages received", ["channel"])
STORED = Counter("ingest_stored_total", "Readings stored")
DUPLICATES = Counter("ingest_duplicates_total", "Readings already stored (same device, boot, seq)")
REJECTED = Counter("ingest_rejected_total", "Device messages rejected", ["reason"])
COUNTER_RESETS = Counter(
    "ingest_counter_resets_total", "Meter counters that went down or jumped", ["kind"]
)
BATCH_SECONDS = Histogram("ingest_batch_seconds", "Time to check and store one batch")
COMMANDS = Counter("device_commands_total", "Finished device commands", ["outcome"])
COMMAND_SECONDS = Histogram(
    "device_command_seconds",
    "From sending a command to its ack",
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 15),
)
CONNECTED = Gauge("ingest_mqtt_connected", "1 while connected to the MQTT broker")
