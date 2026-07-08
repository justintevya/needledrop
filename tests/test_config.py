import pytest
from pathlib import Path
from needledrop.config import load_config, save_config, ConfigError

MINIMAL = """
audio: {device: {usb_id: "08bb:2902"}}
sonos: {vinyl_zones: [Living Room]}
"""


def test_load_minimal_fills_defaults(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(MINIMAL)
    cfg = load_config(p)
    assert cfg.detect.music_on_db == -45.0
    assert cfg.stream.port == 8341
    assert cfg.sonos.vinyl_zones == ["Living Room"]


def test_bad_threshold_order_rejected(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(MINIMAL + "detect: {music_on_db: -60, music_off_db: -50}\n")
    with pytest.raises(ConfigError, match="music_on_db"):
        load_config(p)


def test_roundtrip(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(MINIMAL)
    cfg = load_config(p)
    save_config(cfg, p)
    assert load_config(p) == cfg


def test_bitrate_whitelist(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(MINIMAL + "stream: {bitrate: 321}\n")
    with pytest.raises(ConfigError, match="bitrate"):
        load_config(p)
