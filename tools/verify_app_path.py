# -*- coding: utf-8 -*-
"""走真实代码路径：AudioAnalyzer(启动/回环/回调/跟踪) + 往指定声道放音，看收到的事件。"""
import math
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyaudiowpatch as pyaudio  # noqa: E402
import fps_audio_radar as app  # noqa: E402
from audio_direction import sector_name  # noqa: E402

events = []
lock = threading.Lock()


def on_event(ev):
    with lock:
        events.append(ev)


analyzer = app.AudioAnalyzer(on_event)
ok = analyzer.start()
print("AudioAnalyzer.start() ->", ok, "| error:", analyzer.error)
print("设备:", analyzer.device_name, "| channels:", analyzer.channels,
      "| rate:", analyzer.sample_rate)
if not ok:
    sys.exit(1)

p = pyaudio.PyAudio()
wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
out = p.open(format=pyaudio.paFloat32, channels=analyzer.channels,
             rate=analyzer.sample_rate, output=True,
             output_device_index=wasapi["defaultOutputDevice"],
             frames_per_buffer=1024)
out.start_stream()

n = 1024
names = ("FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR")


def play(ch, seconds=0.5, amp=0.3, freq=900.0):
    total = int(analyzer.sample_rate * seconds / n) + 1
    t = np.arange(n * total) / float(analyzer.sample_rate)
    data = np.zeros((n * total, analyzer.channels), dtype=np.float32)
    if ch is not None:
        data[:, ch] = (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)
    out.write(data.tobytes())


print("\n--- 自动模式（多声道布局）---")
for ch in (0, 1, 2, 4, 5, 6, 7):
    with lock:
        events.clear()
    time.sleep(0.1)
    play(ch)
    time.sleep(0.15)
    with lock:
        snap = list(events)
    if not snap:
        print("%-3s -> 没有事件" % names[ch])
        continue
    last = snap[-1]
    angles = [math.degrees(e["angle"]) for e in snap]
    print("%-3s -> %d 个事件, 最后角度 %+6.1f° %s, 类型=%s, intensity=%.2f, conf=%.2f"
          % (names[ch], len(snap), angles[-1],
             sector_name(math.radians(angles[-1])), last["type"],
             last["intensity"], last["confidence"]))

print("\n--- 输入模式=立体声（多声道设备上只按 FL/FR 声像）---")
app.CONFIG["input_mode"] = "stereo"
for ch in (0, 1):
    with lock:
        events.clear()
    time.sleep(0.1)
    play(ch)
    time.sleep(0.15)
    with lock:
        snap = list(events)
    if snap:
        a = math.degrees(snap[-1]["angle"])
        print("%-3s -> %+6.1f° %s" % (names[ch], a, sector_name(math.radians(a))))
    else:
        print("%-3s -> 没有事件" % names[ch])
app.CONFIG["input_mode"] = "auto"

print("\n--- 静音 2 秒，看有没有误报（噪声门/阈值）---")
with lock:
    events.clear()
time.sleep(2.0)
with lock:
    snap = list(events)
print("静音期间事件数:", len(snap),
      ("| 角度: " + ", ".join("%+.0f°" % math.degrees(e["angle"]) for e in snap[:8])) if snap else "")
if analyzer.estimator.levels.size:
    print("各声道电平:", " ".join("%s=%.5f" % (names[i], v)
                                  for i, v in enumerate(analyzer.estimator.levels)))

out.stop_stream()
out.close()
p.terminate()
analyzer.stop()
print("\n已关闭。")
