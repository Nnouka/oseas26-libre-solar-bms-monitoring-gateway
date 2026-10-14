import pytest

from thingset import crc32_hex, decode_live, encode_live

# Captured from the real firmware running in native_sim (docs/native_sim-tutorial.md).
NATIVE_SIM_LINE = (
    '#mLive {"Meas":{"rPackVoltage_V":0.00,"rStackVoltage_V":0.00,"rPackCurrent_A":0.00,'
    '"rCellTemps_degC":[-273.1,0.0],"rICTemp_degC":-273.1,"rMOSFETTemp_degC":-273.1,'
    '"rSOC_pct":0.0,"rErrorFlags":0,"rBmsState":3,"rCellVoltages_V":[0.000,0.000,0.000,0.000,'
    '0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000],'
    '"rCellAvgVoltage_V":null,"rCellMinVoltage_V":10.000,"rCellMaxVoltage_V":0.000,'
    '"rBalancingStatus":0}} 437123CA#'
)

def test_crc_of_the_captured_line_is_correct():
    statement = NATIVE_SIM_LINE[:-1].rpartition(" ")[0]
    assert crc32_hex(statement) == "437123CA"

def test_decode_reads_the_captured_line():
    meas = decode_live(NATIVE_SIM_LINE)
    assert meas["rBmsState"] == 3
    assert meas["rCellAvgVoltage_V"] is None # JSON null
    assert len(meas["rCellVoltages_V"]) == 16
    assert meas["rCellMinVoltage_V"] == 10.0
    
def test_encode_of_decode_gives_back_the_identical_line():
    assert encode_live(decode_live(NATIVE_SIM_LINE)) == NATIVE_SIM_LINE
    
def test_decode_rejects_a_corrupted_line():
    bad = NATIVE_SIM_LINE.replace('"rBmsState":3', '"rBmsState":2')
    with pytest.raises(ValueError, match="CRC"):
        decode_live(bad)
    with pytest.raises(ValueError):
        decode_live("hello")