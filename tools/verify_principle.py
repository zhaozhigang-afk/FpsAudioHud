# -*- coding: utf-8 -*-
"""端到端原理验证：
A 段：真实声卡回路（默认 8ch 虚拟设备）—— 往单声道放 1kHz，看抓回来落在哪个声道、解出什么方向。
B 段：纯算法鲁棒性 —— 底噪 + 单声道脉冲、纯噪声误报、移动声源跟踪。
C 段：立体声声像模式。
输出为纯文本报告。
"""
import math
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyaudiowpatch as pyaudio
from audio_direction import DirectionEstimator, SourceTracker, channel_names, sector_name

FS = 48000
N = 1024
RATE = 48000
CH = 8


def tone(frames, ch_index, freq=1000.0, amp=0.3, channels=CH, fs=FS):
    t = np.arange(frames) / float(fs)
    data = np.zeros((frames, channels), dtype=np.float32)
    data[:, ch_index] = (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)
    return data


def band_rms(block, fs=FS):
    n = block.shape[0]
    win = np.hanning(n)
    spec = np.fft.rfft(block * win[:, None], axis=0)
    pw = (spec.real ** 2 + spec.imag ** 2).sum(axis=0)
    return np.sqrt(2.0 * pw / (n * float(np.sum(win ** 2))))


def section_a():
    print("=" * 74)
    print("A. 真实回路验证：8ch 输出单声道音 -> WASAPI 回环抓回 -> 方向计算")
    print("=" * 74)

    p = pyaudio.PyAudio()
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    out_dev = wasapi["defaultOutputDevice"]
    loop = p.get_default_wasapi_loopback()

    out = p.open(format=pyaudio.paFloat32, channels=CH, rate=RATE,
                 output=True, output_device_index=out_dev,
                 frames_per_buffer=N)
    cap = p.open(format=pyaudio.paFloat32, channels=CH, rate=RATE,
                 input=True, input_device_index=int(loop["index"]),
                 frames_per_buffer=N)
    out.start_stream()
    cap.start_stream()

    # 抓回数据的真实声道名，用能量核对（防止 VB-Cable 内部重排）
    names = channel_names(CH)
    print("设备: %s  |  回环: %s" % (p.get_device_info_by_index(out_dev)["name"],
                                     loop["name"]))
    print("假定声道顺序: %s\n" % ", ".join(names))

    hdr = "播放声道(假定位置) -> 抓回能量前3  解出角度  扇区"
    print(hdr)
    print("-" * len(hdr))

    results = []
    for ch in (0, 1, 2, 4, 5, 6, 7):
        capture_blocks = []
        stop = threading.Event()

        def capture():
            while not stop.is_set():
                try:
                    raw = cap.read(N, exception_on_overflow=False)
                except Exception:
                    continue
                capture_blocks.append(np.frombuffer(raw, dtype=np.float32).reshape(N, CH))

        thread = threading.Thread(target=capture, daemon=True)
        thread.start()
        time.sleep(0.05)
        payload = tone(N * 30, ch)  # 0.64s
        t0 = time.time()
        out.write(payload.tobytes())
        time.sleep(0.45)
        stop.set()
        thread.join(timeout=1.0)
        t_done = time.time()

        if not capture_blocks:
            print("ch%-2d %-10s 抓不到数据" % (ch, names[ch]))
            continue

        blocks = np.stack(capture_blocks)
        # 找出这一段里最响的块
        energy = np.array([float(np.sqrt(np.mean(b ** 2))) for b in blocks])
        peak_idx = int(np.argmax(energy))
        loud = blocks[max(0, peak_idx - 3): peak_idx + 3]
        rms_vec = band_rms(np.concatenate(loud, axis=0))
        order = np.argsort(rms_vec)[::-1][:3]
        top = ", ".join("%s=%.4f" % (names[i], rms_vec[i]) for i in order)

        est = DirectionEstimator(RATE, N)
        est.set_channels(CH)
        angles = []
        first_detect = None
        for i, b in enumerate(blocks):
            src = est.process(b)
            if src is not None:
                angles.append(math.degrees(src["angle"]))
                if first_detect is None:
                    first_detect = i
        if angles:
            med = float(np.median(angles))
            print("ch%-2d %-10s -> %-28s %+6.1f°  %s  (%d/%d 块检出)"
                  % (ch, names[ch], top, med, sector_name(math.radians(med)),
                     len(angles), len(blocks)))
            results.append((ch, med))
        else:
            print("ch%-2d %-10s -> %-28s 未检出" % (ch, names[ch], top))
    out.stop_stream()
    cap.stop_stream()
    out.close()
    cap.close()
    p.terminate()
    return results


