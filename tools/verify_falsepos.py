# -*- coding: utf-8 -*-
"""真实路径下的误报与抗干扰验证：静音 / 持续环境噪声 / 噪声里夹脚步。"""
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

CH = 8
FS = 48000
N = 1024
events = []
lock = threading.Lock()

analyzer = app.AudioAnalyzer(lambda ev: (lock.acquire(), events.append(ev), lock.release()))
assert analyzer.start(), analyzer.error
CH = analyzer.channels
FS = analyzer.sample_rate
print("设备 %s | %dch %dHz" % (analyzer.device_name, CH, FS))

p = pyaudio.PyAudio()
wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
out = p.open(format=pyaudio.paFloat32, channels=CH, rate=FS, output=True,
             output_device_index=wasapi["defaultOutputDevice"], frames_per_buffer=N)
out.start_stream()

rng = np.random.default_rng(5)
play_stop = threading.Event()


def pink_block(n, sigma):
    w = rng.normal(0, 1, n)
    # 一阶低通近似粉红
    acc = np.zeros(n)
    a = 0.0
    for i in range(n):
        a = 0.995 * a + 0.005 * w[i] * 12.0
        acc[i] = a
    out_block = acc * (sigma / (np.std(acc) + 1e-12))
    return out_block.astype(np.float32)


def noise_thread(sigma, ch_mask, extra=None):
    t0 = time.time()
    while not play_stop.is_set():
        block = np.zeros((N, CH), dtype=np.float32)
        tone = pink_block(N, sigma)
        for c in ch_mask:
            block[:, c] = tone
        if extra is not None:
            block += extra(len(block) if False else t0)
        out.write(block.tobytes())


def burst_adder(ch, every=0.5, amp=0.25):
    state = {"next": time.time() + 0.3}

    def feed(_t0):
        frames = N
        b = np.zeros((frames, CH), dtype=np.float32)
        now = time.time()
        if now >= state["next"]:
            state["next"] = now + every
            state["active"] = 8  # 8 块 ≈ 170ms
        left = state.get("active", 0)
        if left > 0:
            state["active"] = left - 1
            t = np.arange(frames) / FS
            env = np.exp(-np.linspace(0, 2, frames))
            b[:, ch] = (amp * env * np.sin(2 * math.pi * 650 * t)).astype(np.float32)
        return b
    return feed


def run(label, seconds, sigma, ch_mask, extra=None):
    with lock:
        events.clear()
    play_stop.clear()
    th = threading.Thread(target=noise_thread, args=(sigma, ch_mask, extra), daemon=True)
    th.start()
    time.sleep(seconds)
    play_stop.set()
    th.join(timeout=1.0)
    with lock:
        snap = list(events)
    angles = [math.degrees(e["angle"]) for e in snap]
    inten = [e["intensity"] for e in snap]
    sectors = {}
    for a in angles:
        s = sector_name(math.radians(a))
        sectors[s] = sectors.get(s, 0) + 1
    print("\n%s  (%.1fs, 噪声sigma=%.3f, 声道=%s)" % (label, seconds, sigma, ch_mask))
    print("  事件数 %d (%.1f 个/秒)  强度 中位 %s"
          % (len(snap), len(snap) / seconds,
             ("%.2f" % float(np.median(inten))) if inten else "-"))
    confs = [e["confidence"] for e in snap]
    if confs:
        kept = [e for e in snap if e["confidence"] >= 0.45]
        print("  conf 中位 %.2f | 加 conf>=0.45 门限后剩 %d 个 (%.1f 个/秒)"
              % (float(np.median(confs)), len(kept), len(kept) / seconds))
    if sectors:
        print("  方向分布:", ", ".join("%s×%d" % kv for kv in
                                     sorted(sectors.items(), key=lambda kv: -kv[1])))
    return angles


run("A 完全静音", 5.0, 0.0, ())
run("B 全声道持续环境噪声(-40dBFS)", 8.0, 0.01, range(CH))
run("C 只有前左/前右有环境噪声（模拟游戏环境声）", 8.0, 0.01, (0, 1))
_add = burst_adder(4)
run("D 全声道环境噪声 + 后左脚步(每0.5s)", 10.0, 0.01, range(CH), _add)

out.stop_stream()
out.close()
p.terminate()
analyzer.stop()
print("\n完成。")
