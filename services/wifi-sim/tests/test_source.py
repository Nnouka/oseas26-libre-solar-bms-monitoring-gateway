import os
import threading
import time

import pytest

from source import open_source
from test_thingset import NATIVE_SIM_LINE


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="serial:PORT"):
        open_source("ftp://nope")


@pytest.mark.skipif(os.name == "nt", reason="uses a Unix pseudo-terminal as a fake serial port")
def test_serial_source_reads_lines_from_a_port():
    master, slave = os.openpty()                       # a fake serial port pair
    port = os.ttyname(slave)

    def device():                                      # the "BMS" writes one noisy line, then a good one
        time.sleep(0.3)
        os.write(master, b"uart:~$ \r\n" + NATIVE_SIM_LINE.encode() + b"\r\n")

    threading.Thread(target=device, daemon=True).start()
    lines = open_source(f"serial:{port}@115200")
    got = [next(lines).strip() for _ in range(2)]
    assert got[1] == NATIVE_SIM_LINE