def section_b():
    print("\n" + "=" * 74)
    print("B. 算法鲁棒性（不经过声卡，直接喂块）")
    print("=" * 74)
    rng = np.random.default_rng(7)

    # B1 底噪 + 后左单声道脉冲：噪声把方向往中间拉吗？
    est = DirectionEstimator(FS, N)
    est.set_channels(8)
    noise_amp = 0.01
    angles = []
    for i in range(120):
        b = rng.normal(0, noise_amp, (N, 8)).astype(np.float32)
        if 60 <= i < 80:  # 后左脚步
            env = np.hanning(N)[:, None]
            b[:, 4] += (0.25 * env[:, 0] * np.sin(2 * math.pi * 900 * np.arange(N) / FS)).astype(np.float32)
        src = est.process(b)
        if src is not None:
            angles.append(math.degrees(src["angle"]))
    if angles:
        print("B1 30dB信噪比 后左(BL)脚步: 中位 %+.1f° %s | 范围 %+.1f..%+.1f | 检出 %d/20 块"
              % (float(np.median(angles)), sector_name(math.radians(float(np.median(angles)))),
                 min(angles), max(angles), len(angles)))
    else:
        print("B1 未检出（异常）")

    # B2 纯噪声是否误报
    est = DirectionEstimator(FS, N)
    est.set_channels(8)
    false_pos = 0
    for i in range(200):
        b = rng.normal(0, 0.01, (N, 8)).astype(np.float32)
        if est.process(b) is not None:
            false_pos += 1
    print("B2 纯底噪误报: %d/200 块 (%.0f%%)" % (false_pos, false_pos / 2.0))

    # B3 移动声源：前左 -> 前右 扫过，检查跟踪是否单调、不跳变
    est = DirectionEstimator(FS, N)
    est.set_channels(8)
    tracker = SourceTracker()
    track_angles = []
    now = 0.0
    for i in range(80):
        g_left = max(0.0, 1.0 - i / 79.0)
        g_right = 1.0 - g_left
        t = np.arange(N) / FS
        b = np.zeros((N, 8), dtype=np.float32)
        b[:, 0] = (0.3 * g_left * np.sin(2 * math.pi * 800 * t)).astype(np.float32)
        b[:, 1] = (0.3 * g_right * np.sin(2 * math.pi * 800 * t)).astype(np.float32)
        src = est.process(b)
        now += N / FS
        for patch in tracker.update(src, now):
            track_angles.append(math.degrees(patch["angle"]))
    if track_angles:
        print("B3 前左->前右 扫过: 首 %+.0f° 末 %+.0f° 单调=%s"
              % (track_angles[0], track_angles[-1],
                 all(b >= a - 1e-6 for a, b in zip(track_angles, track_angles[1:]))))

    # B4 纯正面能量对比：多声道 vs 立体声模式，硬左信号
    b = np.zeros((N, 8), dtype=np.float32)
    t = np.arange(N) / FS
    b[:, 0] = (0.3 * np.sin(2 * math.pi * 800 * t)).astype(np.float32)
    b[:, 1] = b[:, 0] * 0.2
    a_auto = DirectionEstimator(FS, N); a_auto.set_channels(8)
    a_st = DirectionEstimator(FS, N); a_st.set_channels(8)
    print("B4 同一块(L强/R=0.2L): 多声道模式 %+.1f° | 立体声模式 %+.1f°"
          % (math.degrees(a_auto.process(b)["angle"]),
             math.degrees(a_st.process(b, stereo_pan=True)["angle"])))

    # B5 分类器：脚步 / 枪响 是否分得开
    from audio_direction import classify_sound
    print("B5 分类(脚步特征) =", classify_sound(0.02, 2.5, 900, 3),
          "| 分类(枪响特征) =", classify_sound(0.5, 5.0, 3000, 2))


def section_c():
    print("\n" + "=" * 74)
    print("C. 立体声（2ch）模式：声像反解角度表")
    print("=" * 74)
    est = DirectionEstimator(FS, N)
    est.set_channels(2)
    t = np.arange(N) / FS
    for wl, wr in ((1.0, 0.0), (0.8, 0.2), (0.5, 0.5), (0.2, 0.8), (0.0, 1.0)):
        b = np.zeros((N, 2), dtype=np.float32)
        b[:, 0] = (wl * 0.4 * np.sin(2 * math.pi * 900 * t)).astype(np.float32)
        b[:, 1] = (wr * 0.4 * np.sin(2 * math.pi * 900 * t)).astype(np.float32)
        src = est.process(b)
        if src:
            print("L=%.1f R=%.1f -> %+6.1f°  %s" % (wl, wr, math.degrees(src["angle"]),
                                                   sector_name(src["angle"])))


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
