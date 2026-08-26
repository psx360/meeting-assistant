#!/usr/bin/python3
import fcntl
import os
import time


I2C_SLAVE = 0x0703
ADDRESS = 0x3C
WIDTH = 128
HEIGHT = 64
FONT = {
    "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41),
    "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E),
}


fd = os.open("/dev/i2c-0", os.O_RDWR)
fcntl.ioctl(fd, I2C_SLAVE, ADDRESS)


def command(*values):
    os.write(fd, bytes((0x00, *values)))


def data(values):
    for offset in range(0, len(values), 16):
        os.write(fd, bytes((0x40,)) + bytes(values[offset:offset + 16]))


command(
    0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
    0xAD, 0x8B, 0xA1, 0xC8, 0xDA, 0x12, 0x81, 0x60,
    0xD9, 0x22, 0xDB, 0x35, 0xA4, 0xA6, 0xAF,
)
time.sleep(0.1)

pixels = [[False for _ in range(WIDTH)] for _ in range(HEIGHT)]
text = "HELLO"
scale = 3
glyph_width = 5 * scale
spacing = scale
total_width = len(text) * glyph_width + (len(text) - 1) * spacing
x0 = (WIDTH - total_width) // 2
y0 = (HEIGHT - 7 * scale) // 2

for char_index, char in enumerate(text):
    glyph = FONT[char]
    base_x = x0 + char_index * (glyph_width + spacing)
    for column, bits in enumerate(glyph):
        for row in range(7):
            if bits & (1 << row):
                for dx in range(scale):
                    for dy in range(scale):
                        pixels[y0 + row * scale + dy][base_x + column * scale + dx] = True

for page in range(HEIGHT // 8):
    command(0xB0 + page, 0x02, 0x10)
    row = []
    for x in range(WIDTH):
        value = 0
        for bit in range(8):
            if pixels[page * 8 + bit][x]:
                value |= 1 << bit
        row.append(value)
    data(row)

os.close(fd)
