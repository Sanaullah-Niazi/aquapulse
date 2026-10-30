"""The ingestor (M4): the server's link to the devices.

* `counter.py`  litres from the cumulative counter (FR-23), pure logic
* `handler.py`  check and store every device message (FR-22)
* `commands.py` send queued commands, time them out (FR-13, FR-24)
* `service.py`  the MQTT connection and the main loop: `python -m app.ingest`
"""
