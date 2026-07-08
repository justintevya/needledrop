"""USB VID:PID → ALSA card resolution via sysfs (survives card index reshuffles)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from needledrop.config import DeviceCfg

log = logging.getLogger(__name__)


class DeviceNotFound(Exception):
    """No sound card matches the configured device identity."""


@dataclass
class SoundCard:
    index: int
    name: str
    usb_id: str
    capture: bool


def _read_usb_id(device_dir: Path) -> str:
    """Walk up from the card's device dir (max 3 levels) looking for idVendor/idProduct.

    ALSA cards sit on a USB *interface* dir; the ids live on the USB *device* dir above it.
    `device` is a symlink on real sysfs - resolve it first, or Path.parent walks up the
    symlink's lexical location (/sys/class/sound/cardN/) instead of the USB device tree.
    """
    d = device_dir.resolve() if device_dir.exists() else device_dir
    for _ in range(4):
        vid = d / "idVendor"
        pid = d / "idProduct"
        if vid.is_file() and pid.is_file():
            return f"{vid.read_text().strip()}:{pid.read_text().strip()}".lower()
        if d.parent == d:
            break
        d = d.parent
    return ""


def list_cards(
    sysfs_root: Path = Path("/sys/class/sound"),
    proc_asound: Path = Path("/proc/asound"),
) -> list[SoundCard]:
    cards: list[SoundCard] = []
    if not sysfs_root.is_dir():
        return cards
    for entry in sorted(sysfs_root.iterdir()):
        m = re.fullmatch(r"card(\d+)", entry.name)
        if not m:
            continue
        index = int(m.group(1))
        usb_id = _read_usb_id(entry / "device")
        proc_card = proc_asound / f"card{index}"
        id_file = proc_card / "id"
        name = id_file.read_text().strip() if id_file.is_file() else ""
        capture = any(
            p.is_dir() and re.fullmatch(r"pcm\d+c", p.name)
            for p in (proc_card.iterdir() if proc_card.is_dir() else [])
        )
        cards.append(SoundCard(index=index, name=name, usb_id=usb_id, capture=capture))
    return cards


def resolve_card(cfg: DeviceCfg, cards: list[SoundCard]) -> SoundCard:
    """Resolution order: usb_id → card_name → sole capture-capable USB card → raise."""
    if cfg.usb_id:
        want = cfg.usb_id.lower()
        for card in cards:
            if card.usb_id == want:
                return card
    if cfg.card_name:
        for card in cards:
            if card.name == cfg.card_name:
                return card
    usb_capture = [c for c in cards if c.usb_id and c.capture]
    if len(usb_capture) == 1:
        card = usb_capture[0]
        log.warning(
            "no usb_id/card_name match; falling back to sole USB capture card %s (%s)",
            card.name,
            card.usb_id,
        )
        return card
    raise DeviceNotFound(
        f"no sound card matches usb_id={cfg.usb_id!r} card_name={cfg.card_name!r} "
        f"among {[(c.index, c.name, c.usb_id) for c in cards]}"
    )


def alsa_device_string(card: SoundCard) -> str:
    """Name-based ALSA device string - stable across card index reshuffles."""
    return f"hw:CARD={card.name},DEV=0"
