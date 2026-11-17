# -*- coding: utf-8 -*-
"""
FPS 音频雷达 —— HUD 绘制层（PyQt5）

只负责画两块东西：
    · 屏幕四边的 12 段「声音波形条」——哪边有声音，那边的柱子就跳起来
    · 屏幕正中刻度盘里的「直线箭头」——箭杆 + 实心三角头，指向声源方向

几何全部来自 hud_geometry.py（waveform_bars / arrow_geometry / dial_ticks），
这里不重新算角度，保证绘制和单测用同一套换算。

对应 ShengWen HUD 的视觉语言：
    PointerHud._paint_active_segments / _paint_segment    → paint_edge_waves()
    PointerHud._draw（刻度环 + 指针 + 方位文字）            → paint_pointers()
    direction_overlay.EdgeSurface.draw_idle_line          → _paint_idle_segment()
"""

from __future__ import annotations

import math

from PyQt5.QtCore import Qt, QPointF, QRectF
from PyQt5.QtGui import (QPainter, QColor, QPen, QBrush,
                         QRadialGradient, QPolygonF)

from audio_direction import SOUND_TYPES, clamp
from hud_geometry import (HUD_COLORS, arrow_geometry, build_segments, dial_ticks,
                          level_color, pointer_radius, waveform_bars)


# ============================ 颜色小工具 ============================
def _qcolor(rgb, alpha=255):
    return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(clamp(alpha, 0, 255)))


def _pt(point):
    """(x, y) 元组 → QPointF。"""
    return QPointF(float(point[0]), float(point[1]))


def _mix(rgb_a, rgb_b, t):
    t = clamp(float(t), 0.0, 1.0)
    return (rgb_a[0] + (rgb_b[0] - rgb_a[0]) * t,
            rgb_a[1] + (rgb_b[1] - rgb_a[1]) * t,
            rgb_a[2] + (rgb_b[2] - rgb_a[2]) * t)


def source_rgb(source, color_mode="red"):
    """声源颜色：单色模式取调色板，type 模式按声音类型。"""
    if color_mode == "type":
        return tuple(SOUND_TYPES.get(source.sound_type, SOUND_TYPES["other"])["color"])
    return HUD_COLORS.get(color_mode, HUD_COLORS["red"])


def segment_rgb(state, color_mode="red"):
    if color_mode == "type":
        return tuple(SOUND_TYPES.get(state.sound_type, SOUND_TYPES["other"])["color"])
    return HUD_COLORS.get(color_mode, HUD_COLORS["red"])


# ============================ 边缘波形条 ============================
def _paint_idle_segment(p, seg, cfg):
    """未激活时的细底线：告诉你 HUD 在什么位置，不抢画面。"""
    alpha = 255.0 * clamp(cfg.idle_opacity, 0.0, 1.0) * 0.75
    color = _qcolor((116, 110, 101), alpha)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(color))
    rect = QRectF(*seg.rect)
    if seg.orientation == "h":
        thin = max(1.5, rect.height() * 0.06)
        if seg.y <= 1.0:
            rect.setHeight(thin)
        else:
            rect.setTop(rect.bottom() - thin)
    else:
        thin = max(1.5, rect.width() * 0.06)
        if seg.x <= 1.0:
            rect.setWidth(thin)
        else:
            rect.setLeft(rect.right() - thin)
    p.drawRoundedRect(rect, thin / 2.0, thin / 2.0)


def _paint_waveform(p, seg, state, cfg, phase, color_mode="red"):
    """一段贴边波形条：整段一层底光 + 一排高低跳动的柱子。"""
    weight = clamp(state.weight, 0.0, 1.0)
    if weight <= 0.02:
        return

    base = _mix(segment_rgb(state, color_mode), level_color(state.level), 0.45)
    alpha = clamp(cfg.opacity * (0.30 + 0.70 * weight), 0.0, 1.0)
    rect = QRectF(*seg.rect)

    # 柱子：每根从屏幕边往内长，越高的越亮；柱子本身就有形状，不再铺大块底光
    p.setPen(Qt.NoPen)
    for (x, y, w, h, amp) in waveform_bars(seg, state.level, weight, phase):
        amp = clamp(amp, 0.0, 1.0)
        color = _mix(base, (255, 255, 255), 0.34 * amp)
        bar_alpha = alpha * (0.30 + 0.70 * clamp(amp * 1.15, 0.0, 1.0))
        if bar_alpha <= 0.02:
            continue
        p.setBrush(_qcolor(color, 255.0 * bar_alpha))
        bar_rect = QRectF(x, y, max(1.6, w), max(1.6, h))
        radius = min(bar_rect.width(), bar_rect.height()) * 0.32
        p.drawRoundedRect(bar_rect, radius, radius)


def paint_edge_waves(p, width, height, frame, cfg, color_mode="red"):
    """12 段屏幕边缘波形条。"""
    segments = build_segments(width, height, cfg)
    p.setPen(Qt.NoPen)
    for seg in segments:
        _paint_idle_segment(p, seg, cfg)
        state = frame.segments.get(seg.key)
        if state is not None:
            _paint_waveform(p, seg, state, cfg, frame.phase, color_mode)


