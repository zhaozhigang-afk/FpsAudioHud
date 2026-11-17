# -*- coding: utf-8 -*-
"""
HUD 音浪 / 指针自测（不需要声卡、不需要开窗口）

覆盖：
    · 12 段边缘音浪的角度映射与布局
    · 边缘波形条的柱数、朝向、响度缩放与确定性
    · 中心直线箭头几何（方向、起止点、箭头宽度）
    · 专注提示 / 多方向提示的选择逻辑
    · 多声源聚类（DirectionEstimator.components）与多光点跟踪

运行：python test_hud.py
"""

import math
import unittest

import numpy as np

from audio_direction import (DirectionEstimator, SourceTracker,
                             sector_name, wrap_angle)
from hud_geometry import (HudConfig, HudSource, SEGMENT_KEYS, HUD_PRESETS,
                          arrow_geometry, build_frame, build_segments, dial_ticks,
                          level_color, pick_focus, pick_multi, preset_config,
                          segment_weight, segment_weights, waveform_bars)

FS = 48000
N = 1024


def sine_block(channels, active, amplitude=0.5, freq=1000.0):
    """生成一个只有指定声道有 1kHz 正弦的音频块（和 test_direction.py 同一套）。"""
    t = np.arange(N) / float(FS)
    data = np.zeros((N, channels), dtype=np.float32)
    tone = (amplitude * np.sin(2.0 * math.pi * freq * t)).astype(np.float32)
    for channel in active:
        data[:, channel] = tone
    return data


def source(angle_deg, intensity=0.6, sound_type="footstep", track_id=1):
    return HudSource(angle=math.radians(angle_deg), level=intensity,
                     intensity=intensity, fade=1.0, sound_type=sound_type,
                     track_id=track_id)


def event(angle_deg, intensity=0.6, age=0.0, now=100.0, sound_type="footstep",
          track_id=1):
    return {"id": track_id, "angle": math.radians(angle_deg),
            "intensity": intensity, "type": sound_type,
            "timestamp": now - age, "confidence": 1.0, "channels": 8}


# ============================ 边缘音浪 ============================
class TestSegments(unittest.TestCase):
    def test_front_lights_top_only(self):
        weights = segment_weights(0.0)
        self.assertAlmostEqual(weights["top"], 1.0, places=6)
        self.assertAlmostEqual(weights["bottom"], 0.0, places=6)
        self.assertAlmostEqual(weights["right"], 0.0, places=6)
        self.assertAlmostEqual(weights["top_left_h"], 0.0, places=6)

    def test_diagonal_lights_corner_pair(self):
        weights = segment_weights(math.radians(45))
        self.assertAlmostEqual(weights["top_right_h"], 1.0, places=6)
        self.assertAlmostEqual(weights["top_right_v"], 1.0, places=6)
        self.assertAlmostEqual(weights["top"], 0.0, places=6)
        self.assertAlmostEqual(weights["right"], 0.0, places=6)

    def test_back_and_left(self):
        self.assertAlmostEqual(segment_weights(math.pi)["bottom"], 1.0, places=6)
        self.assertAlmostEqual(segment_weights(math.radians(-90))["left"], 1.0, places=6)
        self.assertAlmostEqual(segment_weights(math.radians(-135))["bottom_left_h"], 1.0, places=6)

    def test_symmetric_and_smooth(self):
        # 左右对称
        for key, mirror in (("top_left_h", "top_right_h"),
                            ("bottom_left_v", "bottom_right_v"),
                            ("left", "right")):
            self.assertAlmostEqual(segment_weight(key, math.radians(-40)),
                                   segment_weight(mirror, math.radians(40)), places=9)
        # 22.5° 时两边各亮一半，且不是台阶
        mid = segment_weights(math.radians(22.5))
        self.assertAlmostEqual(mid["top"], 0.5, delta=0.02)
        self.assertAlmostEqual(mid["top_right_h"], 0.5, delta=0.02)
        # 角度连续：1° 的差不会造成 0.2 以上的跳变
        a = segment_weight("top", math.radians(10))
        b = segment_weight("top", math.radians(11))
        self.assertLess(abs(a - b), 0.2)

    def test_layout_covers_all_edges(self):
        segments = build_segments(1920, 1080, HudConfig())
        self.assertEqual(len(segments), 12)
        self.assertEqual(len({seg.key for seg in segments}), 12)
        self.assertEqual(set(seg.key for seg in segments), set(SEGMENT_KEYS))
        for seg in segments:
            self.assertGreaterEqual(seg.x, -0.001)
            self.assertGreaterEqual(seg.y, -0.001)
            self.assertLessEqual(seg.x + seg.width, 1920.001)
            self.assertLessEqual(seg.y + seg.height, 1080.001)
            self.assertGreater(seg.width, 0.0)
            self.assertGreater(seg.height, 0.0)

    def test_layout_scale_changes_size(self):
        small = {seg.key: seg for seg in build_segments(1920, 1080, HudConfig(size=0.8))}
        big = {seg.key: seg for seg in build_segments(1920, 1080, HudConfig(size=1.2))}
        self.assertLess(small["top"].width, big["top"].width)
        self.assertLess(small["left"].height, big["left"].height)


