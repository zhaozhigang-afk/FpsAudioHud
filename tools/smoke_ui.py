# -*- coding: utf-8 -*-
"""
开发用：不显示窗口，把「全部设置」的三个页签和电平表各渲染一张图，
用于确认界面代码没有报错、布局正常。

运行：python tools/smoke_ui.py
"""

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter, QColor

import fps_audio_radar as appmod

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preview")


def render(widget, path, background=QColor(18, 22, 20, 255)):
    widget.resize(widget.sizeHint().expandedTo(widget.size()))
    image = QImage(widget.size(), QImage.Format_ARGB32)
    image.fill(background)
    painter = QPainter(image)
    widget.render(painter)
    painter.end()
    image.save(path)
    return path


class FakeController:
    """模拟"8 声道设备，但只有前左/前右有声音"。"""

    running = True
    channels = 8
    sample_rate = 48000
    device_name = "Fake 7.1 [Loopback]"
    device_index = 9
    devices = [(9, "Fake 7.1 [Loopback]", 8), (12, "CABLE In 16ch", 8)]
    last_error = ""

    LEVELS = np.array([0.05, 0.04, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)

    def levels_snapshot(self):
        return self.LEVELS.copy()

    def activity_snapshot(self):
        return self.LEVELS.copy()

    def estimator_channels(self):
        return 8

    def restart(self, index=None):
        return True

    def set_preview(self, angle, hold=3.0):
        self.preview = angle


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)

    controller = FakeController()
    hud = appmod.HudWindow(lambda: [])
    hud.config = hud.config.clone(global_alpha=0.85, center_dot_size=4)

    window = appmod.SettingsWindow(controller, hud)
    window.show()
    window._update_diagnosis()
    print("diag:", window.diag.text().split("\n")[0])

    for index, name in enumerate(("ui_settings.png", "ui_hud_settings.png", "ui_device.png")):
        window.tabs.setCurrentIndex(index)
        print(render(window, os.path.join(OUT_DIR, name)))

    meter = appmod.ChannelMeter(controller)
    meter.resize(330, 158)
    print(render(meter, os.path.join(OUT_DIR, "ui_meter.png")))

    print("ok")


if __name__ == "__main__":
    main()
