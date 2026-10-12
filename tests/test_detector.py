import numpy as np

from sentinel_guard.detector import MotionDetector


def scene(x=None, brightness=0, rng=None):
    f = np.full((240, 320, 3), 90 + brightness, np.uint8)
    f[100:140, 50:270] = 120 + brightness
    if rng is not None:
        f = np.clip(f.astype(int) + rng.integers(-3, 4, f.shape), 0, 255).astype(np.uint8)
    if x is not None:
        f[80:160, x:x + 50] = 250
    return f


def warm(det, rng, n=60):
    for _ in range(n):
        assert not det.process(scene(rng=rng))


def test_static_scene_with_noise_no_motion():
    rng = np.random.default_rng(0)
    det = MotionDetector(warmup_frames=30)
    for _ in range(150):
        assert not det.process(scene(rng=rng))


def test_moving_object_detected():
    rng = np.random.default_rng(1)
    det = MotionDetector(warmup_frames=30)
    warm(det, rng, 40)
    hits = [det.process(scene(x=20 + i * 12, rng=rng)) for i in range(15)]
    assert any(hits)


def test_brief_blip_ignored():
    rng = np.random.default_rng(2)
    det = MotionDetector(warmup_frames=30, min_consecutive_frames=3)
    warm(det, rng, 40)
    assert not det.process(scene(x=100, rng=rng))
    assert not det.process(scene(rng=rng))


def test_global_brightness_change_ignored():
    rng = np.random.default_rng(3)
    det = MotionDetector(warmup_frames=30)
    warm(det, rng, 40)
    assert not any(det.process(scene(brightness=70, rng=rng)) for _ in range(10))
