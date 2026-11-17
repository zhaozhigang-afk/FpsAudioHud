# -*- coding: utf-8 -*-
"""
方向映射自测（不需要声卡，纯数学验证）

运行：python test_direction.py
"""

import math
import unittest

import numpy as np

from audio_direction import (DirectionEstimator, SourceTracker, classify_sound,
                             intensity_from_snr, pan_angle, sector_name,
                             wrap_angle)


FS = 48000
N = 1024


def sine_block(channels, active, amplitude=0.5, freq=1000.0):
    """生成一个只有指定声道有 1kHz 正弦的音频块。"""
    t = np.arange(N) / float(FS)
    data = np.zeros((N, channels), dtype=np.float32)
    tone = (amplitude * np.sin(2.0 * math.pi * freq * t)).astype(np.float32)
    for channel in active:
        data[:, channel] = tone
    return data


class TestAngles(unittest.TestCase):
    def test_wrap(self):
        self.assertAlmostEqual(wrap_angle(0.0), 0.0)
        self.assertAlmostEqual(wrap_angle(2.0 * math.pi), 0.0)
        self.assertAlmostEqual(wrap_angle(-math.pi), math.pi)
        self.assertAlmostEqual(wrap_angle(3.0 * math.pi), math.pi)
        self.assertAlmostEqual(wrap_angle(math.radians(270)), math.radians(-90))

    def test_sector_names(self):
        expected = ["中前", "右前", "右侧", "右后", "正后", "左后", "左侧", "左前"]
        for name, deg in zip(expected, (0, 45, 90, 135, 180, -135, -90, -45)):
            self.assertEqual(sector_name(math.radians(deg)), name, deg)
        self.assertEqual(sector_name(math.radians(22)), "中前")
        self.assertEqual(sector_name(math.radians(23)), "右前")
        self.assertEqual(sector_name(math.radians(-179)), "正后")

    def test_pan_angle(self):
        self.assertAlmostEqual(pan_angle(1.0, 1.0), 0.0, places=6)
        self.assertAlmostEqual(pan_angle(1.0, 0.0), -math.pi / 2, places=6)
        self.assertAlmostEqual(pan_angle(0.0, 1.0), math.pi / 2, places=6)
        self.assertAlmostEqual(pan_angle(0.0, 0.0), 0.0, places=6)
        self.assertGreater(pan_angle(1.0, 0.5), -math.pi / 2)
        self.assertLess(pan_angle(1.0, 0.5), 0.0)
        # 单调：右声道越强，角度越靠右
        values = [pan_angle(1.0, r) for r in (0.1, 0.3, 1.0, 2.0, 5.0)]
        self.assertEqual(values, sorted(values))