# ============================ 中心指针 ============================
class TestArrow(unittest.TestCase):
    CX, CY, R = 400.0, 300.0, 120.0

    def test_directions(self):
        right = arrow_geometry(math.radians(90), self.CX, self.CY, self.R, 1.0)
        self.assertGreater(right["tip"][0], self.CX)
        self.assertAlmostEqual(right["tip"][1], self.CY, places=6)

        front = arrow_geometry(0.0, self.CX, self.CY, self.R, 1.0)
        self.assertAlmostEqual(front["tip"][0], self.CX, places=6)
        self.assertLess(front["tip"][1], self.CY)

        back = arrow_geometry(math.pi, self.CX, self.CY, self.R, 1.0)
        self.assertGreater(back["tip"][1], self.CY)

        left = arrow_geometry(math.radians(-90), self.CX, self.CY, self.R, 1.0)
        self.assertLess(left["tip"][0], self.CX)

    def test_length_follows_strength(self):
        weak = arrow_geometry(0.0, self.CX, self.CY, self.R, 0.1)
        strong = arrow_geometry(0.0, self.CX, self.CY, self.R, 1.0)
        self.assertLess(weak["length"], strong["length"])
        self.assertLessEqual(strong["length"], self.R * 1.02)
        self.assertGreaterEqual(weak["length"], self.R * 0.5)

    def test_start_and_head_order(self):
        """箭杆从圆心外一点开始，箭头底边在箭杆末端和尖端之间。"""
        geo = arrow_geometry(0.0, self.CX, self.CY, self.R, 1.0)
        start_d = self.CY - geo["start"][1]
        head_d = self.CY - geo["head_base"][1]
        tip_d = self.CY - geo["tip"][1]
        self.assertGreater(start_d, 0.0)
        self.assertGreater(head_d, start_d)
        self.assertGreater(tip_d, head_d)
        self.assertAlmostEqual(start_d, self.R * 0.20, delta=1.0)

    def test_head_width(self):
        geo = arrow_geometry(0.0, self.CX, self.CY, self.R, 1.0, head_width_ratio=0.10)
        width = abs(geo["head_left"][0] - geo["head_right"][0])
        self.assertAlmostEqual(width, self.R * 0.10 * 2.0, places=6)
        # 尖端在箭头中轴线上
        self.assertAlmostEqual(geo["tip"][0], self.CX, places=6)

    def test_scale_shortens_arrow(self):
        full = arrow_geometry(0.0, self.CX, self.CY, self.R, 1.0, length_scale=1.0)
        short = arrow_geometry(0.0, self.CX, self.CY, self.R, 1.0, length_scale=0.6)
        self.assertLess(short["length"], full["length"])

    def test_ticks(self):
        ticks = dial_ticks(self.CX, self.CY, self.R, 8)
        self.assertEqual(len(ticks), 8)
        # 第一个刻度在正上方（0=正前）
        x0, y0, x1, y1 = ticks[0]
        self.assertAlmostEqual(x0, self.CX, places=6)
        self.assertLess(y0, self.CY)
        self.assertLess(y1, y0)

    def test_hud_has_no_text_fields(self):
        """HUD 上不显示文字：数据结构和配置里都不该再有方位/类型文案。"""
        self.assertFalse(hasattr(HudConfig(), "show_labels"))
        hud_source = HudSource(angle=0.0, level=1.0, intensity=1.0, fade=1.0)
        for field in ("label", "heading", "type_label"):
            self.assertFalse(hasattr(hud_source, field), field)


