# needledrop

Drop the needle and your Sonos speakers play the record. Lift it, and once the side is over they stop and go back to whatever they were doing. That's it. That's the product.

[![CI](https://github.com/justintevya/needledrop/actions/workflows/ci.yml/badge.svg)](https://github.com/justintevya/needledrop/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/needledrop)](https://pypi.org/project/needledrop/)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

needledrop is a single program that runs on any Linux box with a cheap USB audio adapter. It listens to your turntable, figures out when a record is actually playing (and not just a bumped tonearm), and streams it to whichever Sonos rooms you pick. When the side runs out, it waits long enough for you to flip the record, then hands the speakers back to the house.

It comes with a little web dashboard for watching the levels and tweaking settings. Curious what it looks like? Any running copy will show you a fake session at `/?demo=1`.

## Why this exists

For years the trick for getting a turntable onto Sonos was held together with tape: run darkice and icecast, register a fake radio station on TuneIn, point Sonos at it, and pray. Then Sonos dropped custom stations, the TuneIn registration path rotted, and one day the whole thing just stopped. Mine died mid-record. I got annoyed enough to fix it properly.

needledrop replaces that entire stack with one program and zero cloud. It talks to the radio player already built into every Sonos unit, over plain UPnP, on your own network. Nothing gets installed on the speakers, nothing phones home, and it keeps working with the internet unplugged. No TuneIn, no accounts, no plugins, no prayer.

## What you need

| Piece | What works |
|---|---|
| Adapter | Any PCM2902-class USB phono adapter, roughly 20 to 40 bucks. The Behringer UFO202 has a phono preamp built in. The Behringer UCA202 is line level. The ART USB Phono Plus works too, as do the countless identical-chipset clones. Or run any phono preamp into any USB line-in codec. |
| Turntable | Anything with a needle. If it has a line output, skip the phono preamp. |
| Computer | Any Linux box. A Raspberry Pi 3, 4, or 5 is plenty. On a Pi, put the adapter on a powered hub, because the onboard ports brown out under load. |
| Speakers | Any Sonos players, current S2 or ancient S1, all the way down to the oldest Play:1 firmware. |

## Install

You need Python 3.11 or newer. The short version:

```bash
uv tool install needledrop        # or: pipx install needledrop
needledrop setup                  # asks which adapter and which rooms, writes the config
```

Plain pip works too, in a venv if you like:

```bash
python -m venv .venv && . .venv/bin/activate
pip install needledrop
needledrop setup
```

Either way, `needledrop setup` writes the config and prints the exact commands to install the systemd service and the udev rule. Run those, then:

```bash
sudo systemctl enable --now needledrop
```

Open `http://<your-box>:8341/` and put a record on.

About that udev rule (`90-needledrop.rules`): it stops Linux from power-managing your USB adapter into a coma. Install it with the two commands the wizard prints. Skip it and the cheaper adapters have a habit of vanishing halfway through side two.

## First record

Drop the needle and give it a couple of seconds. The dashboard climbs from IDLE to PLAYING, your rooms group up, and the record comes out of every speaker you picked. When the side ends, needledrop waits a few minutes (long enough to flip) before it stops, ungroups, and restores everyone's volume.

If it doesn't start when it should, or won't stop when it should, it's almost always the detection thresholds, and the fix is the calibration wizard behind the gear icon. It records ten seconds of your needle-up noise floor and ten seconds of a silent groove, then suggests the two numbers that separate music from silence on your gear. Cartridges, preamps, and adapter gain vary wildly, so the defaults are just a starting point.

## How it works

```
ALSA capture ─► ring buffer ─┬─► detector (high-pass, RMS, hysteresis) ─► state machine ─► SoCo
(arecord or     (PCM, 4s)    │                                            │                (group, volume,
 sounddevice,                └─► MP3 encoder (lameenc, 320 CBR)           │                 play, stop, watchdog)
 44.1k, 16-bit,                      │                                    ▼
 stereo)                             └─► HTTP fan-out (/stream.mp3)   websocket for state and VU
                aiohttp app: dashboard, REST API, websocket, /healthz  (audio stream on port + 1)
```

Four states, one loop:

- **IDLE.** Silence on the input. Sonos is left alone and the stream stays warm.
- **SENSING.** Signal detected. A short debounce (2 seconds by default) makes sure it's music and not you dusting the platter.
- **PLAYING.** Rooms are grouped under one coordinator and playing the stream. A watchdog re-issues play every 10 seconds in case a player wanders off.
- **GRACE.** The side went quiet. It streams silence for a grace window (4 minutes by default, enough to flip the record) before stopping Sonos, ungrouping, and putting the volumes back.

## Configuration

The config lives at `~/.config/needledrop/config.yaml`, or `/etc/needledrop/config.yaml` for a system install. The setup wizard writes it, and the settings panel on the dashboard edits it live. Every key, with its default:

| Key | Default | What it does |
|---|---|---|
| `audio.device.usb_id` | `""` | The USB vendor:product id of your adapter (from `lsusb`), for example `08bb:2902`. Matched through sysfs, so it survives reboots and port shuffles. |
| `audio.device.card_name` | `""` | ALSA card name fallback (like `CODEC`) if the usb_id misses. If both miss and there's exactly one USB capture card, it gets used with a warning. |
| `audio.sample_rate` | `44100` | Fixed at 44100, the one rate old and new Sonos both accept as radio. Anything else is rejected. |
| `detect.highpass_hz` | `40.0` | High-pass corner. Cuts turntable rumble and cheap-adapter DC offset so they don't register as music. |
| `detect.music_on_db` | `-45.0` | Smoothed level above this counts as music. Must sit above `music_off_db`. |
| `detect.music_off_db` | `-55.0` | Smoothed level below this counts as silence. The gap between the two is hysteresis, so a quiet passage doesn't flap the state. |
| `detect.start_debounce_s` | `2.0` | Music must hold this long before playback starts. |
| `detect.end_of_side_s` | `240.0` | Silence this long during GRACE means the side is over and Sonos stops. Four minutes buys you a leisurely flip. |
| `stream.port` | `8341` | Port for the dashboard, API, and websocket. The audio itself is served on `port + 1`, with raw framing the oldest firmware accepts. |
| `stream.bitrate` | `320` | MP3 bitrate, one of 128, 192, 256, 320. Constant bitrate only, because old players have buffer bugs with variable bitrate radio. |
| `sonos.vinyl_zones` | `[]` | The room names to pull into vinyl playback. Must be non-empty. |
| `sonos.preferred_coordinator` | `""` | The room that pulls the stream and feeds the rest of the group. Pick a wired or newer player. Empty means the first available vinyl room. |
| `sonos.dont_interrupt_busy` | `true` | Rooms already playing something else get skipped (and flagged on the dashboard) instead of hijacked. |
| `sonos.master_volume` | `null` | Set every vinyl room to this volume on start. `null` leaves volumes alone. Either way, the old volumes come back when it stops. |

There are no secrets in this file. There's nowhere to put one.

## About the delay

The gap between the stylus and the sound is 2 to 6 seconds, and no setting will tune it away. Sonos treats the stream as internet radio and buffers it like any station, which is also exactly why it never skips. Capture and encoding add well under half a second; the rest is the players' own buffer. You're not listening along with the needle, you're listening a few seconds behind it. For playing records through the house, that's fine. For monitoring while you cut a lathe record, it's not.

## Security

needledrop is open on the local network on purpose. Anyone on your LAN can open the dashboard and start or stop playback. That's the same deal Sonos already gives you: Sonos control is unauthenticated UPnP, so anyone on the network can already drive your speakers from any Sonos app. needledrop adds no new exposure and stores no secrets. The config is room names and threshold numbers, nothing more.

If your network has people on it you don't trust, put the dashboard behind a reverse proxy with a login (Caddy `basic_auth`, nginx `auth_request`, Authelia, take your pick) and firewall the two ports down to the proxy. The speakers only need to reach the stream port (`stream.port + 1`).

## Troubleshooting

**Playback never starts, or never stops.** Thresholds, nine times out of ten. Run the calibration wizard on the dashboard. It measures your needle-up noise floor and a silent groove and sets `music_on_db` and `music_off_db` for your actual gear.

**The `xruns` counter keeps climbing** (footer and `/healthz`). Those are capture overruns: samples arrived faster than they were read. A few here and there are harmless. Steady growth points at a starved USB bus (on a Pi, use a powered hub), CPU contention, or a desktop sound server fighting you for the device.

**Which room should be the coordinator?** A wired or newer player. The coordinator is the one player that pulls the stream from needledrop and feeds it to everyone else, so make it your strongest link, not a battery speaker at the far end of the house.

**Notes for old firmware.** Quick connect-and-disconnect probes in the log before playback are normal; players poke the stream (sometimes from a different unit in the fleet) before they tune in. needledrop serves the audio with raw HTTP/1.0 framing, no chunked encoding, and frame-aligned starts, because the oldest Play:1 firmware rejects anything else. Confirmed working on firmware 86.7.

**PipeWire and PortAudio.** On desktop-style systems the PipeWire PortAudio shim can deliver a fraction of real time and a flood of xruns. That's why the `arecord` subprocess backend is the default: it talks straight to ALSA and doesn't care which sound server thinks it owns the box.

## Development

```bash
git clone https://github.com/justintevya/needledrop && cd needledrop
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
pytest -q          # full suite, no hardware or network needed
ruff check .
```

Where things live (`src/needledrop/`):

- `cli.py`, the `run`, `setup`, and `version` commands
- `config.py`, dataclasses and YAML load, validate, save
- `devices.py`, USB vendor:product to ALSA card resolution through sysfs
- `capture.py`, the capture supervisor with unplug and backoff recovery
- `ringbuf.py`, a thread-safe PCM ring buffer
- `detect.py`, a hand-rolled biquad high-pass with RMS and hysteresis
- `state.py`, the IDLE, SENSING, PLAYING, GRACE machine with an injectable clock
- `encode.py`, the lameenc MP3 wrapper
- `stream.py`, the listener hub and raw-framing stream server
- `sonos.py`, SoCo grouping, busy-skip, watchdog, and volume restore
- `web.py`, REST, websocket, healthz, and the static dashboard
- `app.py`, the composition root
- `units.py`, systemd unit and udev rule text

Pull requests are welcome. Good starting points: an Opus or AAC encoder backend (the encoder already sits behind an interface), MQTT or Home Assistant state publishing, and support for a split S1 and S2 fleet. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
