from slackgif.config import GifInfo, Preset, SearchPolicy
from slackgif.search import compress, is_success


class FakeBackend:
    name = "fake"
    quality_levels = None

    def __init__(self, size_fn):
        self.size_fn = size_fn
        self.specs = []
        self.closed = False

    def available(self):
        return True

    def can_handle(self, info, preset):
        return True

    def encode(self, src, dst, spec, info, *, timeout_s=None):
        self.specs.append(spec)
        size = max(6, int(self.size_fn(spec)))
        dst.write_bytes(b"GIF89a" + b"\x00" * (size - 6))

    def close(self):
        self.closed = True


class DiscreteFakeBackend(FakeBackend):
    quality_levels = (0, 30, 60, 100)

    def __init__(self):
        super().__init__(
            lambda s: (
                5_000_000
                * (s.width / 500) ** 1.55
                * (s.fps / 10.0) ** 0.90
                * (0.50 + 0.005 * s.quality)
            )
        )


def fake_info(**kw):
    base = dict(
        width=500,
        height=625,
        frames=136,
        fps=10.0,
        duration_s=13.6,
        loop=0,
        loop_known=True,
        has_alpha=False,
        variable_delays=False,
        size_bytes=12_781_355,
    )
    base.update(kw)
    return GifInfo(**base)


def model(spec):
    # Deterministic, monotone, with a cliff at q=57.
    return (
        12_000_000
        * (spec.width / 500) ** 1.55
        * (spec.fps / 10.0) ** 0.90
        * (0.60 + 0.0040 * spec.quality)
        * (1.08 if spec.quality >= 57 else 1.0)
    )


def native_model(spec):
    # A base that fits at native dimensions with q_min, so the Q search runs.
    return (
        6_000_000
        * (spec.width / 500) ** 1.55
        * (spec.fps / 10.0) ** 0.90
        * (0.60 + 0.0040 * spec.quality)
        * (1.08 if spec.quality >= 57 else 1.0)
    )


def make_preset(target=5_242_880, fixed_canvas=None, **policy):
    base = dict(
        target_bytes=target,
        margin=0.99,
        max_width=1024,
        max_height=1024,
        max_fps=30,
        min_width=64,
        min_fps=5,
        min_quality=30,
        max_encodes=10,
    )
    base.update(policy)
    return Preset(
        name="test",
        policy=SearchPolicy(**base),
        fixed_canvas=fixed_canvas,
        fit="contain",
        background=None,
        keep_transparency=False,
        loop="preserve",
    )


def test_is_success_threshold():
    assert is_success(100, 100) is True
    assert is_success(101, 100) is False
    assert is_success(10**9, None) is True


