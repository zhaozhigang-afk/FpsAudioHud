# -*- coding: utf-8 -*-
"""
方向映射自测（不需要声卡，纯数学验证）

运行：python test_direction.py
"""

import math
import unittest

import numpy as np

from audio_direction import (DirectionEstimator, SourceTracker, classify_sound,
                             effective_band, effective_snr_threshold, intensity_from_snr,
                             pan_angle, sector_name, wrap_angle,
                             EFF_THRESHOLD_MAX, EFF_THRESHOLD_MIN)


FS = 48000
N = 1024


def rumble_block(channels, rumble=0.06, step=0.05, step_ch=4):
    """全声道 80Hz 轰鸣 + 单个声道 1.2kHz 脚步（模拟载具低频压住远处脚步）。"""
    t = np.arange(N) / float(FS)
    data = np.zeros((N, channels), dtype=np.float32)
    for channel in range(channels):
        data[:, channel] = rumble * np.sin(2.0 * math.pi * 80.0 * t + channel * 0.3)
    data[:, step_ch] += step * np.sin(2.0 * math.pi * 1200.0 * t) * np.hanning(N)
    return data


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

    def test_effective_threshold(self):
        # 中性灵敏度 = 阈值原样
        self.assertAlmostEqual(effective_snr_threshold(1.8, 1.0), 1.8)
        # 灵敏度越高，等效阈值越低（= 更容易抓到远处）
        self.assertLess(effective_snr_threshold(1.8, 2.0),
                        effective_snr_threshold(1.8, 1.0))
        self.assertLess(effective_snr_threshold(1.8, 1.0),
                        effective_snr_threshold(1.8, 0.5))
        # 钳位：不能低到常亮，也不能高到什么都听不见
        self.assertAlmostEqual(effective_snr_threshold(0.2, 2.5), EFF_THRESHOLD_MIN)
        self.assertAlmostEqual(effective_snr_threshold(9.0, 0.5), EFF_THRESHOLD_MAX)
        # 非法输入不炸
        self.assertAlmostEqual(effective_snr_threshold(1.8, 0), 1.8)
        self.assertAlmostEqual(effective_snr_threshold(1.8, None), 1.8)

    def test_effective_band(self):
        self.assertEqual(effective_band(150, 8000), (150.0, 8000.0))
        self.assertEqual(effective_band(-10, 9000), (20.0, 9000.0))
        self.assertEqual(effective_band(100, 25000), (100.0, 20000.0))
        lo, hi = effective_band(19990, 20000)     # 间距不足，得往下让
        self.assertGreaterEqual(hi - lo, 50.0)
        self.assertEqual(hi, 20000.0)
        lo, hi = effective_band(8000, 8030)       # 反着给也要合法
        self.assertGreaterEqual(hi - lo, 50.0)
        self.assertEqual(effective_band(None, None), (150.0, 8000.0))

    def test_eq_presets_are_valid(self):
        from fps_audio_radar import EQ_CURVE_PRESETS, eq_bin_count
        self.assertGreaterEqual(len(EQ_CURVE_PRESETS), 6)
        for name, segments in EQ_CURVE_PRESETS.items():
            self.assertTrue(segments, name)
            for seg in segments:
                self.assertLess(seg["lo"], seg["hi"], name)
                self.assertGreaterEqual(seg["lo"], 150.0, name)
                self.assertLessEqual(seg["hi"], 8000.0, name)
                self.assertGreaterEqual(eq_bin_count(seg["lo"], seg["hi"]), 2, name)

    def test_eq_helpers(self):
        from fps_audio_radar import sanitize_eq, eq_key, eq_bin_count
        segments = sanitize_eq([
            {"lo": 2000, "hi": 1000, "gain_db": 99},      # 反的 + 增益超限
            {"lo": 100, "hi": 300, "gain_db": -40},
            "garbage",                                     # 非 dict 直接丢
        ])
        self.assertEqual(len(segments), 2)
        self.assertLessEqual(max(s["gain_db"] for s in segments), 24.0)
        self.assertGreaterEqual(min(s["gain_db"] for s in segments), -24.0)
        # bin 计数：1024 点 = 46.9Hz/bin
        self.assertEqual(eq_bin_count(150, 250), 2)
        self.assertEqual(eq_bin_count(1200, 2500), 28)
        self.assertEqual(eq_bin_count(20, 40), 0)
        self.assertEqual(eq_key([{"lo": 150, "hi": 250, "gain_db": 0}]),
                         ((150.0, 250.0, 0.0),))

    def test_presets_keep_effective_threshold(self):
        from fps_audio_radar import PRESETS
        for name, expected in (("低", 2.5), ("中", 1.8), ("高", 1.25)):
            cfg = PRESETS[name]
            self.assertAlmostEqual(
                effective_snr_threshold(cfg["snr_threshold"], cfg["sensitivity"]),
                expected, places=2, msg=name)
        # 频带跟档位走：低档全带，中/高档抗轰鸣
        self.assertEqual(PRESETS["低"]["band_low"], 60.0)
        self.assertEqual(PRESETS["低"]["band_high"], 12000.0)
        for name in ("中", "高"):
            self.assertEqual(PRESETS[name]["band_low"], 150.0, name)
            self.assertEqual(PRESETS[name]["band_high"], 8000.0, name)

    def _burst_hits(self, est, blocks=80, period=19, burst=2):
        """周期性脚步瞬态（连续音会被自适应底噪当成环境音，必须用瞬态测）。"""
        hits = 0
        for i in range(blocks):
            step_on = (i % period) < burst
            source = est.process(rumble_block(8, step=0.05 if step_on else 0.0),
                                 noise_gate=0.008, snr_threshold=1.8)
            if i >= 20 and source is not None:
                hits += 1
        return hits

    def test_band_filters_rumble(self):
        """默认 150~8000 能避开 80Hz 轰鸣；切回全带就被轰鸣压死。"""
        est = DirectionEstimator(FS, N)
        self.assertAlmostEqual(est.band[0], 150.0)
        self.assertAlmostEqual(est.band[1], 8000.0)
        self.assertGreater(self._burst_hits(est), 0, "窄带下脚步应该检得出来")

        est.set_band(60, 12000)
        self.assertEqual(est.band, (60.0, 12000.0))
        self.assertEqual(self._burst_hits(est), 0,
                         "全带下 80Hz 轰鸣应该把脚步压到阈值以下")

        est.set_band(150, 8000)
        self.assertGreater(self._burst_hits(est), 0, "切回窄带应该恢复检测")

    def test_band_rejects_bad_values(self):
        est = DirectionEstimator(FS, N)
        est.set_band(5000, 100)      # 反了
        est.set_band(-1, 8000)       # 非法
        self.assertEqual(est.band, (150.0, 8000.0))

    def test_no_startup_false_positive(self):
        """开机能马上用：纯环境音的前 3 秒不应该闪出一堆假方向。"""
        rng = np.random.default_rng(3)
        est = DirectionEstimator(FS, N)
        hits = 0
        for _ in range(140):
            block = rng.normal(0, 0.002, (N, 8))
            if est.process(block, noise_gate=0.008,
                           snr_threshold=1.25, direction_gain=1.0) is not None:
                hits += 1
        self.assertEqual(hits, 0, "启动阶段误报 %d 块" % hits)

    def test_loud_first_block_detected(self):
        """但第一块的枪声/脚步不能被丢：预热底噪不能拿当前块自己的电平。"""
        est = DirectionEstimator(FS, N)
        source = est.process(sine_block(8, [4], amplitude=0.5),
                             noise_gate=0.008, snr_threshold=1.25)
        self.assertIsNotNone(source)

    def test_classify(self):
        self.assertEqual(classify_sound(0.5, 5.0, 3000.0, 2), "gunshot")
        self.assertEqual(classify_sound(0.2, 3.0, 300.0, 12), "explosion")
        self.assertEqual(classify_sound(0.02, 2.5, 900.0, 3), "footstep")
        self.assertEqual(classify_sound(0.01, 1.2, 1200.0, 10), "door")


if __name__ == "__main__":
    unittest.main(verbosity=2)
