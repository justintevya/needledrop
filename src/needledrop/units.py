"""systemd unit + udev rule text generation (never writes to /etc itself)."""

from __future__ import annotations


def systemd_unit(user: str, exec_path: str, config_path: str) -> str:
    """Systemd service for the needledrop daemon (non-root, audio group)."""
    return f"""\
[Unit]
Description=needledrop - drop the needle, your whole house plays
Wants=network-online.target
After=network-online.target sound.target

[Service]
Type=simple
User={user}
SupplementaryGroups=audio
ExecStart={exec_path} run --config {config_path}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def udev_rule(usb_id: str) -> str:
    """Udev rule disabling USB autosuspend for the phono ADC.

    PCM2902-class codecs glitch or vanish when the kernel autosuspends them
    mid-capture; pinning power/control to "on" is the fix.
    """
    vid, pid = usb_id.split(":")
    return (
        f"# needledrop: keep the phono ADC ({usb_id}) awake - no USB autosuspend\n"
        f'ACTION=="add", SUBSYSTEM=="usb", ATTR{{idVendor}}=="{vid}", '
        f'ATTR{{idProduct}}=="{pid}", ATTR{{power/control}}="on"\n'
    )
