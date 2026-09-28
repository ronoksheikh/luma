"""Colour, gradients, layers (premultiplied readback, roll-off, light), motion, text, camera."""
import math

import numpy as np
import pytest
import skia

from luma_engine.brand import BrandKit, Color, Gradient, linear_to_srgb, srgb_to_linear
from luma_engine.camera import Camera, apply_h, lambert, to_light_vector
from luma_engine.layers import LIGHT_GAIN, Frame, coverage_paint, motion_blur, read_surface, rolloff
from luma_engine.motion import EASINGS, Channel, Spring, Timeline, cubic_bezier, stagger
from luma_engine.text import shape_text


# ---------------------------------------------------------------- colour ------------
def test_srgb_roundtrip_and_known_values():
    x = np.linspace(0, 1, 1001, dtype=np.float32)
    assert np.abs(linear_to_srgb(srgb_to_linear(x)) - x).max() < 2e-6
    assert srgb_to_linear(0.5) == pytest.approx(0.21404, abs=1e-5)
    assert Color.of("#FF5A5F").hex == "#FF5A5F"
    assert Color.of("rgb(255, 0, 0)").hex == "#FF0000"
    assert Color.of("coral").hex == "#FF7F50"


def test_brandkit_avoid():
    kit = BrandKit.from_dict({"colors": {"ember": "#FF5A5F"}, "avoid": ["#FF0000"]})
    assert kit.violates_avoid("#F80404") is not None
    assert kit.violates_avoid("#0B0F2A") is None


# ---------------------------------------------------------------- gradients ---------
def test_gradient_lut_interpolates_in_srgb():
    g = Gradient.linear(["#000000", "#FFFFFF"])
    lut = g.lut(3)
    assert lut[1][:3] == pytest.approx([0.5, 0.5, 0.5], abs=1e-6)  # sRGB midpoint, not linear


def test_skia_shader_matches_float_gradient_obb_with_transform():
    g = Gradient.linear([(0, "#FF5A5F"), (1, "#FFB547")], (0, 1), (1, 0))
    g.transform = np.array([[1, 0.2, 0], [0, 1, 0], [0, 0, 1]])
    bbox = (20.0, 10.0, 200.0, 120.0)
    W, H = 256, 160
    ref = g.render(W, H, bbox)[..., :3]
    surf = skia.Surface(W, H)
    surf.getCanvas().drawPaint(skia.Paint(Shader=g.shader(bbox)))
    got = surf.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)[..., :3] / 255.0
    assert np.abs(got - ref).max() < 2.5 / 255


def test_radial_focal_gradient_matches_skia():
    g = Gradient.radial([(0, "#FFFFFF"), (1, "#003366")], center=(0.5, 0.5), r=0.5, focal=(0.35, 0.4))
    W = H = 128
    ref = g.render(W, H, (0, 0, W, H))[..., :3]
    surf = skia.Surface(W, H)
    surf.getCanvas().drawPaint(skia.Paint(Shader=g.shader((0, 0, W, H))))
    got = surf.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)[..., :3] / 255.0
    assert np.abs(got - ref).mean() < 1.5 / 255


# ---------------------------------------------------------------- layers ------------
def test_premultiplied_readback():
    surf = skia.Surface(8, 8)
    surf.getCanvas().clear(skia.Color4f(1, 0, 0, 0.5))
    default = surf.makeImageSnapshot().toarray()  # BGRA, unpremultiplied (the pitfall)
    assert default[0, 0].tolist() == [0, 0, 255, 128]
    from luma_engine.layers import new_surface

    s2 = new_surface(8, 8)
    s2.getCanvas().clear(skia.Color4f(1, 0, 0, 0.5))
    arr = read_surface(s2)
    assert arr[0, 0] == pytest.approx([0.5, 0, 0, 0.5], abs=1e-3)  # premultiplied RGBA


def test_coverage_paint_is_unpremultiplied():
    p = coverage_paint(0.25)
    c = p.getColor4f()
    assert (c.fR, c.fG, c.fB, c.fA) == pytest.approx((1, 1, 1, 0.25))


def test_rolloff_identity_in_unit_range_and_bounded():
    rng = np.random.default_rng(0)
    x = rng.random((32, 32, 3)).astype(np.float32)
    assert np.array_equal(rolloff(x), x)
    hdr = x * 4
    y = rolloff(hdr)
    assert y.max() <= 1.0 + 1e-6 and y.min() >= 0


def test_hdr_light_survives_skia_plus_clamp():
    f = Frame(16, 16, background="#000000")
    with f.light_canvas() as c:
        c.drawRect(skia.Rect.MakeWH(16, 16), f.light_paint("#FFFFFF", 3.0))
        c.drawRect(skia.Rect.MakeWH(16, 16), f.light_paint("#FFFFFF", 2.5))
    lt = f.light_total()
    assert lt[8, 8, 0] == pytest.approx(5.5, rel=5e-3)  # > 1: HDR preserved via gain scaling
    assert LIGHT_GAIN >= 16


def test_frame_without_light_is_bit_exact():
    f = Frame(32, 16, background="#0B0F2A")
    out = f.resolve()
    expect = Color.of("#0B0F2A").rgb
    assert np.abs(out[..., :3] * 255 - expect * 255).max() < 0.06


