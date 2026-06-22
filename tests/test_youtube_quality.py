"""Тесты качества видео YouTube и адаптивного polling."""

import pytest

from apiat.tools.youtube_tool import YoutubeTool, _ALLOWED_QUALITIES, _DEFAULT_QUALITY
from apiat.cli import _PollMode


# --- quality clamping ---

def test_default_quality():
    assert YoutubeTool._clamp_quality(None) == _DEFAULT_QUALITY


def test_quality_exact_match():
    assert YoutubeTool._clamp_quality(480) == 480


def test_quality_rounds_down():
    assert YoutubeTool._clamp_quality(500) == 480
    assert YoutubeTool._clamp_quality(400) == 360
    assert YoutubeTool._clamp_quality(250) == 240


def test_quality_minimum():
    assert YoutubeTool._clamp_quality(100) == _ALLOWED_QUALITIES[0]


def test_quality_maximum_allowed():
    assert YoutubeTool._clamp_quality(1080) == 1080


def test_quality_above_max_clamped():
    assert YoutubeTool._clamp_quality(4320) == 1080


# --- adaptive polling ---

def test_poll_mode_starts_normal():
    p = _PollMode(60)
    assert p._mode == _PollMode.NORMAL


def test_poll_mode_goes_fast_on_two_requests():
    import time
    p = _PollMode(60)
    # Симулируем 2 запроса в пределах 2 минут
    now = time.monotonic()
    p._recent = [now - 10, now - 5]
    p._last_activity = now - 5
    sleep = p.next_sleep()
    assert p._mode == _PollMode.FAST
    assert 17 <= sleep <= 24


def test_poll_mode_goes_idle_after_30min():
    import time
    p = _PollMode(60)
    p._last_activity = time.monotonic() - 31 * 60
    p._recent = []
    sleep = p.next_sleep()
    assert p._mode == _PollMode.IDLE
    assert 120 <= sleep <= 420


def test_poll_mode_reverts_normal_after_9min():
    import time
    p = _PollMode(60)
    p._mode = _PollMode.FAST
    p._last_activity = time.monotonic() - 10 * 60
    p._recent = []
    sleep = p.next_sleep()
    assert p._mode == _PollMode.NORMAL
    assert 51 <= sleep <= 69  # base 60 ± 9


def test_poll_mode_record_activity_updates_last():
    import time
    p = _PollMode(60)
    before = p._last_activity
    time.sleep(0.01)
    p.record_activity(1)
    assert p._last_activity > before


def test_poll_mode_record_zero_does_not_update():
    import time
    p = _PollMode(60)
    before = p._last_activity
    time.sleep(0.01)
    p.record_activity(0)
    assert p._last_activity == before


def test_poll_mode_recent_pruned_after_2min():
    import time
    p = _PollMode(60)
    old = time.monotonic() - 200  # >2 мин назад
    p._recent = [old, old]
    p._last_activity = time.monotonic() - 5
    p.next_sleep()
    assert len(p._recent) == 0
