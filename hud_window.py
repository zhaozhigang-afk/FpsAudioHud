# -*- coding: utf-8 -*-
"""
FPS 音频雷达 —— 全屏 HUD 窗口（音浪 / 指针）

形态和主雷达窗口一致：无边框、置顶、透明背景、鼠标穿透，不抢游戏焦点。
区别是它铺满主屏幕：音浪贴着屏幕四边，指针在屏幕正中（准星附近），
不需要看角落的小雷达也能判断方向。

数据来源：外部传进来的 events_getter（主程序里就是 AudioController.snapshot），
这里不碰音频，只做「取事件 → 算一帧 → 画」。

显示模式：off / wave / pointer / dual
提示逻辑：focus（专注提示，只画最响的一个）/ multi（多方向提示，最多 N 个）
"""

from __future__ import annotations

import ctypes
import platform
import time

from PyQt5.QtCore import Qt, QTimer, QRect
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QApplication, QWidget

from hud_geometry import (HudConfig, build_frame, preset_config)
from hud_paint import paint_hud

IS_WINDOWS = platform.system() == "Windows"

# ---------------------------- Win32 穿透 ----------------------------
if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _GWL_EXSTYLE = -20
    _WS_EX_LAYERED = 0x00080000
    _WS_EX_TRANSPARENT = 0x00000020
    _HWND_TOPMOST = -1
    _HWND_NOTOPMOST = -2
    _SWP_NOMOVE = 0x0002
    _SWP_NOSIZE = 0x0001
    _SWP_NOACTIVATE = 0x0010


def set_click_through(hwnd, enable=True):
    """让窗口鼠标事件穿透到下层窗口（游戏），HUD 才能既看得见又不挡操作。"""
    if not IS_WINDOWS or not hwnd:
        return
    style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if enable:
        style |= (_WS_EX_LAYERED | _WS_EX_TRANSPARENT)
    else:
        style &= ~_WS_EX_TRANSPARENT
    _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)


def set_topmost(hwnd, enable=True):
    """独占全屏游戏里置顶窗口会把画面压掉，检测到全屏时临时取消置顶。"""
    if not IS_WINDOWS or not hwnd:
        return
    _user32.SetWindowPos(int(hwnd), _HWND_TOPMOST if enable else _HWND_NOTOPMOST,
                         0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)


# ---------------------------- HUD 窗口 ----------------------------
class HudWindow(QWidget):
    """铺满主屏幕的透明 HUD；默认不显示，set_mode("dual") 之类才会出现。"""

    def __init__(self, events_getter=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint
                            | Qt.Tool
                            | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("FPS 音频雷达 HUD")

        self.config = HudConfig()
        self.mode = "off"
        self.hint_logic = "focus"
        self.color_mode = "red"
        self.lifetime = 1.4
        self._events_getter = events_getter
        self._topmost = True

        self._timer = QTimer(self)
        self._timer.setInterval(33)          # ~30fps，够跟手又不费 CPU
        self._timer.timeout.connect(self._tick)

        self._fit_to_screen()

    # ---------- 对外接口 ----------
    def set_events_getter(self, getter):
        self._events_getter = getter

    def set_mode(self, mode):
        mode = mode if mode in ("off", "wave", "pointer", "dual") else "off"
        self.mode = mode
        if mode == "off":
            self._timer.stop()
            self.hide()
            return
        self._fit_to_screen()
        self.show()
        self._apply_native_flags()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def set_hint_logic(self, logic):
        self.hint_logic = "multi" if logic == "multi" else "focus"
        self.update()

    def set_color_mode(self, color_mode):
        self.color_mode = str(color_mode or "red")
        self.update()

    def set_lifetime(self, seconds):
        self.lifetime = max(0.2, float(seconds))

    def apply_preset(self, name):
        self.config = preset_config(name)
        self.update()

    def apply_config(self, config):
        self.config = config
        self.update()

    def set_topmost(self, enable):
        """全屏游戏切前台时由主程序调用，避免 HUD 抢独占全屏。"""
        if enable == self._topmost:
            return
        self._topmost = bool(enable)
        flags = self.windowFlags()
        if enable:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if self.mode != "off":
            self.show()
        self._apply_native_flags()

    # ---------- 内部 ----------
    def _fit_to_screen(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.geometry()
        self.setGeometry(QRect(geo.x(), geo.y(), geo.width(), geo.height()))

    def _apply_native_flags(self):
        hwnd = int(self.winId()) if self.isVisible() else 0
        if not hwnd:
            return
        set_click_through(hwnd, True)
        set_topmost(hwnd, self._topmost)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_native_flags()

    def _tick(self):
        if self.mode == "off":
            return
        self.update()

    def _current_events(self):
        if self._events_getter is None:
            return []
        try:
            return list(self._events_getter() or ())
        except Exception:
            return []

    def build_current_frame(self, now=None):
        now = time.time() if now is None else float(now)
        return build_frame(self._current_events(), now, lifetime=self.lifetime,
                           config=self.config, hint_logic=self.hint_logic)

    def render_hud(self, painter, width, height, now=None):
        frame = self.build_current_frame(now)
        paint_hud(painter, width, height, frame, self.config, self.mode, self.color_mode)

    def paintEvent(self, event):
        if self.mode == "off":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        self.render_hud(p, self.width(), self.height())
        p.end()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