def test_motion_blur_static_is_single_sample_and_moving_averages():
    def static(t):
        return np.full((4, 4, 3), 0.25, np.float32)

    img, n = motion_blur(static, 1.0, 60)
    assert n == 2 and np.array_equal(img, static(0))

    def moving(t):
        a = np.zeros((1, 64, 3), np.float32)
        a[0, int((t * 60 * 16) % 64)] = 1.0
        return a

    img, n = motion_blur(moving, 1.0, 60, 180, 16)
    assert n > 2 and img.sum() == pytest.approx(3.0, rel=0.05)  # energy conserved, spread over pixels
    assert (img[0, :, 0] > 0).sum() > 2


# ---------------------------------------------------------------- motion ------------
@pytest.mark.parametrize("freq,zeta,v0", [(2.0, 0.3, 0.0), (1.5, 0.7, 2.0), (3.0, 0.1, -1.0)])
def test_spring_closed_form_landmarks(freq, zeta, v0):
    s = Spring(freq, zeta, v0=v0)
    t1 = s.first_crossing_time()
    assert s.value(t1) == pytest.approx(1.0, abs=1e-9)
    ts = np.linspace(1e-4, t1, 2000)[:-1]
    assert np.all(s.value(ts) < 1.0)  # truly the first crossing
    tp = s.peak_time()
    assert abs(s.velocity(tp)) < 1e-8
    assert s.overshoot() == pytest.approx(s.value(tp) - 1)
    # physics check: numerical ODE matches the closed form
    dt, x, v = 1e-5, 0.0, v0
    w, z = s.w0, s.zeta
    for _ in range(int(0.5 / dt)):
        a = -2 * z * w * v - w * w * (x - 1)
        v += a * dt
        x += v * dt
    assert x == pytest.approx(s.value(0.5), abs=2e-3)


def test_spring_textbook_peak_and_settle_snap():
    s = Spring(2.0, 0.4)
    wd = s.w0 * math.sqrt(1 - 0.16)
    assert s.peak_time() == pytest.approx(math.pi / wd)
    assert s.overshoot() == pytest.approx(math.exp(-0.4 * math.pi / math.sqrt(1 - 0.16)))
    ts = s.settle_time(1e-4)
    assert s.at(ts + 0.01, 0, 0, 10) == 10  # snapped exactly
    crit = Spring(2.0, 1.0)
    assert crit.first_crossing_time() is None and crit.overshoot() == 0


def test_easings_endpoints_and_bezier():
    for name, f in EASINGS.items():
        assert f(0.0) == pytest.approx(0.0, abs=1e-6), name
        assert f(1.0) == pytest.approx(1.0, abs=1e-6), name
    lin = cubic_bezier(0, 0, 1, 1)
    assert lin(0.37) == pytest.approx(0.37, abs=1e-6)


def test_channel_and_timeline():
    ch = Channel([(0, 0.0), (1, 10.0), (2, 0.0)])
    assert ch(1) == 10 and ch(0.5) > 0 and ch(3) == 0
    vec = Channel([(0, [0, 0]), (1, [10, 20], None)])
    assert vec(1).tolist() == [10, 20]
    tl = Timeline(5, 60)
    tl.add("seat", 1.234, "seat", snap=True)
    assert tl.t("seat") * 60 == pytest.approx(round(1.234 * 60))
    assert Timeline.from_dict(tl.to_dict()).t("seat") == tl.t("seat")
    assert stagger(3, 1.0, 0.1) == pytest.approx([1.0, 1.1, 1.2])


# ---------------------------------------------------------------- text --------------
def test_text_shaping_kerning_and_tracking():
    a = shape_text("AVATAR", "Inter", 600, 100)
    nk = shape_text("AVATAR", "Inter", 600, 100, features={"kern": False})
    assert a.advance < nk.advance - 5  # kerning pairs pull AV/VA/TA together
    tr = shape_text("AVATAR", "Inter", 600, 100, tracking=100)
    assert tr.advance == pytest.approx(a.advance + 5 * 10, abs=1e-6)  # 100/1000 em between 6 glyphs
    x0, y0, x1, y1 = a.bounds()
    assert y1 <= 1.0 and -y0 == pytest.approx(a.cap_height, rel=0.03)  # caps sit on the baseline
    assert not a.glyphs[0].path.isEmpty()


# ---------------------------------------------------------------- camera ------------
def test_camera_identity_on_z0_and_perspective():
    cam = Camera(1920, 1080)
    p, z = cam.project([[100, 200, 0], [960, 540, 0]])
    assert p == pytest.approx(np.array([[100, 200], [960, 540]]), abs=1e-6)
    far, _ = cam.project([[1960, 540, 1000]])
    assert far[0, 0] < 1960  # further away → closer to the centre
    H = cam.layer_matrix(200, 100, center=(960, 540, 0))
    assert apply_h(H, [[100, 50]]) == pytest.approx(np.array([[960, 540]]), abs=1e-6)


def test_light_vector_sign_convention():
    facing_camera = (0, 0, -1)  # camera looks along +z
    key_in_front = to_light_vector((0, 0, -500), (0, 0, 0))
    assert lambert(facing_camera, key_in_front) == pytest.approx(1.0)
    key_behind = to_light_vector((0, 0, 500), (0, 0, 0))
    assert lambert(facing_camera, key_behind) == 0.0
