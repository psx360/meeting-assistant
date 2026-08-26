#!/usr/bin/python3
import time
import spidev


def encode(red, green, blue):
    bits = []
    # This LED uses RGB byte order (verified on the connected hardware).
    for value in (red, green, blue):
        for shift in range(7, -1, -1):
            bits.extend((1, 1, 0) if value & (1 << shift) else (1, 0, 0))
    output = bytearray()
    for offset in range(0, 72, 8):
        value = 0
        for bit in bits[offset:offset + 8]:
            value = (value << 1) | bit
        output.append(value)
    output.extend(b"\x00" * 24)
    return list(output)


spi = spidev.SpiDev()
spi.open(0, 1)
spi.max_speed_hz = 2_400_000
spi.mode = 0
spi.xfer2(encode(0, 0, 0))
time.sleep(0.1)
spi.close()
