from dataclasses import replace

import pytest

from slackgif.config import GifInfo, is_compliant, resolve_preset, validate_limits
from slackgif.errors import UsageError


def info(**kw):
    base = dict(
        width=500,
        height=625,
        frames=100,
        fps=10.0,
        duration_s=10.0,
        loop=0,
        loop_known=True,
        has_alpha=False,
        variable_delays=False,
        size_bytes=4_000_000,
    )
    base.update(kw)
    return GifInfo(**base)


def preset(name, **kw):
    kwargs = dict(max_size=None, quality=None, loop=None, fit=None, background=None)
    kwargs.update(kw)
    return resolve_preset(name, **kwargs)


def test_message_preset():
    p = preset("message")
    assert p.policy.target_bytes == 5_242_880
    assert p.policy.max_width == 1024
    assert p.policy.margin == 0.99
    assert p.fixed_canvas is None


def test_emoji_preset():
    p = preset("emoji")
    assert p.policy.target_bytes == 131_072
    assert p.policy.margin == 0.97
    assert p.policy.max_frames == 50
    assert p.fixed_canvas == (128, 128)
    assert p.loop == "forever"
    assert p.keep_transparency is True


def test_custom_requires_target_or_quality():
    with pytest.raises(UsageError):
        preset("custom")


def test_compliance_copy_predicate():
    message = preset("message")
    emoji = preset("emoji")
    assert is_compliant(info(), message)
    assert not is_compliant(
        info(width=3000, height=3000, size_bytes=3_000_000), message
    )
    assert not is_compliant(info(width=64, height=64, size_bytes=1000), emoji)
    assert is_compliant(info(width=128, height=128, frames=50, size_bytes=1000), emoji)
    assert not is_compliant(info(loop=None), emoji)


def test_quality_alone_has_no_target():
    p = preset("message", quality=80)
    assert p.policy.target_bytes is None
    assert p.policy.fixed_quality == 80


def test_transforms_force_reencode():
    message = preset("message", background="white")
    assert message.keep_transparency is False
    assert not is_compliant(info(has_alpha=True, size_bytes=1000), message)
    fixed = preset("message", max_size="5MB", quality=80)
    assert not is_compliant(info(size_bytes=1000), fixed)


def test_custom_max_fps_none_is_no_cap():
    custom = preset("custom", max_size="5MB")
    assert custom.policy.max_fps is None
    assert is_compliant(info(fps=100.0, size_bytes=1000), custom)


def test_fit_pad_normalized():
    custom = preset("custom", max_size="5MB", fit="pad")
    assert custom.fit == "contain"


def test_max_size_overrides_preset_target():
    emoji = preset("emoji", max_size="1KB")
    assert emoji.policy.target_bytes == 1024


def test_background_and_keep_transparency_conflict():
    with pytest.raises(UsageError):
        preset("message", background="white", keep_transparency=True)


def test_loop_validation():
    assert preset("message", loop="forever").loop == "forever"
    assert preset("message", loop="once").loop == "once"
    assert preset("message", loop="3").loop == 3
    with pytest.raises(UsageError):
        preset("message", loop="-1")
    with pytest.raises(UsageError):
        preset("message", loop="sometimes")


def test_unknown_preset_rejected():
    with pytest.raises(UsageError):
        preset("avatar")


def test_validate_limits_rejects_large_inputs():
    policy = preset("message").policy
    with pytest.raises(UsageError):
        validate_limits(info(frames=20_000), policy)
    with pytest.raises(UsageError):
        validate_limits(info(width=20_000, height=20_000), policy)
    with pytest.raises(UsageError):
        validate_limits(info(duration_s=1000.0), policy)


def test_allow_large_bypasses_limits():
    policy = replace(preset("message").policy, allow_large=True)
    validate_limits(info(frames=20_000, duration_s=1000.0), policy)


def test_background_always_requires_reencode():
    message = preset("message", background="white")
    assert not is_compliant(info(has_alpha=False, size_bytes=1000), message)


def test_validate_limits_rejects_total_pixels():
    policy = preset("message").policy
    with pytest.raises(UsageError):
        validate_limits(info(width=5000, height=5000, frames=20), policy)
