#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 FPS 听障辅助音频雷达 —— 全屏 HUD 版
 捕获系统输出音频 → 分析声音方位 → 贴边波形条 + 中心直线箭头
=====================================================================
 功能：
   - WASAPI loopback 捕获系统输出音频（pyaudiowpatch）
   - 立体声 / 4.0 / 5.1 / 6.1 / 7.1，多声道 360 度方向检测
   - 全屏 HUD：12 段贴边的「声音波形条」+ 屏幕正中的「直线箭头」
   - 两种提示逻辑：专注提示（只画最响的一个）/ 多方向提示（最多 4 个方向）
   - 准星位置固定一个红色中心点，方便对齐
   - HUD 铺满主屏、置顶、鼠标穿透；所有参数走系统托盘

 运行：python fps_audio_radar.py [--debug] [--no-audio] [--hud dual] [--hint multi]
=====================================================================
"""

import sys
import os
import math
import json
import time
import argparse
import threading
import platform
import ctypes
import dataclasses

import numpy as np

from PyQt5.QtWidgets import (QApplication, QWidget, QSystemTrayIcon,
                             QMenu, QAction, QDialog, QFormLayout,
                             QDoubleSpinBox, QPushButton, QHBoxLayout,
                             QVBoxLayout, QGridLayout, QLabel, QSpinBox,
                             QSlider, QCheckBox, QTabWidget, QComboBox)
from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import (QPainter, QColor, QPen, QPixmap, QIcon, QFont)

from audio_direction import (SECTOR_NAMES, SECTOR_ANGLES_DEG,
                             DirectionEstimator, SourceTracker,
                             channel_names, channel_layout, CHANNEL_LABELS,
                             intensity_from_snr, sector_name, clamp)
from hud_geometry import (HudConfig, HUD_MODES, MODE_NAMES, HUD_PRESET_NAMES,
                          HUD_COLOR_OPTIONS, HINT_NAMES, preset_config)
from hud_window import HudWindow

# ============================ Windows API ============================
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _GWL_STYLE = -16
    _WS_CAPTION = 0x00C00000

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def is_foreground_fullscreen(my_hwnd=0):
    """检测当前前台窗口是否为全屏（无边框且尺寸等于屏幕）。"""
    if not IS_WINDOWS:
        return False
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd or hwnd == my_hwnd:
            return False
        rect = _RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        screen_w = _user32.GetSystemMetrics(0)
        screen_h = _user32.GetSystemMetrics(1)
        if width >= screen_w and height >= screen_h:
            style = _user32.GetWindowLongW(hwnd, _GWL_STYLE)
            if not (style & _WS_CAPTION):
                return True
    except Exception:
        pass
    return False


# ============================ 全局配置 ============================
SAMPLE_RATE = 48000
BLOCK_SIZE = 1024

SENSITIVITY = 1.3
SNR_THRESHOLD = 1.8
DIRECTION_GAIN = 1.0
NOISE_GATE = 0.008
EVENT_LIFETIME = 1.4

DEBUG = False

CONFIG = {
    "sensitivity":      SENSITIVITY,
    "snr_threshold":    SNR_THRESHOLD,
    "direction_gain":   DIRECTION_GAIN,
    "noise_gate":       NOISE_GATE,
    "event_lifetime":   EVENT_LIFETIME,
    "input_mode":       "auto",     # auto=按声道布局 / stereo=只当前左前右是立体声
    # ---- 全屏 HUD（音浪 / 指针）----
    "hud_mode":         "dual",     # 默认就开双 HUD（小雷达已经去掉）
    "hint_logic":       "multi",    # focus=专注提示 / multi=多方向提示
    "hud_preset":       "平衡",     # 轻提示 / 平衡 / 明显
    "hud_color":        "red",      # red / type / orange / cyan / green / white
    "hud_max_sources":  4,          # 多方向提示最多同时显示几个
    "center_dot":       True,       # 准星位置的红色中心点
    "center_dot_size":  3,          # 中心点半径（像素）
}

# 低/中/高 一键预设（音频侧）
PRESETS = {
    "低": {"sensitivity": 0.8, "snr_threshold": 2.5, "direction_gain": 1.0, "noise_gate": 0.015},
    "中": {"sensitivity": 1.3, "snr_threshold": 1.8, "direction_gain": 1.0, "noise_gate": 0.008},
    "高": {"sensitivity": 2.0, "snr_threshold": 1.2, "direction_gain": 1.0, "noise_gate": 0.003},
}

LAYOUT_DESC = {1: "单声道", 2: "立体声", 3: "2.1", 4: "4.0", 5: "5.0",
               6: "5.1", 7: "6.1", 8: "7.1"}


# ============================ 音频分析器 ============================
class AudioAnalyzer:
    """WASAPI loopback 音频捕获 + 多声道方向分析"""

    def __init__(self, event_callback):
        self.callback       = event_callback
        self.running        = False
        self.stream         = None
        self.p              = None
        self.channels       = 2
        self.sample_rate    = SAMPLE_RATE
        self.device_name    = ""
        self.device_index   = None
        self.devices        = []
        self.error          = ""
        self.estimator      = DirectionEstimator(SAMPLE_RATE, BLOCK_SIZE)
        self.tracker        = SourceTracker()
        self.lock           = threading.Lock()
        self.last_event     = None
        self._debug_count   = 0
        self._debug_levels  = None
        self._callback_error = False
        self._recent        = np.zeros(0)

    # ---------- 启动 ----------
    def start(self, device_index=None):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.error = "未安装 pyaudiowpatch，请运行: pip install pyaudiowpatch"
            print("[错误] " + self.error)
            return False

        try:
            self.p = pyaudio.PyAudio()
        except Exception as exc:
            self.error = "PyAudio 初始化失败: %s" % exc
            print("[错误] " + self.error)
            return False

        # 列出所有 WASAPI 回环设备（托盘里可以手动切换）
        self.devices = []
        wasapi_info = None
        try:
            wasapi_info = self.p.get_host_api_info_by_type(pyaudio.paWASAPI)
            for loopback in self.p.get_loopback_device_info_generator():
                if loopback.get("hostApi") == wasapi_info["index"]:
                    self.devices.append((int(loopback["index"]),
                                         str(loopback.get("name", "?")),
                                         int(loopback.get("maxInputChannels", 2) or 2)))
        except Exception:
            pass

        # 候选设备顺序：
        #   手动指定 → 默认输出设备的回环 → 其余回环（声道多的优先）→ 默认输出设备本身
        # 机器上常同时存在 VB-Cable / HDMI / 耳机等多个回环设备，抓错设备听到的就是
        # "别的"声音，方向自然不对；默认回环打不开时也要能自动换一个能用的。
        candidates = []
        seen = set()

        def add_device(dev):
            if not dev:
                return
            try:
                index = int(dev.get("index", -1))
            except Exception:
                return
            if index < 0 or index in seen:
                return
            seen.add(index)
            candidates.append(dev)

        if device_index is not None:
            try:
                add_device(self.p.get_device_info_by_index(int(device_index)))
            except Exception:
                pass
        try:
            add_device(self.p.get_default_wasapi_loopback())
        except Exception:
            pass
        for index, _name, _ch in sorted(self.devices, key=lambda item: -item[2]):
            try:
                add_device(self.p.get_device_info_by_index(index))
            except Exception:
                pass
        if wasapi_info:
            try:
                add_device(self.p.get_device_info_by_index(
                    wasapi_info["defaultOutputDevice"]))
            except Exception:
                pass

        if not candidates:
            self.error = "找不到任何音频输出设备"
            print("[错误] " + self.error)
            return False

        # 逐个设备尝试：8 声道 → 6 → 4 → 2
        self.stream = None
        self.channels = 2
        last_error = ""
        for dev in candidates:
            dev_index = int(dev.get("index", -1))
            dev_max_ch = int(dev.get("maxInputChannels", 2) or 2)
            rate = int(dev.get("defaultSampleRate", SAMPLE_RATE) or SAMPLE_RATE)
            attempts = sorted({ch for ch in (8, 6, 4, 2, dev_max_ch)
                               if 1 <= ch <= min(8, dev_max_ch)}, reverse=True) or [2]
            for try_ch in attempts:
                try:
                    stream = self.p.open(
                        format=pyaudio.paFloat32,
                        channels=try_ch,
                        rate=rate,
                        input=True,
                        input_device_index=dev_index,
                        frames_per_buffer=BLOCK_SIZE,
                        stream_callback=self._audio_callback,
                    )
                except Exception as exc:
                    last_error = str(exc)
                    continue
                self.stream = stream
                self.channels = try_ch
                self.sample_rate = rate
                self.device_name = str(dev.get("name", "?"))
                self.device_index = dev_index
                print("[音频] 捕获设备: %s (index=%d)" % (self.device_name, dev_index))
                print("[音频] 成功打开 %d 声道流, 采样率 %d" % (try_ch, rate))
                break
            if self.stream is not None:
                break

        if self.stream is None:
            self.error = "无法打开任何音频流（设备可能被独占）: %s" % last_error
            print("[错误] " + self.error)
            return False

        self.estimator = DirectionEstimator(self.sample_rate, BLOCK_SIZE)
        self.estimator.set_channels(self.channels)

        if self.channels == 2:
            print("[警告] 当前只有立体声(2ch)，只能检测前方左右，无法区分身后方向")
            print("[提示] 请在 Windows 声音设置中把输出设备配置为 7.1 环绕声")
        elif self.channels >= 6:
            print("[音频] 多声道模式(%dch)，支持 360 度方向检测（含身后）"
                  % self.channels)

        self.running = True
        try:
            self.stream.start_stream()
        except Exception as exc:
            self.error = "启动音频流失败: %s" % exc
            print("[错误] " + self.error)
            self.running = False
            return False
        return True

    # ---------- 音频回调（PortAudio 线程）----------
    def _audio_callback(self, in_data, frame_count, time_info, status):
        try:
            if not self.running or frame_count <= 0:
                return (None, 0)
            data = np.frombuffer(in_data, dtype=np.float32)
            channels = int(data.size // frame_count)
            if channels <= 0:
                return (None, 0)

            frames = data[:frame_count * channels].reshape(frame_count, channels)
            source = self.estimator.process(
                frames,
                noise_gate=CONFIG["noise_gate"],
                snr_threshold=CONFIG["snr_threshold"],
                direction_gain=CONFIG["direction_gain"],
                stereo_pan=(CONFIG.get("input_mode") == "stereo" and channels > 2),
            )
            now = time.time()

            # 最近各声道的活跃程度（约 4 秒衰减），用于判断"后方声道到底有没有声音"
            levels = self.estimator.levels
            if levels.size:
                if self._recent.shape != levels.shape:
                    self._recent = levels.copy()
                else:
                    self._recent = np.maximum(self._recent * 0.995, levels)

            # 一个音频块可能同时有多个声源（左后脚步 + 正前枪声）：
            # components 由 DirectionEstimator 按声道角度聚类得到，每个分量单独跟踪。
            per_source = []
            if source is not None:
                base = intensity_from_snr(source["snr"], CONFIG["snr_threshold"])
                components = source.get("components") or [{
                    "angle": source["angle"],
                    "share": 1.0,
                    "confidence": source["confidence"],
                }]
                for comp in components:
                    share = clamp(float(comp.get("share", 1.0)), 0.0, 1.0)
                    per_source.append({
                        "angle": comp["angle"],
                        "confidence": comp.get("confidence", source["confidence"]),
                        # 响度按该分量占的比重打折，主声源仍是满强度
                        "intensity": clamp(base * (0.35 + 0.65 * share), 0.12, 1.0),
                        "rms": source["rms"],
                        "peak_ratio": source["peak_ratio"],
                        "centroid": source["centroid"],
                    })

            for patch in self.tracker.update_multi(per_source, now):
                patch["channels"] = channels
                self.last_event = patch
                try:
                    self.callback(patch)
                except Exception:
                    pass

            if DEBUG:
                self._debug_tick(source, channels)
        except Exception as exc:
            if not self._callback_error:
                self._callback_error = True
                print("[错误] 音频回调异常: %s" % exc)
        return (None, 0)

    def _debug_tick(self, source, channels):
        if self._debug_levels is None or len(self._debug_levels) != channels:
            self._debug_levels = np.zeros(channels)
            self._debug_count = 0
        self._debug_levels += self.estimator.levels
        self._debug_count += 1
        if self._debug_count < 47:      # 约 1 秒
            return
        names = channel_names(channels)
        avg = self._debug_levels / float(self._debug_count)
        parts = []
        for i in range(channels):
            name = names[i] if i < len(names) else "CH%d" % i
            parts.append("%s:%.4f" % (name, avg[i]))
        print("[声道] " + " | ".join(parts))
        if source is not None:
            print("[方向] %s %+.0f°  conf=%.2f  snr=%.1f  (%dch)"
                  % (sector_name(source["angle"]), math.degrees(source["angle"]),
                     source["confidence"], source["snr"], channels))
        self._debug_count = 0
        self._debug_levels = np.zeros(channels)

    def levels_snapshot(self):
        return self.estimator.levels.copy()

    def activity_snapshot(self):
        """最近几秒各声道的峰值（用于判断哪些声道真的有声音）。"""
        return self._recent.copy()

    def stop(self):
        self.running = False
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:
            pass
        try:
            if self.p is not None:
                self.p.terminate()
        except Exception:
            pass


# ============================ 音频控制器 ============================
class AudioController:
    """
    音频捕获 + 声源事件存储。

    HUD、设置里的电平表、诊断都从这里取数据：
      start/restart 管 AudioAnalyzer；
      snapshot() 给 HUD 一帧声源列表（含自检预览用的合成声源）。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}
        self.analyzer = None
        self.preview_angle = None
        self.preview_until = 0.0
        self.last_error = ""

    # ---------- 生命周期 ----------
    def start(self, device_index=None):
        self.stop()
        self.analyzer = AudioAnalyzer(self._on_event)
        ok = self.analyzer.start(device_index)
        self.last_error = "" if ok else (self.analyzer.error or "音频捕获失败")
        return ok

    def restart(self, device_index=None):
        return self.start(device_index)

    def stop(self):
        if self.analyzer is not None:
            self.analyzer.stop()
            self.analyzer = None
        with self.lock:
            self.events.clear()

    # ---------- 事件 ----------
    def _on_event(self, event):
        with self.lock:
            self.events[event["id"]] = event
            if len(self.events) > 24:
                newest = sorted(self.events.values(),
                                key=lambda item: item["timestamp"])[-24:]
                self.events = {item["id"]: item for item in newest}

    def snapshot(self, now=None):
        """给 HUD 用的当前声源快照（线程安全，含自检预览）。"""
        now = time.time() if now is None else float(now)
        lifetime = max(0.1, float(CONFIG["event_lifetime"]))
        with self.lock:
            events = [dict(event) for event in self.events.values()]
        events = [event for event in events
                  if now - float(event.get("timestamp", 0.0)) < lifetime]
        if self.preview_angle is not None and now < self.preview_until:
            events.append({
                "id": -1,
                "angle": float(self.preview_angle),
                "intensity": 0.9,
                "confidence": 1.0,
                "type": "test",
                "timestamp": now,
                "channels": 0,
            })
        return events

    def set_preview(self, angle, hold=3.0):
        if angle is None:
            self.preview_angle = None
            self.preview_until = 0.0
        else:
            self.preview_angle = float(angle)
            self.preview_until = time.time() + float(hold)

    # ---------- 状态查询（电平表 / 诊断用）----------
    @property
    def running(self):
        return bool(self.analyzer and self.analyzer.running)

    @property
    def channels(self):
        return int(self.analyzer.channels) if self.analyzer else 0

    @property
    def sample_rate(self):
        return int(self.analyzer.sample_rate) if self.analyzer else 0

    @property
    def device_name(self):
        return str(self.analyzer.device_name) if self.analyzer else ""

    @property
    def device_index(self):
        return getattr(self.analyzer, "device_index", None) if self.analyzer else None

    @property
    def devices(self):
        return list(getattr(self.analyzer, "devices", []) or [])

    def estimator_channels(self):
        return self.analyzer.estimator.channels if self.analyzer else 0

    def levels_snapshot(self):
        return self.analyzer.levels_snapshot() if self.analyzer else np.zeros(0)

    def activity_snapshot(self):
        return self.analyzer.activity_snapshot() if self.analyzer else np.zeros(0)


