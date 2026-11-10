"""Key names accepted by press_key, mapped to HID keyboard usage codes."""

from __future__ import annotations

# Modifier bits of a HID keyboard report.
MODS = {"ctrl": 0x01, "shift": 0x02, "alt": 0x04, "cmd": 0x08, "gui": 0x08}
# HID keyboard usage codes for named keys.
KEYS = {
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29, "backspace": 0x2A,
    "delete": 0x2A, "tab": 0x2B, "space": 0x2C, "right": 0x4F, "left": 0x50,
    "down": 0x51, "up": 0x52, "home": 0x4A, "end": 0x4D, "pageup": 0x4B, "pagedown": 0x4E,
    "forwarddelete": 0x4C,
}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEYS[c] = 0x04 + i
for i, c in enumerate("1234567890"):
    KEYS[c] = 0x1E + i


def parse_key(spec: str) -> tuple[int, int]:
    """'cmd+v' -> (modifier bits, usage code)."""
    parts = [s.strip().lower() for s in spec.split("+") if s.strip()]
    if not parts:
        raise ValueError("empty key")
    mods = 0
    for m in parts[:-1]:
        if m not in MODS:
            raise ValueError(f"unknown modifier {m!r}; expected one of {sorted(MODS)}")
        mods |= MODS[m]
    key = parts[-1]
    if key not in KEYS:
        raise ValueError(f"unknown key {key!r}")
    return mods, KEYS[key]