class TestWaveform(unittest.TestCase):
    """边缘波形条：柱数、朝向、响度缩放。"""

    def _segments(self):
        return {seg.key: seg for seg in build_segments(1920, 1080, HudConfig())}

    def test_bar_count_and_bounds_top(self):
        top = self._segments()["top"]
        bars = waveform_bars(top, level=1.0, weight=1.0, phase=0.25)
        self.assertGreaterEqual(len(bars), 10)
        for (x, y, w, h, amp) in bars:
            self.assertGreaterEqual(x, top.x - 0.001)
            self.assertLessEqual(x + w, top.x + top.width + 0.001)
            self.assertAlmostEqual(y, top.y, places=6)     # 从屏幕顶边往下长
            self.assertGreater(h, 0.0)
            self.assertLessEqual(h, top.height * 1.9 + 0.001)
            self.assertGreaterEqual(amp, 0.0)
            self.assertLessEqual(amp, 1.0)

    def test_bottom_and_side_anchor(self):
        segments = self._segments()
        bottom = segments["bottom"]
        for (x, y, w, h, _amp) in waveform_bars(bottom, 1.0, 1.0, 0.4):
            self.assertAlmostEqual(y + h, bottom.y + bottom.height, places=6)  # 贴着底边向上长

        left = segments["left"]
        for (x, y, w, h, _amp) in waveform_bars(left, 1.0, 1.0, 0.4):
            self.assertAlmostEqual(x, left.x, places=6)          # 贴着左边往右长
            self.assertLessEqual(w, left.width * 1.9 + 0.001)    # 长度最多 1.9 倍厚度
            self.assertLessEqual(h, left.width)                  # 柱宽只是厚度的一小部分
            self.assertGreater(w, 0.0)

        right = segments["right"]
        for (x, y, w, h, _amp) in waveform_bars(right, 1.0, 1.0, 0.4):
            self.assertAlmostEqual(x + w, right.x + right.width, places=6)

    def test_louder_is_taller(self):
        top = self._segments()["top"]
        quiet = waveform_bars(top, level=0.15, weight=1.0, phase=0.3)
        loud = waveform_bars(top, level=1.0, weight=1.0, phase=0.3)
        quiet_mean = sum(bar[3] for bar in quiet) / len(quiet)
        loud_mean = sum(bar[3] for bar in loud) / len(loud)
        self.assertLess(quiet_mean, loud_mean)

    def test_silent_weight_leaves_short_ticks(self):
        top = self._segments()["top"]
        bars = waveform_bars(top, level=1.0, weight=0.0, phase=0.3)
        tallest = max(bar[3] for bar in bars)
        self.assertLess(tallest, top.height * 0.25)

    def test_deterministic_and_animated(self):
        top = self._segments()["top"]
        first = waveform_bars(top, 0.8, 1.0, 0.2)
        again = waveform_bars(top, 0.8, 1.0, 0.2)
        self.assertEqual(first, again)
        moved = waveform_bars(top, 0.8, 1.0, 0.7)
        self.assertNotEqual([bar[3] for bar in first], [bar[3] for bar in moved])


# ============================ 提示逻辑 ============================
class TestSelection(unittest.TestCase):
    def test_focus_keeps_loudest(self):
        sources = [source(-45, 0.3, track_id=1), source(135, 0.9, track_id=2),
                   source(90, 0.5, track_id=3)]
        picked = pick_focus(sources)
        self.assertEqual(len(picked), 1)
        self.assertAlmostEqual(picked[0].intensity, 0.9)
        self.assertEqual(picked[0].track_id, 2)

    def test_multi_limits(self):
        sources = [source(angle, 0.2 + index * 0.1, track_id=index)
                   for index, angle in enumerate((-135, -90, -45, 45, 90))]
        picked = pick_multi(sources, limit=3)
        self.assertEqual(len(picked), 3)
        # 按强度从高到低
        self.assertGreater(picked[0].intensity, picked[1].intensity)

    def test_multi_merges_close_angles(self):
        sources = [source(0, 0.7), source(10, 0.65), source(120, 0.5)]
        picked = pick_multi(sources, limit=4, min_sep_deg=25.0)
        self.assertEqual(len(picked), 2)
        self.assertAlmostEqual(picked[0].intensity, 0.7)

    def test_build_frame_filters_expired(self):
        now = 100.0
        events = [event(0, age=0.2, now=now), event(90, age=3.0, now=now)]
        frame = build_frame(events, now, lifetime=1.4)
        self.assertEqual(len(frame.sources), 1)
        self.assertEqual(sector_name(frame.sources[0].angle), "中前")

    def test_build_frame_focus_vs_multi(self):
        now = 100.0
        events = [event(-135, 0.4, now=now, track_id=1),
                  event(90, 0.8, now=now, track_id=2),
                  event(45, 0.6, now=now, track_id=3)]

        cfg = HudConfig(max_sources=4)
        focus = build_frame(events, now, lifetime=1.4, config=cfg, hint_logic="focus")
        multi = build_frame(events, now, lifetime=1.4, config=cfg, hint_logic="multi")
        self.assertEqual(len(focus.sources), 1)
        self.assertEqual(len(multi.sources), 3)
        self.assertAlmostEqual(focus.sources[0].intensity, 0.8)

    def test_segments_follow_focus_direction(self):
        now = 100.0
        frame = build_frame([event(0, 1.0, now=now)], now, lifetime=1.4)
        self.assertIn("top", frame.segments)
        self.assertNotIn("bottom", frame.segments)
        self.assertNotIn("left", frame.segments)
        self.assertGreater(frame.segments["top"].weight, 0.9)

    def test_frame_fades_and_phase(self):
        now = 100.0
        fresh = build_frame([event(0, 1.0, age=0.0, now=now)], now, lifetime=1.0)
        old = build_frame([event(0, 1.0, age=0.8, now=now)], now, lifetime=1.0)
        self.assertGreater(fresh.sources[0].level, old.sources[0].level)
        self.assertGreaterEqual(fresh.phase, 0.0)
        self.assertLess(fresh.phase, 1.0)

    def test_global_alpha_default(self):
        """整体透明是独立参数：默认 1.0，克隆能单独改，不影响元素不透明度。"""
        cfg = HudConfig()
        self.assertAlmostEqual(cfg.global_alpha, 1.0)
        dim = cfg.clone(global_alpha=0.5)
        self.assertAlmostEqual(dim.global_alpha, 0.5)
        self.assertAlmostEqual(dim.opacity, cfg.opacity)

    def test_level_color_monotonic(self):
        low = level_color(0.0)
        high = level_color(1.0)
        self.assertGreater(high[0], low[0])     # 越响越红
        self.assertLess(high[1], low[1])

    def test_presets_get_bigger(self):
        light = preset_config("轻提示")
        heavy = preset_config("明显")
        self.assertLess(light.size, heavy.size)
        self.assertLess(light.opacity, heavy.opacity)
        self.assertGreaterEqual(heavy.max_sources, light.max_sources)
        # 预设是副本，改它不会污染预设表
        light.size = 9.9
        self.assertNotEqual(HUD_PRESETS["轻提示"].size, 9.9)


