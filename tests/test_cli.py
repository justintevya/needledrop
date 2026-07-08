from pathlib import Path

import needledrop.cli as cli
from needledrop.cli import main
from needledrop.config import load_config
from needledrop.devices import list_cards as real_list_cards


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert "0.1.0" in capsys.readouterr().out


class FakeZone:
    def __init__(self, name: str, household: str = "Sonos_H1"):
        self.player_name = name
        self.household_id = household


def fake_tree(tmp_path: Path):
    """Onboard PCH (no USB) + a PCM2902-class USB capture card."""
    sys_root = tmp_path / "sys"
    proc = tmp_path / "proc"
    for idx, name, usb in [(0, "PCH", None), (1, "CODEC", "08bb:2902")]:
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
        (cdir / "pcm0c" / "info").write_text("capture")
    return sys_root, proc


def test_setup_yes_writes_loadable_config(tmp_path, monkeypatch, capsys):
    sysr, proc = fake_tree(tmp_path)
    monkeypatch.setattr(cli, "list_cards", lambda: real_list_cards(sysr, proc))
    monkeypatch.setattr(
        cli, "_soco_discover", lambda: {FakeZone("Office"), FakeZone("Living Room")}
    )
    cfg_path = tmp_path / "config.yaml"

    assert main(["setup", "--config", str(cfg_path), "--yes"]) == 0

    cfg = load_config(cfg_path)
    assert cfg.audio.device.usb_id == "08bb:2902"
    assert cfg.audio.device.card_name == "CODEC"
    assert cfg.sonos.vinyl_zones == ["Living Room", "Office"]
    assert cfg.sonos.preferred_coordinator == "Living Room"

    out = capsys.readouterr().out
    assert "systemctl enable --now needledrop" in out
    assert ":8341" in out  # dashboard on the web port
    assert "8342" in out  # stream runs on web port + 1


def test_setup_yes_survives_discovery_failure(tmp_path, monkeypatch, capsys):
    sysr, proc = fake_tree(tmp_path)
    monkeypatch.setattr(cli, "list_cards", lambda: real_list_cards(sysr, proc))

    def boom():
        raise OSError("no network")

    monkeypatch.setattr(cli, "_soco_discover", boom)
    cfg_path = tmp_path / "config.yaml"
    # Exit 1: config written but the service must NOT be enabled (empty zones
    # fail load_config at daemon start -> systemd crash-loop).
    assert main(["setup", "--config", str(cfg_path), "--yes"]) == 1
    assert cfg_path.exists()
    assert "vinyl_zones" in cfg_path.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "DO NOT enable the service yet" in out
    assert "systemctl enable --now needledrop" not in out
