import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ingestor import Ingestor
from outbox import Outbox
from sender import Sender

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("mpm-ingestor")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MPM_BASE_URL = os.getenv("MPM_BASE_URL", "http://localhost:8000")
MPM_API_KEY = os.getenv("MPM_API_KEY", "")
OUTBOX_PATH = os.getenv("OUTBOX_PATH", "outbox.db")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8002"))
POLL_S = float(os.getenv("POLL_S", "1"))

outbox = Outbox(OUTBOX_PATH)
sender = Sender(outbox, MPM_BASE_URL, MPM_API_KEY)
ingestor = Ingestor(MQTT_HOST, MQTT_PORT, outbox)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "status": "ok", "service": "mpm-ingestor",
            "mqtt_connected": ingestor.connected,
            "mpm_configured": bool(MPM_API_KEY),
            "queued": outbox.depth(),
            "last_mpm_status": sender.last_status,
            "last_mpm_success_ago_s": None if sender.last_success is None else round(time.time() - sender.last_success),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def deliver_forever(stop: threading.Event) -> None:
    while not stop.is_set():
        if sender.run_once():
            sender.last_success = time.time()
            continue                      # more may be due: don't sleep
        stop.wait(POLL_S)


if __name__ == "__main__":
    if not MPM_API_KEY:
        log.warning("MPM_API_KEY is not set: messages are queued but NOT delivered until it is.")
    ingestor.start()
    stop = threading.Event()
    threading.Thread(target=deliver_forever, args=(stop,), daemon=True, name="deliver").start()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    ingestor.stop()