# ============================ 多声源 ============================
class TestMultiSource(unittest.TestCase):
    def test_components_single_channel(self):
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        result = estimator.process(sine_block(8, [4]))
        self.assertIsNotNone(result)
        self.assertEqual(len(result["components"]), 1)
        self.assertAlmostEqual(math.degrees(result["components"][0]["angle"]), -150.0,
                               delta=1.0)

    def test_components_split_left_behind_and_right_front(self):
        """左后 + 右前同时响：必须拆成两个分量，而不是平均到中间。"""
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        result = estimator.process(sine_block(8, [4, 1]))
        self.assertIsNotNone(result)
        angles = sorted(math.degrees(comp["angle"]) for comp in result["components"])
        self.assertEqual(len(angles), 2, angles)
        self.assertLess(angles[0], -100.0)          # 左后（BL = -150）
        self.assertGreater(angles[1], 10.0)         # 右前（FR = +30）
        shares = [comp["share"] for comp in result["components"]]
        self.assertAlmostEqual(sum(shares), 1.0, delta=0.02)

    def test_front_pair_is_one_source(self):
        """前左+前右一起响是"正前方一个声源"，不能拆成两个。"""
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(8)
        result = estimator.process(sine_block(8, [0, 1]))
        self.assertIsNotNone(result)
        self.assertEqual(len(result["components"]), 1)
        self.assertAlmostEqual(math.degrees(result["components"][0]["angle"]), 0.0,
                               delta=1.0)

    def test_stereo_has_single_component(self):
        estimator = DirectionEstimator(FS, N)
        estimator.set_channels(2)
        result = estimator.process(sine_block(2, [0]))
        self.assertEqual(len(result["components"]), 1)
        self.assertAlmostEqual(result["components"][0]["share"], 1.0)

    def test_tracker_tracks_two_sources(self):
        tracker = SourceTracker(hold=0.2)
        left = {"angle": math.radians(-150), "confidence": 1.0, "intensity": 0.8,
                "rms": 0.02, "peak_ratio": 1.5, "centroid": 900.0}
        right = {"angle": math.radians(30), "confidence": 1.0, "intensity": 0.5,
                 "rms": 0.01, "peak_ratio": 1.4, "centroid": 800.0}
        first = tracker.update_multi([left, right], 1.0)
        self.assertEqual(len(first), 2)
        ids = sorted(patch["id"] for patch in first)
        second = tracker.update_multi([left, right], 1.02)
        self.assertEqual(sorted(patch["id"] for patch in second), ids)
        angles = sorted(math.degrees(patch["angle"]) for patch in second)
        self.assertLess(angles[0], -100.0)
        self.assertGreater(angles[1], 10.0)

    def test_tracker_multi_and_single_are_compatible(self):
        tracker = SourceTracker(hold=0.2)
        first = tracker.update({"angle": 0.0, "confidence": 1.0, "intensity": 0.5,
                                "rms": 0.02, "peak_ratio": 1.5, "centroid": 900.0}, 1.0)
        self.assertEqual(len(first), 1)
        self.assertEqual(tracker.update(None, 1.5), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
