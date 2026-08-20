from __future__ import annotations

from typing import TypedDict


class Palette(TypedDict):
    BG: str
    PANEL: str
    PANEL_2: str
    STROKE: str
    CYAN: str
    INDIGO: str
    TEXT: str
    MUTED: str
    GREEN: str
    AMBER: str
    RED: str
    INNER: str
    CANVAS: str
    TERM: str
    LIST: str
    ENTRY: str
    CHIP: str
    CHIP_HI: str
    SEL: str
    BTN2: str
    DANGER: str
    OK: str
    STATUS: str
    SHELL: str
    TOOLTIP: str
    TOOLTIP_BORDER: str
    SCENE: str
    RING2: str


PALETTES: dict[str, Palette] = {
    "aurora": {
        "BG": "#020617",
        "PANEL": "#081225",
        "PANEL_2": "#0b1730",
        "STROKE": "#1e2c52",
        "CYAN": "#22d3ee",
        "INDIGO": "#818cf8",
        "TEXT": "#dbeafe",
        "MUTED": "#8ea6ca",
        "GREEN": "#86efac",
        "AMBER": "#fbbf24",
        "RED": "#fb7185",
        "INNER": "#0b152b",
        "CANVAS": "#050d1d",
        "TERM": "#030915",
        "LIST": "#081022",
        "ENTRY": "#040a16",
        "CHIP": "#0f1f3c",
        "CHIP_HI": "#183159",
        "SEL": "#1c3257",
        "BTN2": "#143049",
        "DANGER": "#3a1a24",
        "OK": "#173f2e",
        "STATUS": "#0d2332",
        "SHELL": "#203662",
        "TOOLTIP": "#0a1529",
        "TOOLTIP_BORDER": "#24426d",
        "SCENE": "#050b16",
        "RING2": "#22416a",
    },
    "cyber": {
        "BG": "#0b0b14",
        "PANEL": "#10101d",
        "PANEL_2": "#161627",
        "STROKE": "#2a2a4a",
        "CYAN": "#00e5a0",
        "INDIGO": "#7aa2ff",
        "TEXT": "#d6f5ff",
        "MUTED": "#8aa0c8",
        "GREEN": "#57ffa8",
        "AMBER": "#ffd166",
        "RED": "#ff5c7a",
        "INNER": "#121224",
        "CANVAS": "#0a0a18",
        "TERM": "#07070f",
        "LIST": "#0f0f20",
        "ENTRY": "#080812",
        "CHIP": "#181830",
        "CHIP_HI": "#222244",
        "SEL": "#1d1d40",
        "BTN2": "#1a1a35",
        "DANGER": "#3a1222",
        "OK": "#10352a",
        "STATUS": "#0d2b24",
        "SHELL": "#222240",
        "TOOLTIP": "#101026",
        "TOOLTIP_BORDER": "#2a2a55",
        "SCENE": "#0a0a16",
        "RING2": "#223055",
    },
    "light": {
        "BG": "#f4f7fb",
        "PANEL": "#ffffff",
        "PANEL_2": "#eef3fa",
        "STROKE": "#c9d6e8",
        "CYAN": "#0891b2",
        "INDIGO": "#6366f1",
        "TEXT": "#0f1a2e",
        "MUTED": "#5b6b84",
        "GREEN": "#16a34a",
        "AMBER": "#d97706",
        "RED": "#dc2626",
        "INNER": "#e9f1fa",
        "CANVAS": "#f6faff",
        "TERM": "#ffffff",
        "LIST": "#f4f8fe",
        "ENTRY": "#ffffff",
        "CHIP": "#dbe7f5",
        "CHIP_HI": "#c2d6ec",
        "SEL": "#c9daf0",
        "BTN2": "#d6e4f2",
        "DANGER": "#f8d7da",
        "OK": "#d1e7dd",
        "STATUS": "#d6ecdf",
        "SHELL": "#dce7f3",
        "TOOLTIP": "#ffffff",
        "TOOLTIP_BORDER": "#c9d6e8",
        "SCENE": "#eef4fb",
        "RING2": "#c9d8ea",
    },
}

THEME_ORDER = ["aurora", "cyber", "light"]
