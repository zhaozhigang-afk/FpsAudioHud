# -*- coding: utf-8 -*-
"""枚举 WASAPI 设备与回环能力（验证"能抓到几声道"这一步）。"""
import json
import pyaudiowpatch as pyaudio

p = pyaudio.PyAudio()
wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
print("WASAPI hostApi index:", wasapi["index"],
      "| defaultOutputDevice:", wasapi["defaultOutputDevice"])

print("\n--- 所有设备 ---")
for i in range(p.get_device_count()):
    d = p.get_device_info_by_index(i)
    if d.get("hostApi") != wasapi["index"]:
        continue
    print("idx=%-3d in=%-2d out=%-2d rate=%-6d loopback=%-5s %s" % (
        d["index"], d.get("maxInputChannels", 0), d.get("maxOutputChannels", 0),
        int(d.get("defaultSampleRate", 0)),
        str(d.get("isLoopbackDevice", False)), d.get("name")))

print("\n--- 默认输出设备 ---")
try:
    d = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    print(json.dumps({k: d[k] for k in d if isinstance(d[k], (int, float, str, bool))},
                     ensure_ascii=False, indent=1))
except Exception as exc:
    print("ERR", exc)

print("\n--- 默认回环 ---")
try:
    d = p.get_default_wasapi_loopback()
    print(json.dumps({k: d[k] for k in d if isinstance(d[k], (int, float, str, bool))},
                     ensure_ascii=False, indent=1))
except Exception as exc:
    print("ERR", exc)

print("\n--- 各回环设备可打开声道数实测 ---")
for loop in p.get_loopback_device_info_generator():
    if loop.get("hostApi") != wasapi["index"]:
        continue
    idx = int(loop["index"])
    rate = int(loop.get("defaultSampleRate", 48000))
    ok = []
    for ch in (8, 6, 4, 2, 1):
        try:
            s = p.open(format=pyaudio.paFloat32, channels=ch, rate=rate,
                       input=True, input_device_index=idx,
                       frames_per_buffer=1024)
            s.stop_stream(); s.close()
            ok.append(ch)
        except Exception:
            pass
    print("idx=%-3d max_in=%-2d 可开:%s  %s" % (
        idx, loop.get("maxInputChannels", 0), ok, loop.get("name")))

p.terminate()