# ============================ 中心指针（直线箭头）============================
def _paint_dial(p, cx, cy, radius, cfg, active):
    ring_alpha = 255.0 * (0.34 if active else 0.18)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(_qcolor((200, 204, 210), ring_alpha), max(1.0, radius * 0.012)))
    p.drawEllipse(QPointF(cx, cy), radius, radius)

    tick_alpha = 255.0 * (0.55 if active else 0.25)
    p.setPen(QPen(_qcolor((200, 204, 210), tick_alpha), max(1.0, radius * 0.014)))
    for (x0, y0, x1, y1) in dial_ticks(cx, cy, radius, cfg.pointer_ticks):
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    # 正前标记：盘顶一个小三角，随时能确认"上=前"
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(_qcolor((220, 224, 230), 255 * (0.55 if active else 0.22))))
    tri = QPolygonF([
        QPointF(cx - radius * 0.06, cy - radius * 1.10),
        QPointF(cx + radius * 0.06, cy - radius * 1.10),
        QPointF(cx, cy - radius * 1.02),
    ])
    p.drawPolygon(tri)


def _paint_lock_ring(p, cx, cy, radius, angle, cfg, phase):
    """专注提示的锁定环：沿声源方向的一段弧，随脉动呼吸。"""
    sweep = 76
    alpha = 0.45 + 0.35 * math.sin(2.0 * math.pi * phase)
    pen = QPen(_qcolor((255, 210, 90), 255 * alpha), max(1.6, radius * 0.035))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    rect = QRectF(cx - radius * 1.08, cy - radius * 1.08, radius * 2.16, radius * 2.16)
    # Qt 的角度：0° 在 3 点钟方向，逆时针为正；屏幕角度 0=正前=12 点。
    start_deg = 90.0 - math.degrees(angle) - sweep / 2.0
    p.drawArc(rect, int(start_deg * 16), int(sweep * 16))


def _paint_arrow(p, geo, source, base, cfg, radius):
    """一条箭杆 + 一个实心三角头，指尖还有一点柔光。"""
    start = geo["start"]
    head_base = geo["head_base"]
    tip = geo["tip"]

    shaft_alpha = clamp(cfg.opacity * (0.45 + 0.55 * source.level), 0.0, 1.0)
    pen = QPen(_qcolor(base, 255.0 * shaft_alpha), max(2.0, radius * 0.030))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawLine(_pt(start), _pt(head_base))

    p.setPen(Qt.NoPen)
    head_color = _mix(base, (255, 255, 255), 0.18)
    head_alpha = clamp(cfg.opacity * (0.70 + 0.30 * source.level), 0.0, 1.0)
    p.setBrush(_qcolor(head_color, 255.0 * head_alpha))
    p.drawPolygon(QPolygonF([_pt(head_base), _pt(geo["head_left"]),
                             _pt(tip), _pt(geo["head_right"])]))

    # 指尖柔光：深色画面上也能一眼看到箭头指到哪
    glow_r = max(5.0, radius * 0.15)
    glow = QRadialGradient(QPointF(tip[0], tip[1]), glow_r)
    glow.setColorAt(0.0, _qcolor(base, 255 * 0.28 * (0.4 + 0.6 * source.level)))
    glow.setColorAt(1.0, _qcolor(base, 0))
    p.setBrush(QBrush(glow))
    p.drawEllipse(QPointF(tip[0], tip[1]), glow_r, glow_r)


def _paint_center_dot(p, cx, cy, cfg):
    """准星位置的红点：固定在屏幕正中，不管有没有声音都在，方便压枪时对齐。"""
    radius = max(1.0, float(cfg.center_dot_size) * max(0.6, cfg.size))
    red = (239, 68, 68)

    glow_r = radius * 3.4
    glow = QRadialGradient(QPointF(cx, cy), glow_r)
    glow.setColorAt(0.0, _qcolor(red, 120))
    glow.setColorAt(1.0, _qcolor(red, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

    p.setBrush(QBrush(_qcolor(red, 240)))
    p.drawEllipse(QPointF(cx, cy), radius, radius)


def paint_pointers(p, width, height, frame, cfg, color_mode="red"):
    """中心指针盘：多声源时每个声源一根箭头，长度依次内缩避免重叠。"""
    cx, cy = width / 2.0, height / 2.0
    radius = pointer_radius(width, height, cfg)
    sources = frame.sources
    active = bool(sources)

    _paint_dial(p, cx, cy, radius, cfg, active)

    if not active:
        if cfg.center_dot:
            _paint_center_dot(p, cx, cy, cfg)
        return

    for index, source in enumerate(sources):
        # 第二个之后的箭头短一点，几根箭头叠在一起也能分得清
        scale = max(0.45, 1.0 - 0.16 * index)
        geo = arrow_geometry(source.angle, cx, cy, radius, source.level,
                             length_scale=scale, head_width_ratio=cfg.pointer_width)
        base = _mix(source_rgb(source, color_mode), level_color(source.level), 0.35)
        _paint_arrow(p, geo, source, base, cfg, radius)

    if frame.hint_logic == "focus" and cfg.focus_ring:
        _paint_lock_ring(p, cx, cy, radius, sources[0].angle, cfg, frame.phase)

    # 红点最后画，任何情况下都压在箭头/锁定环上面
    if cfg.center_dot:
        _paint_center_dot(p, cx, cy, cfg)


# ============================ 总入口 ============================
def paint_hud(p, width, height, frame, cfg, mode="dual", color_mode="red"):
    """按显示模式画一帧 HUD。mode: off / wave / pointer / dual。"""
    if mode == "off":
        return
    p.setRenderHint(QPainter.Antialiasing, True)
    # 整体透明：一次乘到所有绘制上（波形条、刻度盘、箭头、红点一起变淡）
    p.setOpacity(clamp(cfg.global_alpha, 0.0, 1.0))

    if cfg.show_edges and mode in ("wave", "dual"):
        paint_edge_waves(p, width, height, frame, cfg, color_mode)
    if cfg.show_pointer and mode in ("pointer", "dual"):
        paint_pointers(p, width, height, frame, cfg, color_mode)