# ============================ 声道电平表 ============================
class ChannelMeter(QWidget):
    """显示每个声道的实时电平，用来确认游戏声音到底走的是哪几个声道。"""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setMinimumHeight(158)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(80)
        self.setFont(QFont("Microsoft YaHei UI", 8))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        controller = self.controller
        levels = controller.levels_snapshot() if controller is not None else np.zeros(0)
        if controller is None or (not controller.running and levels.size == 0):
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "音频未启动")
            p.end()
            return

        names = channel_names(len(levels))
        p.setPen(QColor(160, 200, 160))
        p.drawText(4, 13, "%dch · %dHz · %s" % (
            controller.channels, controller.sample_rate,
            LAYOUT_DESC.get(controller.channels, "%dch" % controller.channels)))

        if len(levels) == 0:
            p.end()
            return

        row_h = max(12, min(16, int((h - 22) / max(1, len(levels)))))
        label_w = 44
        bar_x = label_w + 4
        bar_w = max(10, w - bar_x - 34)
        loudest = int(np.argmax(levels)) if levels.size else -1

        for i, level in enumerate(levels):
            y = 20 + i * row_h
            label = names[i] if i < len(names) else "CH%d" % i
            p.setPen(QColor(180, 180, 180))
            p.drawText(4, y + row_h - 4, label)

            fraction = clamp((20.0 * math.log10(max(float(level), 1e-7)) + 60.0) / 60.0,
                             0.0, 1.0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(60, 60, 60, 120))
            p.drawRoundedRect(QRectF(bar_x, y + 2, bar_w, row_h - 5), 2, 2)
            if i == loudest:
                color = QColor(255, 193, 7, 220)
            elif controller.estimator_channels() == 2:
                color = QColor(66, 165, 245, 200)
            else:
                color = QColor(102, 187, 106, 200)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(bar_x, y + 2, max(2.0, bar_w * fraction),
                                     row_h - 5), 2, 2)

            p.setPen(QColor(130, 130, 130))
            p.drawText(bar_x + bar_w + 4, y + row_h - 4, "%.0f" % (
                20.0 * math.log10(max(float(level), 1e-7))))
        p.end()


