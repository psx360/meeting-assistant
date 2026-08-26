#!/usr/bin/python3
import time
import gpiod

chip = gpiod.Chip("gpiochip4")
line = chip.get_line(17)
line.request(consumer="button-test", type=gpiod.LINE_REQ_EV_BOTH_EDGES)
print(f"START level={line.get_value()}", flush=True)
deadline = time.monotonic() + 15
last = None
count = 0
while time.monotonic() < deadline:
    if not line.event_wait(sec=0, nsec=100_000_000):
        continue
    event = line.event_read()
    now = time.monotonic()
    edge = "FALLING/pressed" if event.type == gpiod.LineEvent.FALLING_EDGE else "RISING/released"
    delta = "-" if last is None else f"{(now-last)*1000:.0f}ms"
    print(f"{edge} delta={delta}", flush=True)
    last = now
    count += 1
print(f"END level={line.get_value()} events={count}", flush=True)
