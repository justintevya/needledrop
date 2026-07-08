from pathlib import Path

import pytest

from needledrop.config import DeviceCfg
from needledrop.devices import DeviceNotFound, alsa_device_string, list_cards, resolve_card


def fake_tree(tmp_path: Path, cards: list[tuple[int, str, str | None]]):
    """cards: (index, name, usb 'vid:pid' or None). Builds /sys/class/sound/cardN + /proc/asound/cardN."""
    sys_root = tmp_path / "sys"
    proc = tmp_path / "proc"
    for idx, name, usb in cards:
        d = sys_root / f"card{idx}" / "device"
        d.mkdir(parents=True)
        if usb:
            vid, pid = usb.split(":")
            (d / "idVendor").write_text(vid + "\n")
            (d / "idProduct").write_text(pid + "\n")
        cdir = proc / f"card{idx}"
        cdir.mkdir(parents=True)
        (cdir / "id").write_text(name + "\n")
        (cdir / "pcm0c").mkdir(parents=True)
        (cdir / "pcm0c" / "info").write_text("capture")  # capture-capable marker
    return sys_root, proc


def test_resolve_by_usb_id(tmp_path):
    sysr, proc = fake_tree(tmp_path, [(0, "PCH", None), (1, "CODEC", "08bb:2902")])
    cards = list_cards(sysr, proc)
    card = resolve_card(DeviceCfg(usb_id="08bb:2902"), cards)
    assert card.name == "CODEC" and card.index == 1
    assert alsa_device_string(card) == "hw:CARD=CODEC,DEV=0"


def test_resolve_falls_back_to_name(tmp_path):
    sysr, proc = fake_tree(tmp_path, [(2, "CODEC", "1234:5678")])
    card = resolve_card(DeviceCfg(usb_id="08bb:2902", card_name="CODEC"), list_cards(sysr, proc))
    assert card.index == 2


def test_no_match_raises(tmp_path):
    sysr, proc = fake_tree(tmp_path, [(0, "PCH", None)])
    with pytest.raises(DeviceNotFound):
        resolve_card(DeviceCfg(usb_id="08bb:2902"), list_cards(sysr, proc))
