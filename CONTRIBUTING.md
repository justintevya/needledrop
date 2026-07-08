# Contributing to needledrop

Thanks for helping out. The bar is short: tests first, small modules, and no new dependencies without a good reason.

## Dev setup

```bash
git clone https://github.com/justintevya/needledrop && cd needledrop
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
pytest -q && ruff check .
```

The whole suite runs with no audio hardware and no network. Anything that touches the adapter or Sonos sits behind an injectable fake (see `tests/fakes.py` and the fake-sysfs helpers). Please keep it that way.

## What I ask

- Test first. Write the failing test, then the code. Pull requests that add behavior without tests will not merge.
- Ruff clean. `ruff check .` has to pass (line length 100).
- The runtime dependencies are fixed: `aiohttp`, `sounddevice`, `lameenc`, `soco`, `PyYAML`. No numpy or scipy in the runtime path, since the DSP is hand-rolled on purpose.
- Modules stay small, around 300 lines. Split instead of growing one.
- Injectable time. Anything that depends on timing takes a `now` callable so it can run against a fake clock.

## Commits

Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`. One logical change per commit, and the suite green at every commit.

## Good first pull requests

An Opus or AAC encoder backend, MQTT or Home Assistant publishing, and support for a split S1 and S2 fleet. See the development section in the README.
