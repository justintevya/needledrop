from needledrop.config import DetectCfg
from needledrop.state import Phase, StateMachine


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_needle_drop_debounce_and_grace_cycle():
    clk = Clock()
    sm = StateMachine(DetectCfg(start_debounce_s=2, end_of_side_s=240), now=clk)
    assert sm.update(True).phase == Phase.SENSING
    clk.t = 1.0
    assert sm.update(True) is None
    clk.t = 2.1
    assert sm.update(True).phase == Phase.PLAYING
    clk.t = 100
    assert sm.update(False).phase == Phase.GRACE
    clk.t = 150
    assert sm.update(True).phase == Phase.PLAYING    # track gap over
    clk.t = 200
    sm.update(False)
    clk.t = 200 + 239
    assert sm.update(False) is None
    clk.t = 200 + 241
    assert sm.update(False).phase == Phase.IDLE


def test_flicker_in_sensing_resets():
    clk = Clock()
    sm = StateMachine(DetectCfg(start_debounce_s=2), now=clk)
    sm.update(True)
    clk.t = 1.0
    assert sm.update(False).phase == Phase.IDLE


def test_manual_overrides():
    clk = Clock()
    sm = StateMachine(DetectCfg(), now=clk)
    assert sm.manual_start().phase == Phase.PLAYING
    assert sm.manual_stop().phase == Phase.IDLE


def test_keep_playing_resets_grace():
    clk = Clock()
    sm = StateMachine(DetectCfg(end_of_side_s=240), now=clk)
    sm.manual_start()
    sm.update(False)          # PLAYING → GRACE
    clk.t = 230
    sm.keep_playing()
    clk.t = 460
    assert sm.update(False) is None  # countdown restarted at 230
    clk.t = 471
    assert sm.update(False).phase == Phase.IDLE
