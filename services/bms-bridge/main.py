import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bridge import Bridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
SUMMARY_INTERVAL_S = float(os.getenv("SUMMARY_INTERVAL_S", "900"))   # 15 min
TIME_SCALE = float(os.getenv("TIME_SCALE", "1"))   # >1 ONLY when fed by the accelerated simulator
HTTP_PORT = int(os.getenv("HTTP_PORT", "8001"))

bridge = Bridge(MQTT_HOST, MQTT_PORT, SUMMARY_INTERVAL_S, TIME_SCALE)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "service": "bms-bridge",
                           "mqtt_connected": bridge.connected,
                           "devices": sorted(bridge.trackers)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep logs quiet
        pass


if __name__ == "__main__":
    bridge.start()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    bridge.stop()
