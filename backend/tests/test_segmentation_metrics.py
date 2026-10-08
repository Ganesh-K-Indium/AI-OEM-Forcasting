import numpy as np
import pytest

from app.ml import metrics as M
from app.ml.segmentation import adi_cv2, classify, seasonal_strength


def test_adi_cv2_known_values():
    y = np.array([0, 5, 0, 0, 5, 0, 5, 0, 0, 5], float)  # 4 non-zero in 10 -> ADI 2.5, constant size -> CV2 0
    adi, cv2 = adi_cv2(y)
    assert adi == pytest.approx(2.5) and cv2 == pytest.approx(0.0)
    adi, cv2 = adi_cv2(np.array([10, 12, 8, 11, 9, 10], float))
    assert adi == 1.0 and cv2 < 0.05


def test_classification_quadrants():
    rng = np.random.default_rng(1)
    smooth = 100 + rng.normal(0, 5, 36)
    erratic = np.abs(rng.lognormal(3, 1.2, 36)) + 1
    intermittent = np.where(rng.random(36) < 0.4, 20 + rng.normal(0, 1, 36), 0.0)
    lumpy = np.where(rng.random(36) < 0.4, rng.lognormal(3, 1.3, 36), 0.0)
    t = np.arange(48)
    seasonal = 100 + 30 * np.sin(2 * np.pi * t / 12) + rng.normal(0, 3, 48)
    assert classify(smooth, None).segment == "smooth"
    assert classify(erratic, None).segment == "erratic"
    assert classify(intermittent, None).segment == "intermittent"
    assert classify(lumpy, None).segment == "lumpy"
    assert classify(seasonal, None).segment == "seasonal"
    assert seasonal_strength(seasonal) > 0.8 and seasonal_strength(smooth) < seasonal_strength(seasonal) - 0.2


def test_metrics_and_bias_sign():
    a, f = np.array([100.0, 100.0]), np.array([90.0, 80.0])
    assert M.wmape(a, f) == pytest.approx(0.15)
    assert M.bias(a, f) == pytest.approx(0.15)  # positive = under-forecast
    assert M.bias(a, a + 10) < 0
    assert M.mae(a, f) == 15
    assert M.pinball_loss([10], [8], 0.9) == pytest.approx(0.9 * 2)
    assert M.pinball_loss([10], [12], 0.9) == pytest.approx(0.1 * 2)
    assert M.interval_coverage([5, 15], [4, 4], [10, 10]) == 0.5
    assert M.mase_scale(np.arange(30, dtype=float)) == pytest.approx(12.0)  # seasonal naive on a ramp
