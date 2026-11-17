# -*- coding: utf-8 -*-
"""误报率 / 检出率 / 置信度门限 / 端到端延迟 量化验证。"""
import math
import os
import sys
import time
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_direction import DirectionEstimator, sector_name  # noqa: E402

FS = 48000
N = 1024


def pink_noise(rng, frames, channels, sigma):
    """近似粉红噪声：白噪声经一阶低通叠加，避免纯白噪声不真实。"""
    white = rng.normal(0, 1, (frames, channels))
    out = np.empty_like(white)
    acc = np.zeros(channels)
    for i in range(frames):
        acc = 0.995 * acc + 0.005 * white[i]
        out[i] = acc * 6.0
    out *= sigma / (np.std(out) + 1e-12)
    return out.astype(np.float32)


def step_burst(frames, channels, ch_index, amp):
    t = np.arange(frames) / FS
    b = np.zeros((frames, channels), dtype=np.float32)
    env = np.exp(-np.linspace(0, 3, frames))
    b[:, ch_index] = (amp * env * np.sin(2 * math.pi * 700 * t)).astype(np.float32)
    return b


def run_sequence(noise_sigma, burst_amp, noise_blocks, burst_blocks, ch_index,
                 snr_threshold=1.8, confidence_gate=0.0, noise_factor=1.8):
    rng = np.random.default_rng(11)
    est = DirectionEstimator(FS, N)
    est.set_channels(8)
    est.NOISE_FACTOR = noise_factor
    est._history.clear()
    est.set_channels(8)

    detected_noise = 0
    detected_burst = 0
    angles_burst = []
    conf_burst = []
    angles_noise = []

    for i in range(noise_blocks + burst_blocks):
        if i < noise_blocks:
            b = pink_noise(rng, N, 8, noise_sigma)
        else:
            b = pink_noise(rng, N, 8, noise_sigma) + step_burst(N, 8, ch_index, burst_amp)
        src = est.process(b, snr_threshold=snr_threshold)
        if src is None:
            continue
        if src["confidence"] < confidence_gate:
            continue
        if i < noise_blocks:
            detected_noise += 1
            angles_noise.append(math.degrees(src["angle"]))
        else:
            detected_burst += 1
            angles_burst.append(math.degrees(src["angle"]))
            conf_burst.append(src["confidence"])
    return detected_noise, detected_burst, angles_burst, conf_burst, angles_noise


print("=" * 78)
print("底噪 -40dBFS(粉红) 的 8 声道环境，默认参数下的误报/检出")
print("=" * 78)
dn, db, ab, cb, an = run_sequence(0.01, 0.25, 300, 20, 4)
print("误报: %d/300 块 (%.1f%%)   后左脚步检出: %d/20 块" % (dn, dn / 3.0, db))
if ab:
    print("脚步角度 中位 %+.1f° (%s)  conf 中位 %.2f"
          % (float(np.median(ab)), sector_name(math.radians(float(np.median(ab)))),
             float(np.median(cb))))
if an:
    print("误报角度分布: 中位 %+.1f°, 跨度 %.0f°（越散说明方向是随机的）"
          % (float(np.median(an)), max(an) - min(an)))

print("\n" + "=" * 78)
print("参数扫描：误报率 vs 脚步检出率（噪声 -40dBFS，脚步 0.25）")
print("=" * 78)
print("%-28s %-10s %-10s %s" % ("设置", "误报率", "检出", "脚步角度中位"))
for snr_th, conf_gate in ((1.8, 0.0), (2.5, 0.0), (3.0, 0.0),
                          (1.8, 0.5), (1.8, 0.7), (2.5, 0.6)):
    dn, db, ab, cb, _an = run_sequence(0.01, 0.25, 300, 20, 4,
                                       snr_threshold=snr_th, confidence_gate=conf_gate)
    med = ("%+.1f°" % float(np.median(ab))) if ab else "-"
    print("snr>=%.1f conf>=%.1f%s %-9.1f%% %-10s %s"
          % (snr_th, conf_gate, " " * 8, dn / 3.0, "%d/20" % db, med))

print("\n" + "=" * 78)
print("正前/正后 靠双侧对称声道能不能分开")
print("=" * 78)
for chs, label in (((4, 5), "BL+BR 同时响"), ((0, 1), "FL+FR 同时响"),
                   ((4,), "只 BL"), ((5,), "只 BR")):
    rng = np.random.default_rng(3)
    est = DirectionEstimator(FS, N)
    est.set_channels(8)
    t = np.arange(N) / FS
    b = np.zeros((N, 8), dtype=np.float32)
    for c in chs:
        b[:, c] = (0.25 * np.sin(2 * math.pi * 700 * t)).astype(np.float32)
    src = est.process(b)
    if src:
        print("%-14s -> %+6.1f°  %s  conf=%.2f"
              % (label, math.degrees(src["angle"]), sector_name(src["angle"]),
                 src["confidence"]))

print("\n" + "=" * 78)
print("真实回路端到端延迟（写音频 -> 检测到方向）")
print("=" * 78)
try:
    import pyaudiowpatch as pyaudio
    p = pyaudio.PyAudio()
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    loop = p.get_default_wasapi_loopback()
    out = p.open(format=pyaudio.paFloat32, channels=8, rate=48000, output=True,
                 output_device_index=wasapi["defaultOutputDevice"],
                 frames_per_buffer=N)
    cap = p.open(format=pyaudio.paFloat32, channels=8, rate=48000, input=True,
                 input_device_index=int(loop["index"]), frames_per_buffer=N)
    out.start_stream(); cap.start_stream()
    est = DirectionEstimator(48000, N)
    est.set_channels(8)
    latencies = []
    for trial in range(5):
        blocks = []
        stop = threading.Event()

        def capture():
            while not stop.is_set():
                try:
                    raw = cap.read(N, exception_on_overflow=False)
                except Exception:
                    continue
                blocks.append((time.perf_counter(),
                               np.frombuffer(raw, dtype=np.float32).reshape(N, 8)))
        th = threading.Thread(target=capture, daemon=True); th.start()
        time.sleep(0.15)
        t0 = time.perf_counter()
        payload = np.zeros((N * 20, 8), dtype=np.float32)
        payload[:, 0] = (0.3 * np.sin(2 * math.pi * 1000 * np.arange(N * 20) / 48000)).astype(np.float32)
        out.write(payload.tobytes())
        time.sleep(0.35)
        stop.set(); th.join(timeout=1.0)
        # 找第一块 SNR 超过门限的
        hit = None
        rec = []
        for ts, b in blocks:
            src = est.process(b)
            rec.append((ts, src is not None))
            if src is not None and ts >= t0:
                hit = ts
                break
        if hit:
            latencies.append((hit - t0) * 1000.0)
        time.sleep(0.1)
    out.stop_stream(); cap.stop_stream(); out.close(); cap.close(); p.terminate()
    if latencies:
        print("实测延迟(写调用返回->首个方向块): %s ms，中位 %.1f ms"
              % (", ".join("%.1f" % v for v in latencies), float(np.median(latencies))))
        print("（含输出设备缓冲 + 21.3ms 分析窗；UI 另加 30ms 刷新）")
    else:
        print("没抓到（设备忙或音量为 0）")
except Exception as exc:
    print("延迟测试失败: %r" % (exc,))
