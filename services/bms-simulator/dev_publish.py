import logging
import time

from publisher import Publisher
from sim import Simulator

logging.basicConfig(level=logging.INFO)
pub = Publisher(Simulator(seed=1), "localhost", 1883, "haske-bms-001", interval_s=1.0, speed=300.0)
pub.start()
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pub.stop()
