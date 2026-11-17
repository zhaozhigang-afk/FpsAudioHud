# -*- coding: utf-8 -*-
"""延迟拆解：音频域延迟(块索引) vs 墙钟延迟，判断 200ms 是驱动缓冲还是程序问题。"""
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_direction import DirectionEstimator  # noqa: E402
import pyaudiowpatch as pyaudio  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1024
FS = 48000
BLOCK_MS = N * 1000.0 / FS

p = pyaudio.PyAudio()
wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
loop = p.get_default_wasapi_loopback()
out = p.open(format=pyaudio.paFloat32, channels=8, rate=FS, output=True,
             output_device_index=wasapi["defaultOutputDevice"], frames_per_buffer=N)
cap = p.open(format=pyaudio.paFloat32, channels=8, rate=FS, input=True,
             input_device_index=int(loop["index"]), frames_per_buffer=N)
out.start_stream()
cap.start_stream()

# 丢弃启动阶段的缓冲
for _ in range(20):
    cap.read(N, exception_on_overflow=False)

print("块长 %.1f ms" % BLOCK_MS)
for trial in range(5):
    blocks = []
    t_write = None
    # 写一个 0.2s 的 FL 单声道音（3 个块），记录写调用前后的墙钟
    payload = np.zeros((N * 3, 8), dtype=np.float32)
    payload[:, 0] = (0.4 * np.sin(2 * math.pi * 1000 * np.arange(N * 3) / FS)).astype(np.float32)
    t_before = time.perf_counter()
    out.write(payload.tobytes())
    t_after = time.perf_counter()

    t_end = time.time() + 0.8
    while time.time() < t_end:
        raw = cap.read(N, exception_on_overflow=False)
        blocks.append((time.perf_counter(),
                       np.frombuffer(raw, dtype=np.float32).reshape(N, 8)))

    base = np.array([float(np.sqrt(np.mean(b[:, 0] ** 2))) for _ts, b in blocks])
    thr = max(0.02, float(np.max(base)) * 0.3)
    hit = None
    for i, (ts, _b) in enumerate(blocks):
        if base[i] > thr:
            hit = i
            break
    if hit is None:
        print("试次 %d: 没抓到" % trial)
        continue
    t_hit = blocks[hit][0]
    # 音频域：命中块序号 * 块长（从本轮第一块开始算）
    audio_ms = hit * BLOCK_MS
    wall_ms = (t_hit - t_after) * 1000.0
    write_block_ms = (t_after - t_before) * 1000.0
    print("试次 %d: 第 %d 块命中 | 音频域 %.0f ms | 墙钟(写返回后) %.0f ms | write() 本身耗时 %.1f ms"
          % (trial, hit, audio_ms, wall_ms, write_block_ms))

out.stop_stream(); cap.stop_stream(); out.close(); cap.close(); p.terminate()
