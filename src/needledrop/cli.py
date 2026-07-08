"""needledrop CLI: run | setup | version.

`setup` is an interactive wizard (every prompt shows a sane default;
`--yes` takes all defaults non-interactively). It never writes to /etc -
it prints the install commands instead.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import shutil
import sys
from pathlib import Path

from needledrop import __version__
from needledrop.config import (
    AudioCfg,
    Config,
    DeviceCfg,
    SonosCfg,
    default_config_path,
    save_config,
)
from needledrop.devices import SoundCard, list_cards
from needledrop.units import systemd_unit, udev_rule


def _soco_discover():
    """Lazy soco import so unit tests never touch network code."""
    import soco

    return soco.discover(timeout=10)


def _ask(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw or default


# -- setup wizard ----------------------------------------------------------


def _pick_card(yes: bool) -> DeviceCfg:
    cards = [c for c in list_cards() if c.capture and c.usb_id]
    if not cards:
        print("! No USB capture card found - plug in your ADC and edit the")
        print("  config later (audio.device.usb_id), or re-run setup.")
        return DeviceCfg()
    print("USB capture devices:")
    for i, c in enumerate(cards):
        print(f"  {i + 1}. {c.name}  (usb {c.usb_id}, card {c.index})")
    choice: SoundCard = cards[0]
    if not yes and len(cards) > 1:
        n = _ask("Pick a device", "1")
        try:
            choice = cards[int(n) - 1]
        except (ValueError, IndexError):
            print(f"  (unrecognized choice, using {choice.name})")
    print(f"-> using {choice.name} ({choice.usb_id})")
    return DeviceCfg(usb_id=choice.usb_id, card_name=choice.name)


def _pick_zones(yes: bool) -> tuple[list[str], str]:
    print("Discovering Sonos zones (up to 10s)...")
    try:
        found = _soco_discover() or []
    except Exception as exc:
        print(f"! Sonos discovery failed ({exc}).")
        print("  Continuing with no zones - edit sonos.vinyl_zones in the config later.")
        return [], ""
    zones = sorted({z.player_name for z in found})
    if not zones:
        print("! No Sonos zones found - edit sonos.vinyl_zones in the config later.")
        return [], ""
    households = {h for h in (getattr(z, "household_id", None) for z in found) if h}
    if len(households) > 1:
        print(f"! {len(households)} Sonos households found - zones from all are listed;")
        print("  needledrop v1 plays into one household at a time.")
    print("Zones found: " + ", ".join(zones))
    selected = zones
    if not yes:
        raw = _ask("Zones to include (comma-separated)", ", ".join(zones))
        selected = [z.strip() for z in raw.split(",") if z.strip()]
    coordinator = selected[0] if selected else ""
    if not yes and selected:
        coordinator = _ask("Coordinator zone (pick a wired/newest unit)", coordinator)
    return selected, coordinator


def _print_next_steps(config_path: Path, cfg: Config, args) -> None:
    exec_path = shutil.which("needledrop") or "/usr/local/bin/needledrop"
    unit = systemd_unit(getpass.getuser(), exec_path, str(config_path))
    rule = udev_rule(cfg.audio.device.usb_id or "08bb:2902")
    unit_path = (Path(args.emit_systemd) if args.emit_systemd
                 else config_path.parent / "needledrop.service")
    rule_path = config_path.parent / "90-needledrop.rules"
    unit_path.write_text(unit, encoding="utf-8")
    rule_path.write_text(rule, encoding="utf-8")
    web, stream = cfg.stream.port, cfg.stream.port + 1
    print(f"""
Config written to {config_path}

Next steps
----------
1. Install the service (wrote {unit_path}):
     sudo cp {unit_path} /etc/systemd/system/needledrop.service
     sudo systemctl daemon-reload
     sudo systemctl enable --now needledrop

2. Keep the ADC awake (wrote {rule_path}):
     sudo cp {rule_path} /etc/udev/rules.d/90-needledrop.rules
     sudo udevadm control --reload

3. Open the dashboard:  http://<this-host>:{web}/
   (the MP3 stream itself runs on port {stream} - the dashboard's port + 1)

4. Drop the needle. Calibrate thresholds from the dashboard's gear menu.
""")


def _cmd_setup(args) -> int:
    config_path = Path(args.config) if args.config else default_config_path()
    device = _pick_card(args.yes)
    zones, coordinator = _pick_zones(args.yes)
    cfg = Config(
        audio=AudioCfg(device=device),
        sonos=SonosCfg(vinyl_zones=zones, preferred_coordinator=coordinator),
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_config(cfg, config_path)
    if not zones:
        # An empty zone list fails load_config() at daemon start; enabling the
        # service now would crash-loop under systemd's Restart=always.
        print(
            f"\n!! No Sonos zones configured. Config written to {config_path},\n"
            "!! but DO NOT enable the service yet: edit sonos.vinyl_zones first\n"
            "!! (or re-run `needledrop setup` when your Sonos system is reachable)."
        )
        return 1
    _print_next_steps(config_path, cfg, args)
    return 0


# -- run / version -----------------------------------------------------------


def _cmd_run(args) -> int:
    from needledrop.app import main_async

    config_path = Path(args.config) if args.config else default_config_path()
    try:
        asyncio.run(main_async(config_path))
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="needledrop",
        description="Drop the needle. Your whole house plays.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the daemon")
    p_run.add_argument("--config", help="config file path (default: XDG/system lookup)")

    p_setup = sub.add_parser("setup", help="interactive setup wizard")
    p_setup.add_argument("--config", help="where to write the config")
    p_setup.add_argument("--yes", action="store_true", help="non-interactive, take defaults")
    p_setup.add_argument("--emit-systemd", help="write the systemd unit to this path")

    sub.add_parser("version", help="print version")

    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"needledrop {__version__}")
        return 0
    if args.command == "setup":
        return _cmd_setup(args)
    return _cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
