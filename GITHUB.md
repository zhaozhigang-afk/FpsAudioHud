# GitHub 仓库描述素材（v6）

> 直接复制下面各段到 GitHub 对应位置：仓库名 / About / Topics / Release / 社交预览。
> 项目当前形态：**只有全屏 HUD**（边缘声音波形条 + 中心直线箭头 + 准星红色中心点），无小雷达、无文字、参数可调且自动保存。

---

## 1. 仓库名（Repository name）

| 建议 | 说明 |
|---|---|
| `fps-audio-hud` | 推荐。名字和现在的形态一致（只有 HUD 了） |
| `fps-audio-radar` | 沿用旧名，历史链接不断，但和"雷达"这个词已经不太贴 |

## 2. About（仓库简介，一句话）

**中文（约 60 字）**

> 给听障玩家的 FPS 声音方向 HUD：屏幕四边声音波形条 + 准星处直线箭头，WASAPI 回环，支持立体声/5.1/7.1 与多声源提示。

**English（about 190 chars）**

> Screen-edge audio waveform bars + a straight direction arrow at the crosshair for hearing-impaired FPS players. WASAPI loopback, stereo/5.1/7.1, focus & multi-source hints.

**更短（若字数受限）**

> 听障 FPS 声音方向 HUD：边缘波形条 + 准星箭头，WASAPI 回环，支持 5.1/7.1。

## 3. 仓库详细描述（README 顶部或 repo description 长版）

**中文**

> 实时捕获 Windows 系统输出音频（WASAPI loopback），分析脚步声、枪声等声音的方位，用铺满屏幕的透明 HUD 显示出来：屏幕四边和四个角是 12 段**声音波形条**，屏幕正中（准星位置）是一根**直线箭头**指向声源，准星处还有一个可调的**红色中心点**。
>
> 支持立体声 / 4.0 / 5.1 / 6.1 / 7.1；两种提示逻辑——**专注提示**只画最响的一个方向，**多方向提示**最多同时给出 4~6 个真实方向（按声道聚类，不会把"左后 + 正前"平均成假方向）。HUD 铺满主屏、置顶、鼠标穿透，检测到前台全屏游戏时自动取消置顶；整体透明与元素不透明度可分别调整。
>
> 只读系统音频，不接触游戏进程、不读内存、不发送输入。定位是无障碍辅助工具（听障玩家），不是外挂。纯 Python（PyQt5 + numpy + PyAudioWPatch），单文件 EXE 可直接运行。

**English**

> Captures Windows system audio (WASAPI loopback), estimates where footsteps, gunshots and other cues come from, and shows that direction on a transparent full-screen HUD: 12 sound-waveform bar clusters hugging the screen edges, plus a straight arrow at the crosshair with a small red centre dot. Supports stereo / 4.0 / 5.1 / 6.1 / 7.1, with a focus mode (loudest source only) and a multi-source mode (up to 4–6 real directions, clustered per channel so "back-left + front-right" stays two directions). The overlay is always-on-top, click-through, and backs off when a fullscreen game is in the foreground. Global and per-element transparency are adjustable independently.
>
> Audio-only: it never touches the game process, memory or input. Built as an accessibility aid for hearing-impaired players. Pure Python (PyQt5 + numpy + PyAudioWPatch), shipped as a single EXE.

## 4. Topics（GitHub 标签，直接粘贴）

```text
fps
audio
accessibility
hearing-impaired
sound-localization
wasapi
loopback
pyqt5
python
windows
hud
overlay
surround-sound
7-1-surround
deaf
hard-of-hearing
audio-visualization
real-time
desktop-app
```

## 5. 项目亮点（Release / 推文 / 介绍用）

- **只读音频**：WASAPI loopback 抓系统输出，不碰游戏进程，不读内存、不发输入。
- **边缘波形条**：12 段贴边柱状波形，声音从哪边来，那边的柱子就跳；斜方向两个角落段一起亮。
- **准星箭头**：屏幕正中刻度盘 + 直线箭头（箭杆 + 实心三角头），方位一眼可读，**HUD 上没有文字**。
- **红色中心点**：固定在屏幕正中，大小 1~8 px 可调，方便对齐准星。
- **两种提示逻辑**：专注提示（只给最响的方向）/ 多方向提示（最多 6 个方向，方位近的自动合并）。
- **多声道**：立体声 / 4.0 / 5.1 / 6.1 / 7.1；立体声按等功率声像反解，多声道按能量矢量求和。
- **不挡操作**：全屏透明、置顶、鼠标穿透；独占全屏游戏时自动取消置顶。
- **参数齐全**：统一设置窗口（音频 / HUD / 设备三页签）、整体透明与元素不透明度分离、一键预设、方向自检、声道电平表 + 自动诊断。
- **轻量**：1920×1080 全屏重绘约 3.4 ms/帧；54 项单元测试（20 方向 + 34 HUD）。

## 6. 社交预览（Social preview）

- 图片：`preview/hud_sheet.png`（四种形态拼图：波形条 / 箭头 / 多方向 / 专注提示）。
- 标题：**FPS 听障辅助音频 HUD**
- 描述：屏幕边缘波形条 + 准星直线箭头的声音方向 HUD。

## 7. 徽章（README 顶部可粘贴）

```markdown
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![UI](https://img.shields.io/badge/UI-PyQt5-green)
![Audio](https://img.shields.io/badge/audio-WASAPI%20loopback-informational)
![Tests](https://img.shields.io/badge/tests-54%20passed-brightgreen)
```

## 8. Release v6 文案模板

**标题**：v6 — 全部设置窗口 · 整体透明 · 去文字

**正文**：

```markdown
## 新增
- 统一「全部设置」窗口：音频 / HUD / 设备与自检三个页签，改动即时生效
- 整体透明（20%~100%）与元素不透明度分离，HUD 可调到几乎不抢眼
- 设置自动保存到 %APPDATA%\FPSAudioHUD\settings.json，启动自动套用
- 准星红色中心点（1~8 px，可关闭）

## 改进
- HUD 去掉全部文字，方位只靠波形条和箭头表达
- 设备页可直接切换音频设备、刷新设备列表
- 托盘快捷项与设置窗口双向同步，关窗 / 退出 / 改快捷项都会写盘

## 移除
- 小雷达窗口与雷达形状（圆形 / 半圆 / 水平条）、雷达光点与方位标注
- 旧的「音频设置」「HUD 设置」两个独立对话框（并入全部设置）

## 验证
- python test_direction.py：20 项通过
- python test_hud.py：34 项通过
- 1080p 全屏重绘约 3.4 ms/帧；打包 EXE 启动只创建 1 个全屏穿透窗口
```

## 9. 关键词（SEO / 搜索）

中文：听障、无障碍、FPS、声音方向、脚步方向、方位提示、音频雷达、波形条、HUD、悬浮窗、WASAPI、回环、5.1、7.1、多声道。
English: hearing impaired, accessibility, FPS, sound direction, footstep localization, audio radar, waveform, HUD overlay, WASAPI loopback, surround sound, 5.1, 7.1, click-through overlay, PyQt5.

## 10. README 首段（可直接替换现有简介）

> 给听障玩家的 FPS 游戏声音方向辅助工具：实时捕获系统输出音频，用铺满屏幕的 HUD 显示声源方位——
> 屏幕四边是 12 段**声音波形条**，屏幕正中是**直线箭头**，准星处还有一个固定的**红色中心点**。
> 无文字、鼠标穿透、不挡操作；支持立体声 / 4.0 / 5.1 / 6.1 / 7.1，专注提示与多方向提示可切换。