def test_compliant_source_is_copied(tmp_path, make_gif):
    src = make_gif(size=(64, 48), frames=2)
    info = fake_info(width=64, height=48, size_bytes=src.stat().st_size)
    dst = tmp_path / "out.gif"
    backend = FakeBackend(model)
    result = compress(
        src,
        dst,
        preset=make_preset(),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert result.success and result.encodes == 0
    assert dst.read_bytes() == src.read_bytes()
    assert backend.specs == []


def test_quality_search_fills_budget(tmp_path, make_gif):
    src = make_gif(size=(500, 625), frames=4)
    info = fake_info(size_bytes=12_781_355)
    backend = FakeBackend(native_model)
    result = compress(
        src,
        tmp_path / "out.gif",
        preset=make_preset(),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert result.success
    assert result.candidate.size_bytes <= 5_242_880
    assert result.candidate.size_bytes >= 0.90 * 5_242_880
    assert result.candidate.spec.width == 500
    assert len(backend.specs) <= 10
    assert len({(s.width, s.height, s.fps, s.quality) for s in backend.specs}) == len(
        backend.specs
    )


def test_fixed_canvas_never_shrinks_width(tmp_path, make_gif):
    src = make_gif(size=(500, 625), frames=4)
    info = fake_info(size_bytes=src.stat().st_size)
    preset = make_preset(
        target=131_072,
        max_width=128,
        max_height=128,
        fixed_canvas=(128, 128),
        min_width=128,
        min_fps=2.5,
        max_frames=50,
        max_fps=15,
    )
    backend = FakeBackend(
        lambda s: 400_000 * (s.fps / 10.0) * (0.6 + 0.004 * s.quality)
    )
    result = compress(
        src,
        tmp_path / "out.gif",
        preset=preset,
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert all(s.width == 128 and s.height == 128 for s in backend.specs)
    assert all(s.max_frames == 50 for s in backend.specs)
    assert result.candidate.spec.width == 128


def test_never_upscales(tmp_path, make_gif):
    src = make_gif(size=(64, 48), frames=2)
    info = fake_info(width=64, height=48, size_bytes=src.stat().st_size)
    backend = FakeBackend(model)
    compress(
        src,
        tmp_path / "out.gif",
        preset=make_preset(target=500),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert all(s.width <= 64 and s.height <= 48 for s in backend.specs)


def test_candidate_between_budget_and_target_is_success(tmp_path, make_gif):
    src = make_gif(size=(500, 625), frames=4)
    info = fake_info(size_bytes=12_781_355)
    preset = make_preset(target=5_242_880, max_encodes=1)
    backend = FakeBackend(lambda s: 5_200_000)
    result = compress(
        src,
        tmp_path / "out.gif",
        preset=preset,
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert backend.specs
    assert result.candidate.size_bytes == 5_200_000
    assert result.success is True


def test_static_source_does_not_divide_by_zero(tmp_path, make_gif):
    src = make_gif(size=(64, 48), frames=1, durations=[100])
    info = fake_info(
        width=64,
        height=48,
        frames=1,
        fps=0.0,
        duration_s=0.0,
        size_bytes=src.stat().st_size,
    )
    backend = FakeBackend(lambda s: 10_000)
    result = compress(
        src,
        tmp_path / "out.gif",
        preset=make_preset(target=100),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert result.candidate is not None


def test_discrete_quality_does_not_repeat_commands(tmp_path, make_gif):
    src = make_gif(size=(500, 625), frames=4)
    info = fake_info(size_bytes=12_781_355)
    backend = DiscreteFakeBackend()
    compress(
        src,
        tmp_path / "out.gif",
        preset=make_preset(target=3_000_000),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    specs = {(s.width, s.height, s.fps, s.quality) for s in backend.specs}
    assert len(specs) == len(backend.specs)


def test_fixed_quality_without_target(tmp_path, make_gif):
    src = make_gif(size=(64, 48), frames=2)
    info = fake_info(width=64, height=48, size_bytes=src.stat().st_size)
    preset = make_preset(target=None, fixed_quality=80)
    backend = FakeBackend(lambda s: 10_000)
    result = compress(
        src,
        tmp_path / "out.gif",
        preset=preset,
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert result.success and len(backend.specs) == 1


def test_max_encodes_global_cap(tmp_path, make_gif):
    src = make_gif(size=(500, 625), frames=4)
    info = fake_info(size_bytes=12_781_355)
    backend = FakeBackend(native_model)
    compress(
        src,
        tmp_path / "out.gif",
        preset=make_preset(max_encodes=3),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert 0 < len(backend.specs) <= 3


def test_dimension_search_reduces_width(tmp_path, make_gif):
    src = make_gif(size=(500, 625), frames=4)
    info = fake_info(size_bytes=12_781_355)
    backend = FakeBackend(model)
    result = compress(
        src,
        tmp_path / "out.gif",
        preset=make_preset(target=3_000_000),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert result.success
    assert result.candidate.spec.width < 500
    assert len(backend.specs) > 1


def test_phase_c_uses_fixed_quality(tmp_path, make_gif):
    src = make_gif(size=(500, 625), frames=4)
    info = fake_info(size_bytes=12_781_355)
    backend = FakeBackend(lambda s: 1_000)  # never reaches target=1
    compress(
        src,
        tmp_path / "out.gif",
        preset=make_preset(target=1, fixed_quality=80),
        backend=backend,
        info=info,
        validator=lambda *a: None,
    )
    assert backend.specs
    assert all(s.quality == 80 for s in backend.specs)
