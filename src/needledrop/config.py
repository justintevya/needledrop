"""Configuration schema, YAML load/validate/save for needledrop.

Zero secrets belong in the config file - Sonos control is unauthenticated LAN UPnP.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

VALID_BITRATES = {128, 192, 256, 320}


class ConfigError(Exception):
    """Raised when a config file is invalid; message carries dotted field paths."""


@dataclass
class DeviceCfg:
    usb_id: str = ""
    card_name: str = ""


@dataclass
class AudioCfg:
    device: DeviceCfg = field(default_factory=DeviceCfg)
    sample_rate: int = 44100


@dataclass
class DetectCfg:
    highpass_hz: float = 40.0
    music_on_db: float = -45.0
    music_off_db: float = -55.0
    start_debounce_s: float = 2.0
    end_of_side_s: float = 240.0


@dataclass
class StreamCfg:
    port: int = 8341
    bitrate: int = 320


@dataclass
class SonosCfg:
    vinyl_zones: list[str] = field(default_factory=list)
    preferred_coordinator: str = ""
    dont_interrupt_busy: bool = True
    master_volume: int | None = None


@dataclass
class Config:
    audio: AudioCfg = field(default_factory=AudioCfg)
    detect: DetectCfg = field(default_factory=DetectCfg)
    stream: StreamCfg = field(default_factory=StreamCfg)
    sonos: SonosCfg = field(default_factory=SonosCfg)


def _build(cls, data: object, path: str):
    """Construct dataclass `cls` from a mapping, merging over field defaults."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(data).__name__}")
    nested = {"device": DeviceCfg}
    kwargs = {}
    valid = {f.name for f in cls.__dataclass_fields__.values()}
    for key, value in data.items():
        if key not in valid:
            raise ConfigError(f"{path}.{key}: unknown field")
        if key in nested and cls is AudioCfg:
            kwargs[key] = _build(nested[key], value, f"{path}.{key}")
        else:
            kwargs[key] = value
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _validate(cfg: Config) -> None:
    errors: list[str] = []
    if cfg.detect.music_on_db <= cfg.detect.music_off_db:
        errors.append(
            "detect.music_on_db must be greater than detect.music_off_db "
            f"({cfg.detect.music_on_db} <= {cfg.detect.music_off_db})"
        )
    if cfg.stream.bitrate not in VALID_BITRATES:
        errors.append(
            f"stream.bitrate must be one of {sorted(VALID_BITRATES)}, got {cfg.stream.bitrate}"
        )
    if not 1024 <= cfg.stream.port <= 65535:
        errors.append(f"stream.port must be 1024-65535, got {cfg.stream.port}")
    if not cfg.sonos.vinyl_zones:
        errors.append("sonos.vinyl_zones must be a non-empty list")
    if cfg.audio.sample_rate != 44100:
        errors.append(f"audio.sample_rate must be 44100, got {cfg.audio.sample_rate}")
    if errors:
        raise ConfigError("; ".join(errors))


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return config_from_dict(raw)


def config_from_dict(raw: dict) -> Config:
    """Build + validate a Config from an untrusted mapping (YAML or API PUT)."""
    known = {"audio", "detect", "stream", "sonos"}
    for key in raw:
        if key not in known:
            raise ConfigError(f"{key}: unknown top-level section")
    cfg = Config(
        audio=_build(AudioCfg, raw.get("audio"), "audio"),
        detect=_build(DetectCfg, raw.get("detect"), "detect"),
        stream=_build(StreamCfg, raw.get("stream"), "stream"),
        sonos=_build(SonosCfg, raw.get("sonos"), "sonos"),
    )
    _validate(cfg)
    return cfg


HEADER = "# managed by needledrop - no secrets belong in this file\n"


def save_config(cfg: Config, path: Path) -> None:
    data = asdict(cfg)
    text = HEADER + yaml.safe_dump(data, sort_keys=True, default_flow_style=False)
    path.write_text(text, encoding="utf-8")


def default_config_path() -> Path:
    env = os.environ.get("NEEDLEDROP_CONFIG")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    user_path = base / "needledrop" / "config.yaml"
    if user_path.exists():
        return user_path
    system_path = Path("/etc/needledrop/config.yaml")
    if system_path.exists():
        return system_path
    return user_path
