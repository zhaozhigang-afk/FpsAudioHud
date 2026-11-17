# -*- coding: utf-8 -*-
"""检查"前台全屏时取消置顶"的逻辑对无边框全屏游戏意味着什么（HUD 版）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication, QWidget

import fps_audio_radar as app

qa = QApplication(sys.argv)
hud = app.HudWindow(lambda: [])
hud.set_mode("dual")

# 模拟一个"无边框全屏"游戏窗口（和游戏 borderless 全屏一样：尺寸=屏幕、无标题栏）
game = QWidget()
game.setWindowFlags(Qt.FramelessWindowHint)
game.showFullScreen()
game.activateWindow()
game.raise_()

result = {}


def check():
    fg = app.is_foreground_fullscreen(int(hud.winId()))
    flags_before = bool(hud.windowFlags() & Qt.WindowStaysOnTopHint)
    hud.set_topmost(not fg)          # 主程序里由 800ms 定时器做同一件事
    flags_after = bool(hud.windowFlags() & Qt.WindowStaysOnTopHint)
    result.update(fg=fg, before=flags_before, after=flags_after)
    qa.quit()


QTimer.singleShot(1200, check)
qa.exec_()

print("前台窗口被判定为全屏:", result.get("fg"))
print("HUD 置顶: 检查前 %s -> 检查后 %s" % (result.get("before"), result.get("after")))
print("结论:", "无边框全屏游戏在前台时，HUD 会失去置顶（被游戏画面盖住，不会压坏独占全屏）"
      if result.get("fg") and not result.get("after") else "未复现")