# ============================ 全部设置窗口 ============================
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                            "FPSAudioHUD")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

AUDIO_DEFAULTS = {
    "sensitivity": SENSITIVITY,
    "snr_threshold": SNR_THRESHOLD,
    "direction_gain": DIRECTION_GAIN,
    "noise_gate": NOISE_GATE,
    "event_lifetime": EVENT_LIFETIME,
}


def save_settings(hud):
    """把当前 CONFIG + HUD 参数写进 settings.json；写不了就静默跳过。"""
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        data = {"config": dict(CONFIG), "hud": dataclasses.asdict(hud.config)}
        with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply_saved_settings(hud):
    """启动时套用上次保存的设置（不认识的字段直接忽略）。"""
    saved = load_settings()

    config = saved.get("config") or {}
    if isinstance(config, dict):
        for key, value in config.items():
            if key not in CONFIG:
                continue
            try:
                CONFIG[key] = type(CONFIG[key])(value)
            except (TypeError, ValueError):
                continue

    hud_values = saved.get("hud") or {}
    if isinstance(hud_values, dict):
        allowed = {field.name for field in dataclasses.fields(HudConfig)}
        clean = {key: value for key, value in hud_values.items() if key in allowed}
        if clean:
            try:
                hud.config = hud.config.clone(**clean)
            except TypeError:
                pass
    return saved


