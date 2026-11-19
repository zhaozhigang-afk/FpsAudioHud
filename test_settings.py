# -*- coding: utf-8 -*-
"""
设置文件测试：保存 / 读取 / 旧版迁移 / 坏文件降级。
不需要声卡，也不需要真的开窗口（apply_saved_settings 只用 hud.config）。

运行：python test_settings.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fps_audio_radar as app
from hud_geometry import HudConfig


class FakeHud:
    """apply_saved_settings 只用到 hud.config，所以不需要真的 HUD 窗口。"""

    def __init__(self):
        self.config = HudConfig()


class TestSettingsFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fpshud-")
        self._dir = app.SETTINGS_DIR
        self._path = app.SETTINGS_PATH
        self._config = dict(app.CONFIG)
        app.SETTINGS_DIR = self.tmp
        app.SETTINGS_PATH = os.path.join(self.tmp, "settings.json")
        self.hud = FakeHud()

    def tearDown(self):
        app.SETTINGS_DIR = self._dir
        app.SETTINGS_PATH = self._path
        app.CONFIG.clear()
        app.CONFIG.update(self._config)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_load_audio_params(self):
        """音频参数改完存盘，下次启动要原样回来（用户最关心的那条）。"""
        app.CONFIG["sensitivity"] = 2.1
        app.CONFIG["snr_threshold"] = 1.4
        app.CONFIG["event_lifetime"] = 2.5
        self.assertTrue(app.save_settings(self.hud))

        with open(app.SETTINGS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["schema"], app.SETTINGS_SCHEMA)
        self.assertAlmostEqual(data["config"]["sensitivity"], 2.1)

        app.CONFIG["sensitivity"] = 1.0
        app.CONFIG["event_lifetime"] = 1.4
        app.apply_saved_settings(self.hud)
        self.assertAlmostEqual(app.CONFIG["sensitivity"], 2.1)
        self.assertAlmostEqual(app.CONFIG["snr_threshold"], 1.4)
        self.assertAlmostEqual(app.CONFIG["event_lifetime"], 2.5)

    def test_hud_params_roundtrip(self):
        self.hud.config = self.hud.config.clone(global_alpha=0.45, center_dot_size=6,
                                                max_sources=3)
        self.assertTrue(app.save_settings(self.hud))
        self.hud.config = HudConfig()
        app.apply_saved_settings(self.hud)
        self.assertAlmostEqual(self.hud.config.global_alpha, 0.45)
        self.assertEqual(int(self.hud.config.center_dot_size), 6)
        self.assertEqual(int(self.hud.config.max_sources), 3)

    def test_unknown_fields_are_ignored(self):
        with open(app.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"schema": app.SETTINGS_SCHEMA,
                       "config": {"whatever": 1, "sensitivity": 1.7},
                       "hud": {"nope": True, "global_alpha": 0.7}}, handle)
        app.apply_saved_settings(self.hud)
        self.assertAlmostEqual(app.CONFIG["sensitivity"], 1.7)
        self.assertAlmostEqual(self.hud.config.global_alpha, 0.7)
        self.assertNotIn("whatever", app.CONFIG)

    def test_old_schema_resets_audio_only(self):
        """旧 schema 的音频参数语义不同：回默认值，HUD 参数照旧保留。"""
        with open(app.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1,
                       "config": {"sensitivity": 2.4, "hud_color": "cyan"},
                       "hud": {"global_alpha": 0.6}}, handle)
        app.apply_saved_settings(self.hud)
        self.assertAlmostEqual(app.CONFIG["sensitivity"],
                               app.AUDIO_DEFAULTS["sensitivity"])
        self.assertEqual(app.CONFIG["hud_color"], "cyan")
        self.assertAlmostEqual(self.hud.config.global_alpha, 0.6)

    def test_corrupt_file_falls_back_and_backs_up(self):
        with open(app.SETTINGS_PATH, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        saved = app.apply_saved_settings(self.hud)      # 不抛异常
        self.assertIsInstance(saved, dict)
        self.assertTrue(os.path.exists(app.SETTINGS_PATH + ".broken"))
        self.assertAlmostEqual(app.CONFIG["sensitivity"],
                               app.AUDIO_DEFAULTS["sensitivity"])

    def test_missing_file_is_fine(self):
        self.assertFalse(os.path.exists(app.SETTINGS_PATH))
        saved = app.apply_saved_settings(self.hud)
        self.assertEqual(saved, {})
        self.assertAlmostEqual(app.CONFIG["sensitivity"],
                               app.AUDIO_DEFAULTS["sensitivity"])

    def test_atomic_write_leaves_no_tmp(self):
        app.save_settings(self.hud)
        self.assertTrue(os.path.exists(app.SETTINGS_PATH))
        self.assertFalse(os.path.exists(app.SETTINGS_PATH + ".tmp"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
