# -*- coding: utf-8 -*-
"""
FPS 音频雷达 —— HUD 音浪 / 指针的几何与状态（纯函数 + dataclass，无 Qt 依赖）

这一层只做「角度 → 屏幕上的东西」，与 audio_direction.py 的分工一样：
    audio_direction.py   一块音频 → 方向 / 多声源分量
    hud_geometry.py      方向 + 强度 → 12 段边缘音浪 / 中心指针 / 提示选择
    hud_paint.py         把上面算出来的几何画出来（PyQt5）

角度约定与全工程一致：0=正前（屏幕上方），+π/2=正右，±π=正后。

两种提示逻辑（托盘「提示逻辑」菜单）
------------------------------------
专注提示 focus
    只保留最响的那一个声源，指针加粗、加锁定环，其它声源不画。
    —— 脚步声乱成一团时只给出"最该看的方向"，不干扰瞄准。
多方向提示 multi
    同时画出最多 max_sources 个声源，每个声源一根指针 + 自己那侧的边缘音浪，
    方位角太近的会合并（避免两根指针叠在一起）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from audio_direction import clamp, wrap_angle

HALF_PI = 0.5 * math.pi

# ============================ 常量表 ============================
# 12 段屏幕边缘音浪：四条边 + 四个角各两段（横段贴边、竖段贴边）。
# 这是 ShengWen HUD 的段表结构（PointerHud._segment_geometries）：
#     top / bottom / left / right + *_left_h|*_left_v ...
SEGMENT_KEYS = (
    "top", "bottom", "left", "right",
    "top_left_h", "top_left_v", "top_right_h", "top_right_v",
    "bottom_left_h", "bottom_left_v", "bottom_right_h", "bottom_right_v",
)

# 每段对应的方向（度），和八方向扇区一致；角落段的横/竖两段共用一个角度。
SEGMENT_ANGLES_DEG = {
    "top": 0.0, "bottom": 180.0, "left": -90.0, "right": 90.0,
    "top_left_h": -45.0, "top_left_v": -45.0,
    "top_right_h": 45.0, "top_right_v": 45.0,
    "bottom_left_h": -135.0, "bottom_left_v": -135.0,
    "bottom_right_h": 135.0, "bottom_right_v": 135.0,
}

# 显示模式：音浪 / 指针 / 双 HUD / 关闭
HUD_MODES = ("off", "wave", "pointer", "dual")
MODE_NAMES = {"off": "关闭", "wave": "音浪", "pointer": "指针", "dual": "双 HUD"}

# 提示逻辑
HINT_LOGICS = ("focus", "multi")
HINT_NAMES = {"focus": "专注提示", "multi": "多方向提示"}

# 单色模式下的可选颜色（按声音类型着色时用 SOUND_TYPES 的调色板）
HUD_COLORS = {
    "red": (239, 68, 68),
    "orange": (255, 152, 0),
    "cyan": (0, 229, 255),
    "green": (102, 187, 106),
    "white": (238, 240, 242),
}
HUD_COLOR_OPTIONS = (
    ("红色（默认）", "red"),
    ("按声音类型", "type"),
    ("橙色", "orange"),
    ("青色", "cyan"),
    ("绿色", "green"),
    ("白色", "white"),
)


# ============================ 配置 ============================
@dataclass
class HudConfig:
    """HUD 视觉参数；所有尺寸都用屏幕短边/宽高的比例，换分辨率不用改代码。"""

    size: float = 1.0            # 整体缩放 0.7 ~ 1.4
    opacity: float = 0.82        # 元素不透明度（波形条/箭头本身）0.15 ~ 1.0
    global_alpha: float = 1.0    # 整体透明：整块 HUD（含刻度盘/红点）统一乘这个系数
    glow: float = 0.55           # 外发光强度 0 ~ 1.2
    idle_opacity: float = 0.18   # 未激活时的细底线
    edge_ratio: float = 0.20     # 边缘段长度（占屏宽/屏高）
    corner_ratio: float = 0.13   # 角落段长度
    thickness: float = 0.055     # 横段厚度（占屏幕短边）
    thickness_v: float = 0.068   # 竖段厚度
    pointer_radius: float = 0.17 # 指针盘半径（占屏幕短边）
    pointer_width: float = 0.13  # 指针锥宽度（占指针盘半径）
    pointer_ticks: int = 8       # 刻度数量
    show_edges: bool = True      # 显示边缘音浪
    show_pointer: bool = True    # 显示中心指针
    focus_ring: bool = True      # 专注提示时画锁定环
    center_dot: bool = True      # 准星位置那个红色中心点
    center_dot_size: float = 3.0 # 中心点半径（像素，会再乘 size）
    max_sources: int = 4         # 多方向提示最多同时显示几个
    pulse_speed: float = 0.85    # 音浪脉动速度（周期/秒）

    def clone(self, **changes):
        return replace(self, **changes)


# 三档提示档位
HUD_PRESETS = {
    "轻提示": HudConfig(size=0.85, opacity=0.55, glow=0.28, max_sources=3),
    "平衡": HudConfig(),
    "明显": HudConfig(size=1.15, opacity=0.95, glow=0.85, max_sources=5,
                     thickness=0.062, thickness_v=0.075),
}

HUD_PRESET_NAMES = tuple(HUD_PRESETS.keys())


def preset_config(name):
    """取一档预设的副本（改副本不会污染预设）。"""
    return HUD_PRESETS.get(name, HUD_PRESETS["平衡"]).clone()


def mode_shows_edges(mode):
    return mode in ("wave", "dual")


def mode_shows_pointer(mode):
    return mode in ("pointer", "dual")


# ============================ 基础几何 ============================
def smoothstep(t):
    t = clamp(float(t), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def angle_delta_deg(a, b):
    """两个角度的最小夹角（度，0~180）。"""
    return abs(math.degrees(wrap_angle(float(a) - float(b))))


def segment_weight(key, angle, span=45.0):
    """某一段被某个方向点亮的权重：方向与段中心越接近越亮，45° 外为 0。"""
    center = math.radians(SEGMENT_ANGLES_DEG[key])
    delta = angle_delta_deg(angle, center)
    if delta >= float(span):
        return 0.0
    return 1.0 - smoothstep(delta / float(span))


def segment_weights(angle, span=45.0):
    """8 个方向扇区 → 12 段权重表。"""
    return {key: segment_weight(key, angle, span) for key in SEGMENT_KEYS}


def level_color(level):
    """能级 0~1 → 绿 → 红 的渐变色（指针/音浪的"响度"配色）。"""
    t = clamp(float(level), 0.0, 1.0)
    low = (102, 187, 106)
    high = (239, 83, 80)
    return (round(low[0] + (high[0] - low[0]) * t),
            round(low[1] + (high[1] - low[1]) * t),
            round(low[2] + (high[2] - low[2]) * t))


# ============================ 边缘段布局 ============================
@dataclass(frozen=True)
class SegmentRect:
    key: str
    x: float
    y: float
    width: float
    height: float
    orientation: str      # "h" 水平长段 / "v" 垂直长段
    angle_deg: float

    @property
    def rect(self):
        return (self.x, self.y, self.width, self.height)

    @property
    def center(self):
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


def build_segments(width, height, config=None):
    """
    按屏幕尺寸生成 12 段边缘音浪（贴着屏幕四边，不挡准星）。

    段长度用屏宽/屏高比例；厚度用屏幕短边比例。四个角各两段：一段贴顶/底，
    一段贴左/右，斜方向的声音两段一起亮，看起来就是"从那个角冒出来"。
    """
    cfg = config or HudConfig()
    w = max(2.0, float(width))
    h = max(2.0, float(height))
    scale = max(0.4, float(cfg.size))
    short = min(w, h) * scale

    th_h = max(4.0, short * cfg.thickness)
    th_v = max(4.0, short * cfg.thickness_v)
    edge_h = w * cfg.edge_ratio * scale
    edge_v = h * cfg.edge_ratio * scale
    corner_h = w * cfg.corner_ratio * scale
    corner_v = h * cfg.corner_ratio * scale

    def span(total, length):
        """居中放置，返回 (起点, 长度)。"""
        length = min(float(total), max(8.0, float(length)))
        start = (total - length) / 2.0
        return start, length

    top_x, top_len = span(w, edge_h)
    left_y, left_len = span(h, edge_v)

    segments = [
        SegmentRect("top", top_x, 0.0, top_len, th_h, "h", 0.0),
        SegmentRect("bottom", top_x, h - th_h, top_len, th_h, "h", 180.0),
        SegmentRect("left", 0.0, left_y, th_v, left_len, "v", -90.0),
        SegmentRect("right", w - th_v, left_y, th_v, left_len, "v", 90.0),
        # 左上角
        SegmentRect("top_left_h", 0.0, 0.0, corner_h, th_h, "h", -45.0),
        SegmentRect("top_left_v", 0.0, 0.0, th_v, corner_v, "v", -45.0),
        # 右上角
        SegmentRect("top_right_h", w - corner_h, 0.0, corner_h, th_h, "h", 45.0),
        SegmentRect("top_right_v", w - th_v, 0.0, th_v, corner_v, "v", 45.0),
        # 左下角
        SegmentRect("bottom_left_h", 0.0, h - th_h, corner_h, th_h, "h", -135.0),
        SegmentRect("bottom_left_v", 0.0, h - corner_v, th_v, corner_v, "v", -135.0),
        # 右下角
        SegmentRect("bottom_right_h", w - corner_h, h - th_h, corner_h, th_h, "h", 135.0),
        SegmentRect("bottom_right_v", w - th_v, h - corner_v, th_v, corner_v, "v", 135.0),
    ]
    return segments


# ============================ 边缘波形条 ============================
def _noise01(seed):
    """确定性伪随机 0~1（同一段、同一根条永远得到同一个数，不会闪）。"""
    value = math.sin(float(seed) * 12.9898) * 43758.5453
    return value - math.floor(value)


def _seed_for(key):
    return sum((index + 1) * ord(char) for index, char in enumerate(str(key))) % 997


def waveform_bars(seg, level=1.0, weight=1.0, phase=0.0,
                  min_ratio=0.16, gap_ratio=0.42):
    """
    把一段贴边条拆成一排"声音波形条"（柱状电平）。

    返回 [(x, y, w, h, amp), ...]，坐标是屏幕像素，绘制层直接画圆角小柱。

    规则：
      · 条数由段长决定（约每 3.2 倍厚度一根，限制在 4~40 根）；
      · 每根都从屏幕边往屏幕内侧长，长度 = 厚度 × 幅度；
      · 幅度 = 中间高两端低的包络 × 每根固定的随机形状 × 随时间走的正弦 × 响度，
        所以它一直在"跳"，但相邻两根不会完全一样，看起来才像波形而不是一块板；
      · 完全没声音时也会留一点点底噪高度（min_ratio），边缘不会突然消失。
    """
    x, y, w, h = float(seg.x), float(seg.y), float(seg.width), float(seg.height)
    horizontal = seg.orientation == "h"
    length = w if horizontal else h
    thickness = h if horizontal else w
    if length <= 2.0 or thickness <= 2.0:
        return []

    # 柱距 ~0.42 倍厚度：1080p 下大约 12~13px 一根，一段 10~16 根，才像波形条
    pitch = max(3.0, thickness * 0.42)
    count = int(clamp(round(length / pitch), 10, 60)) or 10
    slot = length / count
    bar = max(1.6, slot * (1.0 - clamp(gap_ratio, 0.0, 0.8)))

    level = clamp(float(level), 0.0, 1.0)
    weight = clamp(float(weight), 0.0, 1.0)
    # 响度 × 该方向占的权重：只有真正被这个方向点亮时柱子才跳起来
    drive = clamp(0.12 + 0.88 * level * weight, 0.0, 1.0)
    seed = _seed_for(seg.key)

    bars = []
    for index in range(count):
        t = (index + 0.5) / count                          # 0..1 沿段长
        envelope = math.sin(math.pi * t) ** 0.55           # 中间高、两端低
        noise = 0.40 + 0.60 * _noise01(seed + index * 7.31)
        wave = 0.5 + 0.5 * math.sin(2.0 * math.pi * (phase + t * 1.7 + noise * 0.6))
        amp = envelope * (0.35 + 0.65 * noise) * (0.55 + 0.45 * wave) * drive
        amp = clamp(amp, min_ratio * drive, 1.0)
        # 柱子最多能长到 ~1.9 倍厚度，这样"波形"才跳得起来，而不是一条细线
        grow = max(2.0, thickness * amp * 1.9)

        if horizontal:
            bx = x + index * slot + (slot - bar) / 2.0
            by = y if y <= 0.5 else y + h - grow
            bars.append((bx, by, bar, grow, amp))
        else:
            by = y + index * slot + (slot - bar) / 2.0
            bx = x if x <= 0.5 else x + w - grow
            bars.append((bx, by, grow, bar, amp))
    return bars


# ============================ 声源选择 ============================
@dataclass
class HudSource:
    """HUD 上要画的一个声源。"""

    angle: float          # 弧度，0=正前
    level: float          # 0~1 显示强度（已含淡出）
    intensity: float      # 0~1 原始强度
    fade: float           # 0~1 淡出系数
    sound_type: str = "other"
    track_id: int = 0


@dataclass
class HudFrame:
    """一帧 HUD 需要的全部数据。"""

    sources: list
    segments: dict        # key -> SegmentState
    phase: float          # 0~1 脉动相位
    now: float
    hint_logic: str = "focus"

    @property
    def dominant(self):
        return self.sources[0] if self.sources else None

    @property
    def active(self):
        return bool(self.sources)


@dataclass(frozen=True)
class SegmentState:
    weight: float       # 0~1 点亮程度
    level: float        # 0~1 该方向的响度
    angle: float        # 来源角度（弧度）
    sound_type: str = "other"   # 按声音类型着色时用


def pick_focus(sources):
    """专注提示：只留最响的一个（同样响时留更新的）。"""
    if not sources:
        return []
    best = max(sources, key=lambda s: (s.intensity, s.level, s.fade))
    return [best]


def pick_multi(sources, limit=4, min_sep_deg=25.0):
    """
    多方向提示：按强度排序取前 limit 个，方位太近的合并。

    合并阈值默认 25°：两根指针如果只差几度，画出来会重叠，
    反而看不清是"一个声音"还是"两个声音"。
    """
    limit = max(1, int(limit))
    kept = []
    for source in sorted(sources, key=lambda s: (-s.intensity, -s.level)):
        if any(angle_delta_deg(source.angle, other.angle) < float(min_sep_deg)
               for other in kept):
            continue
        kept.append(source)
        if len(kept) >= limit:
            break
    return kept


def aggregate_segments(sources, span=45.0):
    """多个声源 → 12 段权重表（每段取把它点得最亮的那个声源）。"""
    segments = {}
    for key in SEGMENT_KEYS:
        best = None
        for source in sources:
            weight = segment_weight(key, source.angle, span)
            if weight <= 0.0:
                continue
            # 响的声源更容易把边缘点亮；权重只用于"亮不亮"，响度单独给颜色
            if best is None or weight > best.weight:
                best = SegmentState(weight=weight, level=source.level,
                                    angle=source.angle, sound_type=source.sound_type)
        if best is not None and best.weight > 0.02:
            segments[key] = best
    return segments


def build_frame(events, now, lifetime=1.4, config=None, hint_logic="focus"):
    """
    事件列表 + 当前时间 → 一帧 HUD 状态。

    events 里每个元素来自 AudioController.snapshot()：
        {"id", "angle", "intensity", "type", "timestamp", ...}
    """
    cfg = config or HudConfig()
    lifetime = max(0.05, float(lifetime))
    sources = []
    for event in events or ():
        try:
            angle = float(event["angle"])
            stamp = float(event.get("timestamp", now))
        except (KeyError, TypeError, ValueError):
            continue
        age = now - stamp
        if age < 0.0 or age >= lifetime:
            continue
        fade = 1.0 - age / lifetime
        intensity = clamp(float(event.get("intensity", 0.4)), 0.0, 1.0)
        sources.append(HudSource(
            angle=wrap_angle(angle),
            level=clamp(intensity * (0.30 + 0.70 * fade), 0.0, 1.0),
            intensity=intensity,
            fade=fade,
            sound_type=str(event.get("type", "other")),
            track_id=int(event.get("id", 0) or 0),
        ))

    if hint_logic == "multi":
        selected = pick_multi(sources, cfg.max_sources)
    else:
        selected = pick_focus(sources)

    return HudFrame(
        sources=selected,
        segments=aggregate_segments(selected),
        phase=(now * max(0.05, cfg.pulse_speed)) % 1.0,
        now=now,
        hint_logic=hint_logic,
    )


# ============================ 指针几何（直线箭头）============================
def arrow_geometry(angle, cx, cy, radius, strength=1.0, length_scale=1.0,
                   start_ratio=0.20, head_ratio=0.17, head_width_ratio=0.10):
    """
    中心指针改成"直线箭头"：

        start       箭杆起点（离圆心 start_ratio × 半径，不会压住准星）
        tip         箭头尖端
        head_base   箭头底边中点（箭杆画到这里为止）
        head_left / head_right  箭头底边两个尖角

    返回的都是屏幕像素坐标；绘制层画一条线 + 一个实心三角头。
    """
    a = wrap_angle(float(angle))
    sin_a, cos_a = math.sin(a), math.cos(a)
    radius = max(6.0, float(radius))
    strength = clamp(float(strength), 0.0, 1.0)
    scale = max(0.2, float(length_scale))

    reach = radius * (0.58 + 0.42 * strength) * scale
    start_d = max(4.0, radius * float(start_ratio) * scale)
    head_len = max(6.0, radius * float(head_ratio) * scale)
    head_d = max(start_d + 2.0, reach - head_len)
    half = max(3.0, radius * float(head_width_ratio) * scale)

    def along(distance):
        return (cx + sin_a * distance, cy - cos_a * distance)

    tip = along(reach)
    head_base = along(head_d)
    start = along(start_d)
    head_left = (head_base[0] + math.sin(a - HALF_PI) * half,
                 head_base[1] - math.cos(a - HALF_PI) * half)
    head_right = (head_base[0] + math.sin(a + HALF_PI) * half,
                  head_base[1] - math.cos(a + HALF_PI) * half)
    return {
        "center": (cx, cy),
        "start": start,
        "tip": tip,
        "head_base": head_base,
        "head_left": head_left,
        "head_right": head_right,
        "length": reach,
        "radius": radius,
        "angle": a,
        "head_width": half * 2.0,
    }


def pointer_radius(width, height, config=None):
    cfg = config or HudConfig()
    return max(24.0, min(width, height) * cfg.pointer_radius * cfg.size)


def dial_ticks(cx, cy, radius, count=8, inner=0.86, outer=1.0):
    """指针盘刻度线（每 45° 一根），返回 [(x0,y0,x1,y1), ...]。"""
    count = max(1, int(count))
    ticks = []
    for index in range(count):
        theta = index * (2.0 * math.pi / count)
        sin_t, cos_t = math.sin(theta), math.cos(theta)
        ticks.append((
            cx + sin_t * radius * inner, cy - cos_t * radius * inner,
            cx + sin_t * radius * outer, cy - cos_t * radius * outer,
        ))
    return ticks


def lock_ring_bounds(angle, half_span_deg=38.0):
    """专注提示的锁定环：指针方向两侧各撑开多少度（返回中心角与半张角，度）。"""
    return math.degrees(wrap_angle(float(angle))), float(half_span_deg)