class TestEstimator(unittest.TestCase):
    def test_silence_returns_none(self):
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        silent = np.zeros((N, 8), dtype=np.float32)
        for _ in range(5):
            self.assertIsNone(estimator.process(silent))

    def test_stereo_left_right_center(self):
        left = DirectionEstimator(FS, N)
        left.set_channels(2)
        source = left.process(sine_block(2, [0]))
        self.assertIsNotNone(source)
        self.assertAlmostEqual(math.degrees(source["angle"]), -90.0, delta=1.0)

        right = DirectionEstimator(FS, N)
        right.set_channels(2)
        source = right.process(sine_block(2, [1]))
        self.assertAlmostEqual(math.degrees(source["angle"]), 90.0, delta=1.0)

        center = DirectionEstimator(FS, N)
        center.set_channels(2)
        source = center.process(sine_block(2, [0, 1]))
        self.assertAlmostEqual(math.degrees(source["angle"]), 0.0, delta=1.0)

    def test_seven_point_one_channel_angles(self):
        expected = {0: -30.0, 1: 30.0, 2: 0.0, 4: -150.0, 5: 150.0, 6: -90.0, 7: 90.0}
        for channel, angle in expected.items():
            estimator = DirectionEstimator(FS, N)
            estimator.set_channels(8)
            source = estimator.process(sine_block(8, [channel]))
            self.assertIsNotNone(source, channel)
            self.assertAlmostEqual(math.degrees(source["angle"]), angle, delta=1.0,
                                   msg="channel %d" % channel)

    def test_five_point_one_surround_angles(self):
        for channel, angle in ((4, -110.0), (5, 110.0)):
            estimator = DirectionEstimator(FS, N)
            estimator.set_channels(6)
            source = estimator.process(sine_block(6, [channel]))
            self.assertIsNotNone(source)
            self.assertAlmostEqual(math.degrees(source["angle"]), angle, delta=1.0)

    def test_lfe_is_ignored(self):
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        self.assertIsNone(estimator.process(sine_block(8, [3])))

    def test_front_pair_center(self):
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        source = estimator.process(sine_block(8, [0, 1]))
        self.assertIsNotNone(source)
        self.assertAlmostEqual(math.degrees(source["angle"]), 0.0, delta=1.0)

    def test_left_channels_are_left(self):
        """FL / BL / SL 无论哪一个，都必须落在左边（sin 分量 < 0）。"""
        for channel in (0, 4, 6):
            estimator = DirectionEstimator(FS, N)
            estimator.set_channels(8)
            source = estimator.process(sine_block(8, [channel]))
            self.assertIsNotNone(source)
            self.assertLess(math.sin(source["angle"]), 0.0, channel)
        for channel in (1, 5, 7):
            estimator = DirectionEstimator(FS, N)
            estimator.set_channels(8)
            source = estimator.process(sine_block(8, [channel]))
            self.assertGreater(math.sin(source["angle"]), 0.0, channel)

    def test_direction_gain_does_not_flip_side(self):
        for gain in (0.8, 1.0, 1.2, 1.5, 2.0, 3.0):
            estimator = DirectionEstimator(FS, N)
            estimator.set_channels(8)
            source = estimator.process(sine_block(8, [4], amplitude=0.4),
                                       direction_gain=gain)
            self.assertIsNotNone(source, gain)
            self.assertLess(math.sin(source["angle"]), 0.0, gain)

    def test_tone_behind_is_rear(self):
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        source = estimator.process(sine_block(8, [4, 5]))
        self.assertIsNotNone(source)
        self.assertEqual(sector_name(source["angle"]), "正后")

    def test_stereo_pan_mode_on_multichannel(self):
        """多声道设备上只有前左/前右有声音时，立体声模式要把方向拉到两侧。"""
        block = sine_block(8, [0])
        block[:, 1] = block[:, 0] * 0.3

        auto = DirectionEstimator(FS, N)
        auto.set_channels(8)
        auto_angle = math.degrees(auto.process(block)["angle"])

        stereo = DirectionEstimator(FS, N)
        stereo.set_channels(8)
        stereo_angle = math.degrees(stereo.process(block, stereo_pan=True)["angle"])

        self.assertLess(auto_angle, 0.0)
        self.assertLess(stereo_angle, auto_angle - 15.0)
        self.assertLess(stereo_angle, -50.0)

    def test_stereo_pan_mode_ignores_center_only(self):
        """只有中置有声音时，前左/前右是空的，立体声模式不应给出假方向。"""
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        self.assertIsNone(estimator.process(sine_block(8, [2]), stereo_pan=True))


class TestTracker(unittest.TestCase):
    SOURCE = {"confidence": 1.0, "intensity": 0.5, "rms": 0.02,
              "peak_ratio": 1.5, "centroid": 900.0}

    def _source(self, angle):
        source = dict(self.SOURCE)
        source["angle"] = angle
        return source

    def test_same_source_keeps_id(self):
        tracker = SourceTracker(hold=0.12)
        first = tracker.update(self._source(0.0), 1.0)
        second = tracker.update(self._source(0.15), 1.02)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["id"], second[0]["id"])

    def test_jump_creates_new_track(self):
        tracker = SourceTracker(hold=0.12)
        first = tracker.update(self._source(0.0), 1.0)
        second = tracker.update(self._source(math.pi), 1.02)
        self.assertNotEqual(first[0]["id"], second[0]["id"])

    def test_expire_then_new_track(self):
        tracker = SourceTracker(hold=0.12)
        first = tracker.update(self._source(0.0), 1.0)
        self.assertEqual(tracker.update(None, 1.2), [])
        again = tracker.update(self._source(0.0), 1.4)
        self.assertNotEqual(first[0]["id"], again[0]["id"])

    def test_smoothing_moves_gradually(self):
        tracker = SourceTracker(hold=0.12, smooth=0.45)
        tracker.update(self._source(0.0), 1.0)
        patch = tracker.update(self._source(math.radians(30)), 1.02)[0]
        self.assertGreater(math.degrees(patch["angle"]), 0.0)
        self.assertLess(math.degrees(patch["angle"]), 30.0)


class TestMisc(unittest.TestCase):
    def test_intensity(self):
        self.assertAlmostEqual(intensity_from_snr(10.0, 2.0), 1.0)
        self.assertGreaterEqual(intensity_from_snr(0.0, 2.0), 0.12)
        low = intensity_from_snr(2.1, 2.0)
        high = intensity_from_snr(4.0, 2.0)
        self.assertLess(low, high)

    def test_classify(self):
        self.assertEqual(classify_sound(0.5, 5.0, 3000.0, 2), "gunshot")
        self.assertEqual(classify_sound(0.2, 3.0, 300.0, 12), "explosion")
        self.assertEqual(classify_sound(0.02, 2.5, 900.0, 3), "footstep")
        self.assertEqual(classify_sound(0.01, 1.2, 1200.0, 10), "door")


if __name__ == "__main__":
    unittest.main(verbosity=2)
