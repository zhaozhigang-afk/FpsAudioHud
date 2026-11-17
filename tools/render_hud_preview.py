# -*- coding: utf-8 -*-
"""
开发用：把 HUD 的几种形态渲染成图片，检查音浪 / 指针的落点与外观。

运行：python tools/render_hud_preview.py
输出：preview/hud_wave.png、hud_pointer.png、hud_multi.png、hud_focus.png、hud_sheet.png
"""

import os
import sys
import time
import math

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter, QColor, QFont
from PyQt5.QtCore import Qt, QRectF

from hud_window import HudWindow
from hud_geometry import HudConfig, preset_config

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preview")
W, H = 960, 540


def make_events(now, spec):
    events = []
    for index, (deg, intensity, sound_type, age) in enumerate(spec):
        events.append({
            "id": index + 1,
            "angle": math.radians(deg),
            "intensity": intensity,
            "confidence": 1.0,
            "type": sound_type,
            "timestamp": now - age,
            "channels": 8,
        })
    return events


def render(app, name, mode, hint_logic, spec, config=None, color_mode="red",
           now=None, caption=""):
    now = time.time() if now is None else now
    hud = HudWindow(lambda: make_events(now, spec))
    hud.mode = mode
    hud.hint_logic = hint_logic
    hud.color_mode = color_mode
    hud.lifetime = 1.4
    hud.config = config or preset_config("平衡")

    image = QImage(W, H, QImage.Format_ARGB32)
    image.fill(QColor(14, 18, 20, 255))
    painter = QPainter(image)
    hud.render_hud(painter, W, H, now=now)

    # 面板标题（预览图专用，实际 HUD 不画这些字）
    painter.setPen(QColor(150, 160, 165, 230))
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(16)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(16, 12, W - 32, 26), Qt.AlignLeft | Qt.AlignTop, caption)
    painter.end()

    path = os.path.join(OUT_DIR, "%s.png" % name)
    image.save(path)
    return path


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    now = time.time()

    panels = [
        ("hud_wave", "wave", "focus",
         [(45, 0.85, "footstep", 0.10)], None, "red", "音浪 HUD（波形条）· 专注提示 · 右前 45°"),
        ("hud_pointer", "pointer", "focus",
         [(-135, 0.85, "gunshot", 0.10)], None, "red", "指针 HUD（直线箭头）· 专注提示 · 左后 -135°"),
        ("hud_multi", "dual", "multi",
         [(-135, 0.55, "footstep", 0.30), (30, 0.95, "gunshot", 0.10),
          (90, 0.70, "door", 0.55)], None, "red", "双 HUD · 多方向提示 · 三个方向各自的波形 + 箭头"),
        ("hud_focus", "dual", "focus",
         [(-135, 0.35, "footstep", 0.60), (30, 0.95, "gunshot", 0.10),
          (90, 0.45, "door", 0.80)], None, "red", "双 HUD · 专注提示 · 只画最响（右前枪声）"),
    ]

    paths = [render(app, name, mode, logic, spec, cfg, color, now, caption)
             for name, mode, logic, spec, cfg, color, caption in panels]

    images = [QImage(path) for path in paths]
    gap = 10
    cols = 2
    rows = (len(images) + cols - 1) // cols
    sheet = QImage(W * cols + gap * (cols - 1), H * rows + gap * (rows - 1),
                   QImage.Format_ARGB32)
    sheet.fill(QColor(0, 0, 0, 255))
    painter = QPainter(sheet)
    for index, img in enumerate(images):
        x = (index % cols) * (W + gap)
        y = (index // cols) * (H + gap)
        painter.drawImage(x, y, img)
    painter.end()
    sheet_path = os.path.join(OUT_DIR, "hud_sheet.png")
    sheet.save(sheet_path)
    print("\n".join(paths + [sheet_path]))


if __name__ == "__main__":
    main()
