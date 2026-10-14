"""Stand-in for the native_sim binary, speaking just enough of its shell for the client tests.

argv[1]: file that plays the role of the uart_1 pseudo-terminal (the real one prints its /dev/pts path).
Understands: `i2c write`, `i2c read`, `fake set-subcmd <code> <byte>...`, `fake live <text>`, `fake exit`.
"""
import re
import sys

uart1 = sys.argv[1]
direct = bytearray(0x80)
data = {}                      # subcommand code -> 4 bytes

print("*** Booting Zephyr OS build (fake) ***", flush=True)
print(f"uart_1 connected to pseudotty: {uart1}", flush=True)


def num(token: str) -> int:
    return int(token, 0)


for raw in sys.stdin:
    parts = raw.split()
    if not parts:
        continue
    if parts[:2] == ["i2c", "write"]:
        reg, payload = num(parts[4]), [num(t) for t in parts[5:]]
        direct[reg:reg + len(payload)] = bytes(payload)
        if reg == 0x3E and len(payload) == 2:                       # subcommand read request
            code = payload[0] | payload[1] << 8
            direct[0x40:0x44] = data.get(code, bytes(4))
    elif parts[:2] == ["i2c", "read"]:
        reg, n = num(parts[4]), num(parts[5])
        chunk = direct[reg:reg + n]
        hexes = [f"{b:02x}" for b in chunk]
        # like Zephyr's shell_hexdump: 8 bytes, an extra space, 8 more
        print("00000000: " + " ".join(hexes[:8]) + ("  " + " ".join(hexes[8:]) if len(hexes) > 8 else "")
              + "    |" + "." * len(chunk) + "|", flush=True)
    elif parts[:2] == ["fake", "set-subcmd"]:
        data[num(parts[2])] = bytes(num(t) for t in parts[3:]).ljust(4, b"\0")
    elif parts[:2] == ["fake", "live"]:
        with open(uart1, "a") as f:
            f.write(raw.split(None, 2)[2])
    elif parts[:2] == ["fake", "exit"]:
        sys.exit(3)
    print("uart:~$ ", flush=True)
