"""Part 3: print the line our code produces for the empty chip of native_sim."""
from firmware import ChipReading, Firmware
from thingset import decode_live, encode_live

fw = Firmware()
fw.boot(ChipReading.empty())
for k in range(3):
    fw.update(ChipReading.empty(), now_ms=1000.0 * k)

line = encode_live(fw.meas())
print(line)
print("CRC ok, BMS state =", decode_live(line)["rBmsState"])
