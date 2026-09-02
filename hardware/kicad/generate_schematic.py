#!/usr/bin/env python3
"""Generate the editable KiCad schematic for the Meeting Assistant wiring."""
import json
import os
from pathlib import Path

import kicad_sch_api as ksa


HERE = Path(__file__).resolve().parent
OUT = HERE / "meeting-assistant.kicad_sch"


def electrical_pin_position(component, pin_number):
    """Return the KiCad connection point (API 0.5.6 mirrors local Y)."""
    pin = component.get_pin(str(pin_number))
    if pin is None:
        raise ValueError(f"{component.reference} has no pin {pin_number}")
    return type(pin.position)(
        float(component.position.x) + float(pin.position.x),
        float(component.position.y) - float(pin.position.y),
    )


def label_pin(schematic, component, pin_number, net, length=7.62, point=None):
    point = point or electrical_pin_position(component, pin_number)
    x, y = float(point.x), float(point.y)
    # Standard connector symbols expose pins on the left; Conn_02x20 also
    # exposes the even column on the right. Route away from the symbol.
    center_x = float(component.position.x)
    direction = -1 if x < center_x else 1
    end = (x + direction * length, y)
    schematic.add_wire((x, y), end)
    schematic.add_label(net, end)


def add_connector(schematic, lib_id, reference, value, position, pin_nets, rows, columns=1):
    component = schematic.components.add(lib_id, reference, value, position)
    for pin_number, net in pin_nets.items():
        point = electrical_pin_position(component, pin_number)
        label_pin(schematic, component, pin_number, net, point=point)
    used = set(pin_nets)
    for pin_number in range(1, rows * columns + 1):
        if pin_number not in used:
            schematic.no_connects.add(electrical_pin_position(component, pin_number))
    return component


def main():
    symbol_dir = os.environ.get("KICAD_SYMBOL_DIR")
    if not symbol_dir:
        raise SystemExit("Set KICAD_SYMBOL_DIR to KiCad's share/kicad/symbols directory")

    schematic = ksa.create_schematic("Meeting Assistant — Radxa ROCK 2F wiring")
    schematic.add_text("RADXA ROCK 2F — physical 40-pin header", (65, 30), size=1.5)
    schematic.add_text("Two INMP441 microphones share I2S0; L/R selects stereo channel", (170, 30), size=1.27)
    schematic.add_text("HW-787AB OLED + encoder controls", (170, 92), size=1.27)
    schematic.add_text("Recording button: active low, external 11 kΩ pull-up", (155, 151), size=1.27)

    add_connector(
        schematic,
        "Connector_Generic:Conn_02x20_Odd_Even",
        "J1",
        "RADXA ROCK 2F GPIO (physical pins)",
        (76.2, 81.28),
        {
            1: "+3V3",
            6: "GND",
            11: "ENC_A_GPIO4_B7",
            12: "I2S0_BCLK_GPIO1_B5",
            13: "ENC_B_GPIO4_C0",
            15: "ENC_PUSH_GPIO4_C6",
            17: "+3V3",
            20: "GND",
            26: "REC_BUTTON_GPIO4_C1",
            29: "BACK_GPIO4_B5",
            31: "CONFIRM_GPIO1_B0",
            32: "OLED_SDA_I2C0",
            35: "I2S0_LRCK_GPIO1_B6",
            36: "OLED_SCL_I2C0",
            38: "I2S0_SD_GPIO1_B7",
        }, rows=20, columns=2,
    )

    add_connector(
        schematic,
        "Connector_Generic:Conn_01x06",
        "J2",
        "INMP441 LEFT",
        (203.2, 55.88),
        {
            1: "+3V3",
            2: "GND",
            3: "I2S0_BCLK_GPIO1_B5",
            4: "I2S0_LRCK_GPIO1_B6",
            5: "I2S0_SD_GPIO1_B7",
            6: "GND",
        }, rows=6,
    )
    schematic.add_text("J2 pins: VDD, GND, SCK, WS, SD, L/R=GND (LEFT)", (172, 70), size=1.0)

    add_connector(
        schematic,
        "Connector_Generic:Conn_01x06",
        "J3",
        "INMP441 RIGHT",
        (203.2, 81.28),
        {
            1: "+3V3",
            2: "GND",
            3: "I2S0_BCLK_GPIO1_B5",
            4: "I2S0_LRCK_GPIO1_B6",
            5: "I2S0_SD_GPIO1_B7",
            6: "+3V3",
        }, rows=6,
    )
    schematic.add_text("J3 pins: VDD, GND, SCK, WS, SD, L/R=3V3 (RIGHT)", (172, 89), size=1.0)

    add_connector(
        schematic,
        "Connector_Generic:Conn_01x09",
        "J4",
        "HW-787AB",
        (203.2, 119.38),
        {
            1: "+3V3",
            2: "GND",
            3: "BACK_GPIO4_B5",
            4: "ENC_B_GPIO4_C0",
            5: "ENC_A_GPIO4_B7",
            6: "ENC_PUSH_GPIO4_C6",
            7: "OLED_SCL_I2C0",
            8: "OLED_SDA_I2C0",
            9: "CONFIRM_GPIO1_B0",
        }, rows=9,
    )
    schematic.add_text("J4 pin order follows PCB silkscreen: 3V3 … CONFIRM", (172, 134), size=1.0)

    switch = schematic.components.add("Switch:SW_Push", "SW1", "RECORD START/STOP", (165.1, 162.56))
    label_pin(schematic, switch, 1, "GND")
    label_pin(schematic, switch, 2, "REC_BUTTON_GPIO4_C1")
    pullup = schematic.components.add("Device:R", "R1", "11k PULL-UP", (195.58, 157.48))
    label_pin(schematic, pullup, 1, "+3V3", length=7.62)
    label_pin(schematic, pullup, 2, "REC_BUTTON_GPIO4_C1", length=7.62)

    schematic.add_text("Do not connect WS2812B data without a 3.3→5 V level shifter", (88, 180), size=1.27)
    schematic.save(OUT)
    (HERE / "meeting-assistant.kicad_pro").write_text(json.dumps({"board": {}, "cvpcb": {}, "erc": {}, "meta": {"filename": "meeting-assistant.kicad_pro", "version": 1}, "net_settings": {}, "pcbnew": {}, "schematic": {}, "text_variables": {}}), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