class SettingsWindow(QDialog):
    """
    全部设置：音频 / HUD / 设备与自检。

    改动即时生效，不用点"应用"；关闭窗口时把参数写到
    %APPDATA%/FPSAudioHUD/settings.json，下次启动自动套用。
    """

    AUDIO_FIELDS = [
        ("sensitivity",    "灵敏度",        0.5, 2.5, 0.1, 2),
        ("snr_threshold",  "信噪比阈值",    1.0, 5.0, 0.1, 2),
        ("direction_gain", "方向增益",      0.5, 3.0, 0.1, 2),
        ("noise_gate",     "噪声门下限",    0.001, 0.05, 0.001, 4),
        ("event_lifetime", "提示保留(秒)",  0.5, 3.0, 0.1, 2),
    ]

    def __init__(self, controller, hud, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.hud = hud
        self.audio_spins = {}
        self._loading = True

        self.setWindowTitle("全部设置")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_audio_tab(), "音频")
        self.tabs.addTab(self._build_hud_tab(), "HUD")
        self.tabs.addTab(self._build_device_tab(), "设备 / 自检")
        layout.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset_defaults)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.tabs.currentChanged.connect(self._sync_widgets)
        self._loading = False
        self._sync_widgets()

        self._diag_timer = QTimer(self)
        self._diag_timer.timeout.connect(self._update_diagnosis)
        self._diag_timer.start(400)
        self._update_diagnosis()

    # ---------------- 音频页 ----------------
    def _build_audio_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("一键预设:"))
        for level in ("低", "中", "高"):
            btn = QPushButton(level)
            btn.clicked.connect(
                lambda checked=False, lv=level: self._apply_audio_preset(lv))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        form = QFormLayout()
        for key, label, lo, hi, step, decimals in self.AUDIO_FIELDS:
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)
            spin.setValue(float(CONFIG[key]))
            spin.valueChanged.connect(self._apply_audio)
            self.audio_spins[key] = spin
            form.addRow(label + ":", spin)
        layout.addLayout(form)

        hint = QLabel("提示保留：声音停止后，HUD 上的波形条和箭头还要淡出多久。")
        hint.setStyleSheet("color:#888; font-size:10px;")
        layout.addWidget(hint)

        layout.addWidget(QLabel("声道电平（确认游戏声音走的是哪几个声道）"))
        self.meter = ChannelMeter(self.controller)
        layout.addWidget(self.meter)

        self.diag = QLabel("正在检测…")
        self.diag.setWordWrap(True)
        layout.addWidget(self.diag)
        return page

    def _apply_audio(self, *_args):
        if self._loading:
            return
        for key, spin in self.audio_spins.items():
            CONFIG[key] = float(spin.value())

    def _apply_audio_preset(self, level):
        preset = PRESETS.get(level, {})
        self._loading = True
        for key, value in preset.items():
            if key in self.audio_spins:
                self.audio_spins[key].setValue(float(value))
        self._loading = False
        self._apply_audio()

    def _update_diagnosis(self):
        controller = self.controller
        if controller is None or not controller.running:
            self.diag.setText("音频未启动，无法诊断。")
            return
        recent = controller.activity_snapshot()
        channels = int(controller.channels)
        if recent.size == 0:
            return
        if channels <= 2:
            self._set_diag(
                "当前是 2 声道：只能判断左右，前后无法区分。\n"
                "想显示左后/右后，需要 Windows 输出设备配置成 5.1/7.1，"
                "并且游戏内音频也选 5.1/7.1（不要选耳机/立体声）。", "warn")
            return

        layout = channel_layout(channels)
        valid = [(index, name, angle) for index, (name, angle, flag) in
                 enumerate(layout) if flag and index < len(recent)]
        if not valid:
            return
        reference = max(float(recent[index]) for index, _n, _a in valid)
        if reference < max(0.004, float(CONFIG["noise_gate"])):
            self._set_diag("等待声音…（在游戏里走两步，看哪些声道有电平）", "idle")
            return
        cut = max(0.0015, reference * 0.25)

        front, surround = [], []
        for index, name, angle in valid:
            if recent[index] < cut:
                continue
            label = CHANNEL_LABELS.get(name, name)
            (surround if abs(angle) > 45.0 else front).append(label)

        if surround:
            self._set_diag(
                "检测到环绕声道有声音（%s）→ 多声道正常，可以显示后方方向。"
                % "、".join(surround[:4]), "ok")
        elif front:
            self._set_diag(
                "只有前方声道有声音（%s）→ 后方声道是空的，算不出左后/右后。\n"
                "说明游戏/Windows 实际输出的是立体声。可以先把「输入模式」"
                "切成「立体声」，至少让左右拉满到两侧。"
                % "、".join(front[:3]), "warn")
        else:
            self._set_diag("等待声音…（在游戏里走两步，看哪些声道有电平）", "idle")

    def _set_diag(self, text, level):
        self.diag.setText(text)
        styles = {
            "ok": ("color:#7cb342;", "rgba(102,187,106,0.10)"),
            "warn": ("color:#e0a030;", "rgba(255,152,0,0.10)"),
            "idle": ("color:#888;", "rgba(0,0,0,0.05)"),
        }
        color, background = styles.get(level, styles["idle"])
        self.diag.setStyleSheet(
            "%s font-size:10px; padding:4px 6px; "
            "background:%s; border-radius:4px;" % (color, background))

    # ---------------- HUD 页 ----------------
    def _build_hud_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()

        self.cmb_mode = QComboBox()
        for mode in HUD_MODES:
            self.cmb_mode.addItem(MODE_NAMES.get(mode, mode), mode)
        self.cmb_mode.currentIndexChanged.connect(self._apply_hud)
        form.addRow("显示模式:", self.cmb_mode)

        self.cmb_hint = QComboBox()
        for key in ("focus", "multi"):
            self.cmb_hint.addItem(HINT_NAMES[key], key)
        self.cmb_hint.currentIndexChanged.connect(self._apply_hud)
        form.addRow("提示逻辑:", self.cmb_hint)

        self.cmb_preset = QComboBox()
        for name in HUD_PRESET_NAMES:
            self.cmb_preset.addItem(name, name)
        self.cmb_preset.currentIndexChanged.connect(self._apply_hud_preset)
        form.addRow("提示档位:", self.cmb_preset)

        self.cmb_color = QComboBox()
        for label, key in HUD_COLOR_OPTIONS:
            self.cmb_color.addItem(label, key)
        self.cmb_color.currentIndexChanged.connect(self._apply_hud)
        form.addRow("提示颜色:", self.cmb_color)

        self.spin_size = QDoubleSpinBox()
        self.spin_size.setRange(0.60, 1.40)
        self.spin_size.setSingleStep(0.05)
        self.spin_size.setDecimals(2)
        self.spin_size.valueChanged.connect(self._apply_hud)
        form.addRow("尺寸:", self.spin_size)

        self.spin_opacity = QDoubleSpinBox()
        self.spin_opacity.setRange(0.15, 1.00)
        self.spin_opacity.setSingleStep(0.05)
        self.spin_opacity.setDecimals(2)
        self.spin_opacity.valueChanged.connect(self._apply_hud)
        form.addRow("元素不透明度:", self.spin_opacity)

        self.spin_alpha = QSpinBox()
        self.spin_alpha.setRange(20, 100)
        self.spin_alpha.setSingleStep(5)
        self.spin_alpha.setSuffix(" %")
        self.spin_alpha.valueChanged.connect(self._apply_hud)
        form.addRow("整体透明:", self.spin_alpha)

        self.spin_glow = QDoubleSpinBox()
        self.spin_glow.setRange(0.00, 1.20)
        self.spin_glow.setSingleStep(0.05)
        self.spin_glow.setDecimals(2)
        self.spin_glow.valueChanged.connect(self._apply_hud)
        form.addRow("光晕:", self.spin_glow)

        self.spin_sources = QSpinBox()
        self.spin_sources.setRange(1, 6)
        self.spin_sources.valueChanged.connect(self._apply_hud)
        form.addRow("多方向上限:", self.spin_sources)

        self.spin_dot = QSpinBox()
        self.spin_dot.setRange(1, 8)
        self.spin_dot.valueChanged.connect(self._apply_hud)
        form.addRow("中心点大小:", self.spin_dot)

        self.chk_edges = QCheckBox("显示边缘波形条")
        self.chk_edges.stateChanged.connect(self._apply_hud)
        self.chk_pointer = QCheckBox("显示中心箭头")
        self.chk_pointer.stateChanged.connect(self._apply_hud)
        self.chk_dot = QCheckBox("准星红色中心点")
        self.chk_dot.stateChanged.connect(self._apply_hud)
        checks = QVBoxLayout()
        checks.addWidget(self.chk_edges)
        checks.addWidget(self.chk_pointer)
        checks.addWidget(self.chk_dot)
        layout.addLayout(checks)

        layout.addLayout(form)

        note = QLabel(
            "整体透明 = 整个 HUD 的透明程度（包括刻度盘和红点）；\n"
            "元素不透明度 = 波形条/箭头本身的浓度。两个都调低最不抢眼。"
        )
        note.setStyleSheet("color:#888; font-size:10px;")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _apply_hud(self, *_args):
        if self._loading:
            return
        mode = self.cmb_mode.currentData() or "dual"
        hint = self.cmb_hint.currentData() or "focus"
        color = self.cmb_color.currentData() or "red"

        if CONFIG.get("hud_mode") != mode:
            CONFIG["hud_mode"] = mode
            self.hud.set_mode(mode)
        if CONFIG.get("hint_logic") != hint:
            CONFIG["hint_logic"] = hint
            self.hud.set_hint_logic(hint)
        if CONFIG.get("hud_color") != color:
            CONFIG["hud_color"] = color
            self.hud.set_color_mode(color)

        cfg = self.hud.config.clone(
            size=float(self.spin_size.value()),
            opacity=float(self.spin_opacity.value()),
            global_alpha=float(self.spin_alpha.value()) / 100.0,
            glow=float(self.spin_glow.value()),
            max_sources=int(self.spin_sources.value()),
            show_edges=bool(self.chk_edges.isChecked()),
            show_pointer=bool(self.chk_pointer.isChecked()),
            center_dot=bool(self.chk_dot.isChecked()),
            center_dot_size=float(self.spin_dot.value()),
        )
        CONFIG["hud_max_sources"] = int(cfg.max_sources)
        CONFIG["center_dot"] = bool(cfg.center_dot)
        CONFIG["center_dot_size"] = int(cfg.center_dot_size)
        self.hud.apply_config(cfg)

    def _apply_hud_preset(self, *_args):
        if self._loading:
            return
        name = self.cmb_preset.currentData()
        if not name:
            return
        base = self.hud.config
        cfg = preset_config(name).clone(
            center_dot=base.center_dot,
            center_dot_size=base.center_dot_size,
            global_alpha=base.global_alpha,
        )
        self.hud.apply_config(cfg)
        CONFIG["hud_preset"] = name
        CONFIG["hud_max_sources"] = int(cfg.max_sources)
        self._sync_widgets()

    # ---------------- 设备页 ----------------
    def _build_device_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()

        self.cmb_device = QComboBox()
        self.cmb_device.currentIndexChanged.connect(self._switch_device)
        form.addRow("音频设备:", self.cmb_device)

        refresh_btn = QPushButton("刷新设备列表（重启音频）")
        refresh_btn.clicked.connect(self._refresh_devices)
        form.addRow("", refresh_btn)

        self.cmb_input = QComboBox()
        self.cmb_input.addItem("自动（按声道布局）", "auto")
        self.cmb_input.addItem("立体声（只按前左/前右）", "stereo")
        self.cmb_input.currentIndexChanged.connect(self._switch_input_mode)
        form.addRow("输入模式:", self.cmb_input)

        layout.addLayout(form)

        test_btn = QPushButton("HUD 方向自检...")
        test_btn.clicked.connect(self._open_check)
        layout.addWidget(test_btn)

        info = QLabel(
            "HUD 铺满主屏、置顶、鼠标穿透；检测到前台全屏游戏时会自动取消置顶。\n"
            "设置保存在：%s" % SETTINGS_PATH
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#888; font-size:10px;")
        layout.addWidget(info)
        layout.addStretch(1)
        return page

    def _fill_devices(self):
        previous = self._loading
        self._loading = True
        self.cmb_device.clear()
        devices = self.controller.devices if self.controller else []
        for index, name, channels in devices:
            self.cmb_device.addItem("%s (%dch)" % (name[:44], channels), int(index))
        position = self.cmb_device.findData(self.controller.device_index
                                            if self.controller else None)
        if position >= 0:
            self.cmb_device.setCurrentIndex(position)
        self._loading = previous

    def _refresh_devices(self):
        if self.controller is None:
            return
        self.controller.restart(self.controller.device_index)
        self._fill_devices()
        self._update_diagnosis()

    def _switch_device(self, *_args):
        if self._loading or self.controller is None:
            return
        index = self.cmb_device.currentData()
        if index is None or index == self.controller.device_index:
            return
        self.controller.restart(int(index))
        self._fill_devices()
        self._update_diagnosis()

    def _switch_input_mode(self, *_args):
        if self._loading:
            return
        key = self.cmb_input.currentData() or "auto"
        CONFIG["input_mode"] = key

    def _open_check(self):
        dialog = HudCheckDialog(self.controller, self)
        dialog.exec_()
        self.controller.set_preview(None)

    # ---------------- 同步 / 重置 / 收尾 ----------------
    def _sync_widgets(self, *_args):
        self._loading = True
        cfg = self.hud.config

        index = self.cmb_mode.findData(CONFIG.get("hud_mode", "dual"))
        self.cmb_mode.setCurrentIndex(index if index >= 0 else 0)
        index = self.cmb_hint.findData(CONFIG.get("hint_logic", "focus"))
        self.cmb_hint.setCurrentIndex(index if index >= 0 else 0)
        index = self.cmb_color.findData(CONFIG.get("hud_color", "red"))
        self.cmb_color.setCurrentIndex(index if index >= 0 else 0)
        index = self.cmb_preset.findData(CONFIG.get("hud_preset") or "")
        self.cmb_preset.setCurrentIndex(index)

        self.spin_size.setValue(float(cfg.size))
        self.spin_opacity.setValue(float(cfg.opacity))
        self.spin_alpha.setValue(int(round(float(cfg.global_alpha) * 100)))
        self.spin_glow.setValue(float(cfg.glow))
        self.spin_sources.setValue(int(cfg.max_sources))
        self.spin_dot.setValue(int(cfg.center_dot_size))
        self.chk_edges.setChecked(bool(cfg.show_edges))
        self.chk_pointer.setChecked(bool(cfg.show_pointer))
        self.chk_dot.setChecked(bool(cfg.center_dot))

        for key, spin in self.audio_spins.items():
            spin.setValue(float(CONFIG[key]))

        index = self.cmb_input.findData(CONFIG.get("input_mode", "auto"))
        self.cmb_input.setCurrentIndex(index if index >= 0 else 0)

        self._fill_devices()
        self._loading = False

    def _reset_defaults(self):
        self._loading = True
        for key, value in AUDIO_DEFAULTS.items():
            CONFIG[key] = value
        CONFIG.update({
            "hud_mode": "dual",
            "hint_logic": "focus",
            "hud_preset": "平衡",
            "hud_color": "red",
            "hud_max_sources": 4,
            "center_dot": True,
            "center_dot_size": 3,
            "input_mode": "auto",
        })
        self.hud.apply_config(preset_config("平衡"))
        self.hud.set_mode("dual")
        self.hud.set_hint_logic("focus")
        self.hud.set_color_mode("red")
        self._loading = False
        self._sync_widgets()

    def closeEvent(self, event):
        save_settings(self.hud)
        super().closeEvent(event)


# ============================ HUD 方向自检 ============================
class HudCheckDialog(QDialog):
    """
    点八个方向按钮（或拖滑块），全屏 HUD 立刻按该方向显示 3 秒。

    小雷达窗口去掉以后，用这个检查"HUD 的箭头和波形条到底指得对不对"。
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("HUD 方向自检")
        self.setFixedWidth(400)
        self._angle = 0.0

        layout = QVBoxLayout(self)
        hint = QLabel("点方向按钮或拖滑块，全屏 HUD 会按这个方向显示 3 秒。\n"
                      "箭头应指向对应方位，波形条应亮在对应那侧。")
        hint.setStyleSheet("color:#33691e; font-size:11px;")
        layout.addWidget(hint)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 359)
        self.slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self.slider)

        self.info = QLabel("0° · 中前")
        self.info.setAlignment(Qt.AlignCenter)
        self.info.setStyleSheet("color:#2e7d32; font-size:13px; font-weight:bold;")
        layout.addWidget(self.info)

        grid = QGridLayout()
        for index, (name, deg) in enumerate(zip(SECTOR_NAMES, SECTOR_ANGLES_DEG)):
            button = QPushButton("%s\n%d°" % (name, int(deg) % 360))
            button.clicked.connect(
                lambda checked=False, d=deg: self.set_angle_deg(d))
            grid.addWidget(button, index // 4, index % 4)
        layout.addLayout(grid)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        # 预览只有 3 秒寿命，窗口开着时定时续一下，不然会自己消失
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(400)

        self.set_angle_deg(0.0)

    def _on_slider(self, value):
        self.set_angle_deg(float(value), update_slider=False)

    def set_angle_deg(self, degrees, update_slider=True):
        degrees = float(degrees) % 360.0
        if update_slider:
            self.slider.blockSignals(True)
            self.slider.setValue(int(round(degrees)) % 360)
            self.slider.blockSignals(False)
        self._angle = degrees
        angle = math.radians(degrees)
        self.info.setText("%d° · %s" % (int(round(degrees)) % 360, sector_name(angle)))
        self._refresh()

    def _refresh(self):
        self.controller.set_preview(math.radians(self._angle), hold=3.0)

    def closeEvent(self, event):
        self.controller.set_preview(None)
        super().closeEvent(event)


# ============================ 托盘菜单 ============================
GAIN_LEVELS = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0]
CENTER_DOT_SIZES = [0, 1, 2, 3, 4, 5, 6, 8]


def make_tray_icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(102, 187, 106), 3))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(8, 8, 48, 48)
    p.setBrush(QColor(239, 68, 68))
    p.setPen(Qt.NoPen)
    p.drawEllipse(28, 28, 8, 8)
    p.end()
    return QIcon(pix)


def main():
    global DEBUG

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--debug", action="store_true",
                        help="在控制台打印每秒声道电平与识别到的方向")
    parser.add_argument("--no-audio", action="store_true",
                        help="只显示 HUD，不启动音频捕获（排查显示问题用）")
    parser.add_argument("--hud", choices=list(HUD_MODES), default=None,
                        help="启动时的 HUD 形态：wave=波形条 / pointer=箭头 / dual=双 HUD")
    parser.add_argument("--hint", choices=["focus", "multi"], default=None,
                        help="HUD 提示逻辑：focus=专注提示 / multi=多方向提示")
    args, _unknown = parser.parse_known_args()
    DEBUG = bool(args.debug)
    if args.hud:
        CONFIG["hud_mode"] = args.hud
    if args.hint:
        CONFIG["hint_logic"] = args.hint

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    controller = AudioController()
    if not args.no_audio and not controller.start():
        print("[错误] %s" % controller.last_error)

    # 全屏 HUD：数据直接取控制器里的声源快照
    hud = HudWindow(controller.snapshot)
    hud.apply_preset(CONFIG["hud_preset"])
    hud.apply_config(hud.config.clone(center_dot=CONFIG["center_dot"],
                                      center_dot_size=CONFIG["center_dot_size"]))
    # 上次退出时的参数（%APPDATA%FPSAudioHUDsettings.json）直接套回来
    apply_saved_settings(hud)
    hud.set_lifetime(CONFIG["event_lifetime"])
    hud.set_hint_logic(CONFIG["hint_logic"])
    hud.set_color_mode(CONFIG["hud_color"])
    hud.set_mode(CONFIG["hud_mode"])

    # 独占全屏游戏里置顶窗口会压画面：检测到全屏就临时取消置顶
    fs_timer = QTimer(app)

    def _check_fullscreen():
        try:
            hud.set_topmost(not is_foreground_fullscreen(int(hud.winId())))
        except Exception:
            pass

    fs_timer.timeout.connect(_check_fullscreen)
    fs_timer.start(800)

    def _save():
        save_settings(hud)

    # 托盘
    tray = QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip("FPS 音频 HUD")
    menu = QMenu()

    def _status_text():
        if controller.running:
            name = controller.device_name.replace(" [Loopback]", "")
            if len(name) > 22:
                name = name[:20] + "…"
            text = "音频: %dch %dHz %s" % (
                controller.channels, controller.sample_rate,
                LAYOUT_DESC.get(controller.channels, "?"))
            if name:
                text += " · " + name
            return text
        if controller.last_error:
            return "音频: 未启动（%s）" % controller.last_error[:24]
        return "音频: 未启动"

    act_status = QAction(_status_text(), menu)
    act_status.setEnabled(False)
    menu.addAction(act_status)

    # 音频设备切换（多个回环设备时用）
    devices = controller.devices
    if len(devices) > 1:
        device_menu = menu.addMenu("音频设备")
        device_actions = {}

        def _switch_device(index):
            controller.restart(index)
            act_status.setText(_status_text())
            tray.setToolTip(act_status.text())
            for dev_index, act in device_actions.items():
                act.setChecked(dev_index == controller.device_index)

        for index, name, _channels in devices:
            act = QAction(name[:42], device_menu, checkable=True)
            act.setChecked(index == controller.device_index)
            act.triggered.connect(lambda checked=False, i=index: _switch_device(i))
            device_actions[index] = act
            device_menu.addAction(act)

    if controller.running:
        tray.setToolTip("FPS 音频 HUD · %dch %dHz" % (
            controller.channels, controller.sample_rate))
    menu.addSeparator()

    # ---------------- 全屏 HUD：波形条 / 箭头 ----------------
    hud_menu = menu.addMenu("HUD 显示")
    hud_actions = {}

    def _make_hud_switch(mode):
        def _switch():
            CONFIG["hud_mode"] = mode
            hud.set_mode(mode)
            _save()
            for key, act in hud_actions.items():
                act.setChecked(key == mode)
            tip = "FPS 音频 HUD"
            if mode != "off":
                tip += " · %s" % MODE_NAMES.get(mode, mode)
            tray.setToolTip(tip)
        return _switch

    for mode in HUD_MODES:
        act = QAction(MODE_NAMES.get(mode, mode), hud_menu, checkable=True)
        act.setChecked(CONFIG.get("hud_mode", "off") == mode)
        act.triggered.connect(_make_hud_switch(mode))
        hud_actions[mode] = act
        hud_menu.addAction(act)

    hint_menu = menu.addMenu("HUD 提示逻辑")
    hint_actions = {}

    def _make_hint_switch(key):
        def _switch():
            CONFIG["hint_logic"] = key
            hud.set_hint_logic(key)
            _save()
            for k, act in hint_actions.items():
                act.setChecked(k == key)
        return _switch

    for key in ("focus", "multi"):
        act = QAction(HINT_NAMES[key], hint_menu, checkable=True)
        act.setChecked(CONFIG.get("hint_logic", "focus") == key)
        act.triggered.connect(_make_hint_switch(key))
        hint_actions[key] = act
        hint_menu.addAction(act)

    preset_menu = menu.addMenu("HUD 提示档位")
    preset_actions = {}

    def _make_preset_switch(name):
        def _switch():
            CONFIG["hud_preset"] = name
            hud.apply_preset(name)
            hud.apply_config(hud.config.clone(center_dot=CONFIG["center_dot"],
                                              center_dot_size=CONFIG["center_dot_size"]))
            _save()
            for k, act in preset_actions.items():
                act.setChecked(k == name)
        return _switch

    for name in HUD_PRESET_NAMES:
        act = QAction(name, preset_menu, checkable=True)
        act.setChecked(CONFIG.get("hud_preset") == name)
        act.triggered.connect(_make_preset_switch(name))
        preset_actions[name] = act
        preset_menu.addAction(act)

    hud_color_menu = menu.addMenu("HUD 颜色")
    hud_color_actions = {}

    def _make_hud_color_switch(key):
        def _switch():
            CONFIG["hud_color"] = key
            hud.set_color_mode(key)
            _save()
            for k, act in hud_color_actions.items():
                act.setChecked(k == key)
        return _switch

    for label, key in HUD_COLOR_OPTIONS:
        act = QAction(label, hud_color_menu, checkable=True)
        act.setChecked(CONFIG.get("hud_color", "red") == key)
        act.triggered.connect(_make_hud_color_switch(key))
        hud_color_actions[key] = act
        hud_color_menu.addAction(act)

    # 中心红点（准星位置那个点）
    dot_menu = menu.addMenu("中心红点")
    dot_actions = {}

    def _make_dot_switch(size):
        def _switch():
            CONFIG["center_dot"] = size > 0
            CONFIG["center_dot_size"] = int(size)
            hud.apply_config(hud.config.clone(center_dot=size > 0,
                                              center_dot_size=float(size)))
            _save()
            for value, act in dot_actions.items():
                act.setChecked(value == size)
        return _switch

    for size in CENTER_DOT_SIZES:
        label = "不显示" if size == 0 else "%d px" % size
        act = QAction(label, dot_menu, checkable=True)
        act.setChecked(CONFIG.get("center_dot_size", 3) == size and
                       (size != 0 or not CONFIG.get("center_dot", True)))
        act.triggered.connect(_make_dot_switch(size))
        dot_actions[size] = act
        dot_menu.addAction(act)

    act_hud_settings = QAction("HUD 设置...", menu)
    act_hud_settings.triggered.connect(lambda: _open_settings(1))
    menu.addAction(act_hud_settings)

    # 输入模式：多声道设备但游戏只给立体声时，切到"立体声"能把左右拉满
    input_menu = menu.addMenu("输入模式")
    input_actions = {}

    def _make_input_switch(key):
        def _switch():
            CONFIG["input_mode"] = key
            _save()
            for k, act in input_actions.items():
                act.setChecked(k == key)
        return _switch

    for key, name in (("auto", "自动（按声道布局）"), ("stereo", "立体声（只按前左/前右）")):
        act = QAction(name, input_menu, checkable=True)
        act.setChecked(CONFIG.get("input_mode", "auto") == key)
        act.triggered.connect(_make_input_switch(key))
        input_actions[key] = act
        input_menu.addAction(act)

    # 方向增益
    gain_menu = menu.addMenu("方向增益(准星)")
    gain_actions = {}

    def _make_gain_switch(val):
        def _switch():
            CONFIG["direction_gain"] = val
            _save()
            for v, act in gain_actions.items():
                act.setChecked(v == val)
        return _switch

    for val in GAIN_LEVELS:
        act = QAction("%s" % val, gain_menu, checkable=True)
        act.setChecked(abs(CONFIG["direction_gain"] - val) < 1e-6)
        act.triggered.connect(_make_gain_switch(val))
        gain_actions[val] = act
        gain_menu.addAction(act)

    menu.addSeparator()

    # 统一设置窗口：所有参数（音频 / HUD / 透明 / 设备）都在里面
    def _open_settings(tab=0):
        dialog = SettingsWindow(controller, hud)
        dialog.tabs.setCurrentIndex(tab)
        dialog.exec_()
        hud.update()
        hud.set_lifetime(CONFIG["event_lifetime"])
        _save()

    act_settings = QAction("全部设置...", menu)
    act_settings.triggered.connect(lambda: _open_settings(0))
    menu.addAction(act_settings)

    # HUD 方向自检
    def _open_check():
        dlg = HudCheckDialog(controller)
        dlg.exec_()
        controller.set_preview(None)
    act_check = QAction("HUD 方向自检...", menu)
    act_check.triggered.connect(_open_check)
    menu.addAction(act_check)

    menu.addSeparator()

    # 退出
    act_quit = QAction("退出", menu)

    def _quit():
        _save()
        controller.set_preview(None)
        controller.stop()
        app.quit()

    act_quit.triggered.connect(_quit)
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
