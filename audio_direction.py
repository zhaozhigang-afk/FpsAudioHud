# -*- coding: utf-8 -*-
"""
FPS 音频雷达 —— 声音方向估计（纯 numpy，无 GUI 依赖，可单独测试）

全工程统一的角度约定
--------------------
    角度单位：弧度
    0      = 正前方（玩家准星方向）
    +π/2   = 正右方
    -π/2   = 正左方
    ±π     = 正后方
    屏幕坐标：正前 = 屏幕上方，正右 = 屏幕右方（HUD 的波形条 / 箭头都用这套）。

为什么原来的映射不准（本模块针对这几点重写）
--------------------------------------------
1. 立体声原先用 ±45° 两个声道角度做向量和，纯左声道最多只能算出 -45°，
   "左侧" 永远到不了雷达左侧。这里改用等功率声像（constant-power pan）反解，
   左右声道能量相等 → 0°，只有左声道 → -90°，只有右声道 → +90°。
2. 原先把整段宽带 RMS 直接当权重，环境底噪、声道串音把所有方向往中间拉。
   这里先做 60Hz~12kHz 带通（FFT 域），再减去每声道自适应底噪，才参与方向计算。
3. 原先方向角乘增益后直接截断到 ±180°，>1 时会截出错误方向。
   这里把增益改成权重锐化（指数），任何增益下方向都连续、不会翻面。
4. 原先只在声音"结束"时提交一个平均角度，短促/移动声音会被平均拉偏。
   这里每块音频都输出实时方向，由 SourceTracker 做同一声源的平滑跟踪。
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

PI = math.pi
TWO_PI = 2.0 * math.pi
HALF_PI = 0.5 * PI

# 八个方向扇区（中心角度，度）：正前 / 右前 / 右 / 右后 / 正后 / 左后 / 左 / 左前
SECTOR_NAMES = ("中前", "右前", "右侧", "右后", "正后", "左后", "左侧", "左前")
SECTOR_ANGLES_DEG = (0.0, 45.0, 90.0, 135.0, 180.0, -135.0, -90.0, -45.0)


def clamp(value, low, high):
    return low if value < low else high if value > high else value


def wrap_angle(angle):
    """把角度归一化到 (-π, π]。"""
    angle = math.fmod(angle + PI, TWO_PI)
    if angle <= 0.0:
        angle += TWO_PI
    return angle - PI


def sector_index(angle):
    """返回角度所属的 8 方向扇区下标（0=中前 ... 4=正后 ... 7=左前）。"""
    return int(round(wrap_angle(angle) / (PI / 4.0))) % 8


def sector_name(angle):
    return SECTOR_NAMES[sector_index(angle)]


# ============================ 声道布局 ============================
# WASAPI / WAVEFORMATEXTENSIBLE 标准声道顺序。
# 每项：(短名, 角度_度, 是否参与方向计算)。LFE 无方向，不参与。
_LAYOUTS = {
    1: (("M", 0.0, True),),
    # 2ch 不做向量和，改用声像反解；这里的 ±90 只是占位。
    2: (("L", -90.0, True), ("R", 90.0, True)),
    3: (("L", -45.0, True), ("R", 45.0, True), ("C", 0.0, True)),
    4: (("FL", -45.0, True), ("FR", 45.0, True),
        ("BL", -135.0, True), ("BR", 135.0, True)),
    5: (("FL", -30.0, True), ("FR", 30.0, True), ("C", 0.0, True),
        ("BL", -135.0, True), ("BR", 135.0, True)),
    6: (("FL", -30.0, True), ("FR", 30.0, True), ("C", 0.0, True),
        ("LFE", 0.0, False),
        ("SL", -110.0, True), ("SR", 110.0, True)),
    7: (("FL", -30.0, True), ("FR", 30.0, True), ("C", 0.0, True),
        ("LFE", 0.0, False),
        ("BL", -150.0, True), ("BR", 150.0, True), ("BC", 180.0, True)),
    8: (("FL", -30.0, True), ("FR", 30.0, True), ("C", 0.0, True),
        ("LFE", 0.0, False),
        ("BL", -150.0, True), ("BR", 150.0, True),
        ("SL", -90.0, True), ("SR", 90.0, True)),
}

# 中文短标签（给声道电平表 / 调试输出用）
CHANNEL_LABELS = {
    "FL": "前左", "FR": "前右", "C": "中置", "LFE": "低音",
    "BL": "后左", "BR": "后右", "SL": "侧左", "SR": "侧右",
    "BC": "后中", "L": "左", "R": "右", "M": "单声道",
}


def channel_layout(n_channels):
    """返回长度恰为 n_channels 的布局表。未知声道数时退化为立体声 + 无效声道。"""
    n = max(1, int(n_channels))
    layout = _LAYOUTS.get(n)
    if layout is not None:
        return layout
    layout = [("L", -90.0, True), ("R", 90.0, True)]
    layout += [("X%d" % i, 0.0, False) for i in range(2, n)]
    return tuple(layout[:n])


def channel_names(n_channels):
    return tuple(name for name, _angle, _valid in channel_layout(n_channels))


def is_stereo_layout(n_channels):
    return int(n_channels) == 2


# ============================ 声像 → 角度 ============================
def pan_angle(weight_left, weight_right):
    """
    由左右声道权重反解等功率声像角度。

    假设游戏用等功率声像律把声源摆到 θ∈[-90°, +90°]：
        L = cos((1 + θ/90°) · π/4),  R = sin((1 + θ/90°) · π/4)
    则 θ = 90° · ( (4/π) · atan(sqrt(R/L)) - 1 )

    性质：左右相等 → 0°；只有左 → -90°；只有右 → +90°；单调连续。
    """
    wl = max(float(weight_left), 0.0)
    wr = max(float(weight_right), 0.0)
    if wl <= 1e-12 and wr <= 1e-12:
        return 0.0
    if wr <= 1e-12:
        return -HALF_PI
    if wl <= 1e-12:
        return HALF_PI
    ratio = math.sqrt(wr / wl)
    return clamp((4.0 / PI) * math.atan(ratio) - 1.0, -1.0, 1.0) * HALF_PI


# ============================ 声音分类 ============================
SOUND_TYPES = {
    "footstep": {"label": "脚步", "color": (66, 165, 245)},
    "gunshot": {"label": "枪响", "color": (239, 83, 80)},
    "explosion": {"label": "爆炸", "color": (255, 152, 0)},
    "door": {"label": "开门", "color": (171, 71, 188)},
    "other": {"label": "其他", "color": (160, 160, 160)},
    "test": {"label": "测试", "color": (0, 229, 255)},
}


def classify_sound(rms, peak_ratio, centroid, duration_blocks):
    """基于频谱特征的简单声音分类（duration_blocks 按 1024 帧一块计数）。"""
    if peak_ratio > 4.0 and centroid > 2000 and duration_blocks < 6:
        return "gunshot"
    if rms > 0.08 and centroid < 800 and duration_blocks >= 4:
        return "explosion"
    if 80 < centroid < 2500 and peak_ratio > 2.0 and duration_blocks < 8:
        return "footstep"
    if 300 < centroid < 3000 and duration_blocks >= 6 and peak_ratio < 5:
        return "door"
    return "other"


def intensity_from_snr(snr, threshold):
    """把信噪比换算成 0.12~1.0 的显示强度（越大表示声音越近/越响）。"""
    threshold = max(float(threshold), 1e-6)
    value = 0.12 + (float(snr) - threshold) / (threshold * 2.5) * 0.88
    return clamp(value, 0.12, 1.0)


# ---- 灵敏度：把「信噪比阈值」换算成实际生效的等效阈值 ----
# 1.0 = 中性（阈值原样使用）；越大越灵敏 = 等效阈值越低。
# 下限 1.25：稳定环境音自己就有 ~1.1 的 snr，再低整块 HUD 会常亮；
# 上限 4.5：再高基本什么都听不到，属于无效区间。
SENSITIVITY_MIN = 0.5
SENSITIVITY_MAX = 2.5
EFF_THRESHOLD_MIN = 1.25
EFF_THRESHOLD_MAX = 4.5


BAND_MIN_HZ = 20.0
BAND_MAX_HZ = 20000.0
BAND_MIN_SPAN = 50.0


def effective_band(low, high):
    """把用户输入的频带夹到合法范围：20~20000Hz，且至少 50Hz 宽。"""
    try:
        lo = float(low)
        hi = float(high)
    except (TypeError, ValueError):
        return (150.0, 8000.0)
    lo = min(BAND_MAX_HZ, max(BAND_MIN_HZ, lo))
    hi = min(BAND_MAX_HZ, max(BAND_MIN_HZ, hi))
    if hi - lo < BAND_MIN_SPAN:
        hi = min(BAND_MAX_HZ, lo + BAND_MIN_SPAN)
        if hi - lo < BAND_MIN_SPAN:
            lo = hi - BAND_MIN_SPAN
    return (lo, hi)


def effective_snr_threshold(threshold, sensitivity=1.0):
    """灵敏度 → 实际参与判断的信噪比阈值：threshold / sensitivity（带安全钳位）。"""
    th = max(1e-6, float(threshold))
    try:
        sens = float(sensitivity)
    except (TypeError, ValueError):
        sens = 1.0
    sens = min(SENSITIVITY_MAX, max(SENSITIVITY_MIN, sens if sens > 0 else 1.0))
    return min(EFF_THRESHOLD_MAX, max(EFF_THRESHOLD_MIN, th / sens))


# ============================ 方向估计器 ============================
class DirectionEstimator:
    """
    单块音频 → 一个实时方向。

    process() 返回 None（静音/低于噪声门）或：
        angle        弧度，0=正前，+右，-左，±π=正后
        confidence   0~1，方向集中度（多声道能量矢量长度）
        level        带内电平（各有效声道 RMS 之和，与旧版噪声门刻度一致）
        snr          信噪比
        rms/peak_ratio/centroid  声音分类特征
        weights      各声道归一化权重（调试用）
    """

    # 分析频带：默认 120~8000 Hz。低频轰鸣（载具/环境）和 8k 以上（雨声、枪声尾音）
    # 对脚步判断没帮助，却会把自适应底噪整体抬高 —— 实测全带下 80Hz 轰鸣 0.06
    # 能把 0.05 的脚步压到 0% 检出，改窄后恢复。可用 set_band() 运行中切换。
    # 低切 150 而不是 120：FFT 分辨率 46.9Hz，120 只比 80Hz 高 2.5 个 bin，
    # 汉宁窗泄漏压不住（实测 120 仍然 0% 检出，150 才恢复）。
    BAND_LOW_HZ = 150.0
    BAND_HIGH_HZ = 8000.0
    HISTORY_SECONDS = 2.5   # 底噪统计窗口
    NOISE_PERCENTILE = 15.0 # 取窗口内 15% 分位数作为底噪
    NOISE_FACTOR = 1.8      # 参与方向计算前，从声道电平里扣掉多少倍底噪

    # 多声源聚类（HUD 的多方向提示用）：声道角度差 <= 65° 视为同一声源。
    # 65° 刚好让 7.1 的 FL/FR（±30°）和中置合成一个"正前"，而 FL 与 BR 会拆成两个。
    MULTI_SPLIT_DEG = 65.0
    MULTI_MIN_SHARE = 0.12  # 一个分量至少占总权重 12% 才算独立声源
    MULTI_MAX_SOURCES = 4

    def __init__(self, sample_rate=48000, block_size=1024):
        self.sample_rate = float(sample_rate) or 48000.0
        self.channels = 0
        self.stereo = False
        self._angles = np.zeros(0)
        self._valid = np.zeros(0, dtype=bool)
        self._nf = np.zeros(0)
        self._levels = np.zeros(0)
        self._history = deque()
        self._history_len = 1
        self.band_low = float(self.BAND_LOW_HZ)
        self.band_high = float(self.BAND_HIGH_HZ)
        # 多段 EQ：[(lo, hi, gain_db), ...]，空 = 全 0 dB（只受低切/高切影响）
        self._eq_segments = ()
        self._eq_gain2 = None
        self._win = None
        self._win_energy = 1.0
        self._freqs = None
        self._band_mask = None
        self._prepare(int(block_size))
        self.set_channels(2)

    # ---------- 内部准备 ----------
    def _prepare(self, block_size):
        n = max(16, int(block_size))
        self.block_size = n
        self._win = np.hanning(n).astype(np.float64)
        self._win_energy = float(np.sum(self._win ** 2)) or 1.0
        self._freqs = np.fft.rfftfreq(n, 1.0 / self.sample_rate)
        self._rebuild_band_mask()
        self._rebuild_eq_weight()
        self._history_len = max(8, int(self.HISTORY_SECONDS * self.sample_rate / n))
        self._history = deque(self._history, maxlen=self._history_len)

    # ---------- 频带 ----------
    @property
    def band(self):
        """当前分析频带 (low, high) Hz。"""
        return (self.band_low, self.band_high)

    def set_band(self, low=None, high=None):
        """运行中切换分析频带（只影响检测，不改音频本身）。非法值原样保留。"""
        try:
            lo = float(low) if low is not None else self.band_low
            hi = float(high) if high is not None else self.band_high
        except (TypeError, ValueError):
            return
        if lo <= 0.0 or hi <= lo:
            return
        self.band_low, self.band_high = lo, hi
        self._rebuild_band_mask()
        # 电平是按频带算的，换带后旧底噪不再可比 —— 必须清掉，
        # 否则切档后会有 2.5 秒用错底噪（表现为突然满屏或整片不漏）
        self._history.clear()

    # ---------- 多段 EQ ----------
    @property
    def eq_segments(self):
        """当前 EQ 段：((lo, hi, gain_db), ...)"""
        return self._eq_segments

    def set_eq(self, segments):
        """设置多段 EQ。段按顺序覆盖：同一 bin 被多段覆盖时，后面的段生效。"""
        cleaned = []
        for seg in segments or ():
            try:
                lo = float(seg["lo"])
                hi = float(seg["hi"])
                gain = float(seg["gain_db"])
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            if hi <= lo or hi <= 0.0:
                continue
            cleaned.append((max(0.0, lo), hi, max(-60.0, min(24.0, gain))))
        self._eq_segments = tuple(cleaned)
        self._rebuild_eq_weight()

    def _rebuild_eq_weight(self):
        if self._freqs is None:
            return
        weight = np.ones(len(self._freqs), dtype=np.float64)
        for lo, hi, gain_db in self._eq_segments:
            mask = (self._freqs >= lo) & (self._freqs < hi)
            if not bool(np.any(mask)):
                continue
            amp = 10.0 ** (gain_db / 20.0)
            weight[mask] = amp * amp          # 作用在功率上
        self._eq_gain2 = weight

    def _rebuild_band_mask(self):
        freqs = self._freqs
        if freqs is None:
            return
        mask = (freqs >= self.band_low) & (freqs <= self.band_high)
        if not bool(np.any(mask)):
            mask = np.ones_like(freqs, dtype=bool)
        self._band_mask = mask

    def set_channels(self, n_channels):
        n = max(1, int(n_channels))
        layout = channel_layout(n)
        self.channels = n
        self._angles = np.radians(np.array([a for _name, a, _v in layout], dtype=np.float64))
        self._valid = np.array([bool(v) for _name, _a, v in layout], dtype=bool)
        self._nf = np.zeros(n, dtype=np.float64)
        self._levels = np.zeros(n, dtype=np.float64)
        self.stereo = is_stereo_layout(n)
        # 历史窗口留空：用 1e-4 塞满会让头 2.5 秒的底噪被严重低估
        # （floor 掉到噪声门，snr 直接飙到 1.4~3.8），开机那几秒会满屏误报。
        # 预热阶段改由 process() 里的"已观测最小电平"兜底。
        self._history = deque(maxlen=self._history_len)

    @property
    def levels(self):
        """最近一块的各声道带内电平（只读快照，音量表用）。"""
        return self._levels

    def reset(self):
        self._nf = np.zeros(self.channels, dtype=np.float64)
        self._history = deque(maxlen=self._history_len)

    # ---------- 多声源聚类 ----------
    def _cluster_weights(self, weights):
        """
        把归一化后的声道权重聚成若干独立声源（HUD 多方向提示的数据来源）。

        单一矢量求和适合"只有一个声源"：左后 + 正前一起响时会被平均成中左，
        那个角度其实谁也不在。这里按声道角度做邻近聚类，每个簇单独求方向，
        就能同时给出"左后有人"和"正前有人"两个方向。
        """
        indices = [i for i in range(int(weights.size))
                   if self._valid[i] and float(weights[i]) > 0.0]
        if not indices:
            return []
        indices.sort(key=lambda i: -float(weights[i]))

        split = math.radians(self.MULTI_SPLIT_DEG)
        clusters = []
        for index in indices:
            angle = float(self._angles[index])
            placed = False
            for cluster in clusters:
                if abs(wrap_angle(angle - cluster["angle"])) <= split:
                    cluster["members"].append(index)
                    cluster["total"] += float(weights[index])
                    members = cluster["members"]
                    vx = sum(float(weights[i]) * math.cos(float(self._angles[i])) for i in members)
                    vy = sum(float(weights[i]) * math.sin(float(self._angles[i])) for i in members)
                    if abs(vx) > 1e-12 or abs(vy) > 1e-12:
                        cluster["angle"] = math.atan2(vy, vx)
                    placed = True
                    break
            if not placed:
                clusters.append({
                    "angle": angle,
                    "total": float(weights[index]),
                    "members": [index],
                })

        total = sum(cluster["total"] for cluster in clusters) or 1.0
        components = []
        for cluster in clusters:
            members = cluster["members"]
            cluster_total = max(float(cluster["total"]), 1e-12)
            vx = sum(float(weights[i]) * math.cos(float(self._angles[i])) for i in members)
            vy = sum(float(weights[i]) * math.sin(float(self._angles[i])) for i in members)
            angle = math.atan2(vy, vx) if (abs(vx) > 1e-12 or abs(vy) > 1e-12) else cluster["angle"]
            share = cluster_total / total
            if share < self.MULTI_MIN_SHARE:
                continue
            components.append({
                "angle": angle,
                "share": float(share),
                # 簇内方向的集中度：单声道源=1，簇内越散越小
                "confidence": clamp(math.hypot(vx, vy) / cluster_total, 0.0, 1.0),
            })

        components.sort(key=lambda comp: -comp["share"])
        return components[:self.MULTI_MAX_SOURCES]

    # ---------- 主处理 ----------
    def process(self, frames, noise_gate=0.008, snr_threshold=1.8,
                direction_gain=1.0, stereo_pan=False):
        """
        stereo_pan=True 时，即使抓到的是 5.1/7.1，也只用前左/前右两个声道
        按立体声声像反解方向（用于"多声道设备、但游戏只输出立体声"的情况）。
        """
        data = np.asarray(frames, dtype=np.float64)
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        if data.ndim != 2 or data.shape[0] < 16:
            return None

        n, ch = data.shape
        if ch != self.channels:
            self.set_channels(ch)
        if n != self.block_size:
            self._prepare(n)

        # ---- 去直流 + 加窗 + FFT 带内能量 ----
        data = data - data.mean(axis=0, keepdims=True)
        spectrum = np.fft.rfft(data * self._win[:, None], axis=0)
        power = spectrum.real ** 2 + spectrum.imag ** 2
        # 多段 EQ 直接作用在频域：段内乘 10^(dB/10)，段外保持 1。
        # 所有声道同一套权重 → 只改电平关系，不改方向。
        if self._eq_gain2 is None:
            band_power = power[self._band_mask].sum(axis=0)
        else:
            weight = self._eq_gain2 * self._band_mask
            band_power = (power * weight[:, None]).sum(axis=0)
        # 由帕塞瓦尔定理还原带内 RMS（不要加极小值，否则静音声道会变成
        # 均匀的微小权重，在没有有效声源时算出一个假方向）
        levels = np.sqrt(np.maximum(2.0 * band_power / (n * self._win_energy), 0.0))
        self._levels = levels

        # ---- 每声道自适应底噪：取最近 2.5 秒该声道电平的 15% 分位数 ----
        # 用分位数而不是"当前最小值"：一声脚步不会把底噪抬起来，
        # 持续的环境噪音/音乐在 2.5 秒后才会被当成底噪，脚步声依旧能抓到。
        self._history.append(levels)
        window = np.asarray(self._history)
        if window.shape[0] >= max(8, self._history_len // 4):
            noise = np.percentile(window, self.NOISE_PERCENTILE, axis=0)
        elif window.shape[0] >= 2:
            # 预热阶段：拿"之前几块"的最小电平当底噪（不含当前块，
            # 否则第一声巨响会把自己的底噪一起抬高，反而检测不到）
            noise = np.min(window[:-1], axis=0)
        else:
            # 第一块：用 -60 dBFS 量级的保守底噪，稳定环境音 snr≈1.4 只闪一块，
            # 而枪声/脚步这种瞬态仍然是几十倍，不会被误杀
            noise = np.full_like(levels, 1e-3)
        noise = np.maximum(noise, 1e-6)
        self._nf = noise

        # ---- 噪声门 / 信噪比 ----
        total_level = float(np.sum(levels))
        floor_level = max(float(np.sum(noise)), float(noise_gate))
        snr = total_level / (floor_level + 1e-12)
        if total_level <= 0.0 or snr < float(snr_threshold):
            return None

        # ---- 声道权重：扣底噪 → 平方 → 有效声道归一化 ----
        weights = np.maximum(levels - noise * self.NOISE_FACTOR, 0.0) ** 2
        weights = np.where(self._valid, weights, 0.0)
        total_weight = float(weights.sum())
        if total_weight <= 0.0:
            return None
        weights = weights / total_weight

        # ---- 方向增益：权重锐化（不会像角度乘以增益那样翻面）----
        gain = float(direction_gain)
        if abs(gain - 1.0) > 1e-6:
            weights = weights ** max(0.1, gain)
            sharpened = float(weights.sum())
            if sharpened <= 0.0:
                return None
            weights = weights / sharpened

        # ---- 方向 ----
        if stereo_pan and ch >= 2:
            pair = weights[:2]
            pair_sum = float(pair.sum())
            if pair_sum <= 0.0:
                return None
            pair = pair / pair_sum
            angle = pan_angle(pair[0], pair[1])
            confidence = 1.0
        elif self.stereo:
            angle = pan_angle(weights[0], weights[1])
            confidence = 1.0
        else:
            real = float(np.sum(weights * np.cos(self._angles)))
            imag = float(np.sum(weights * np.sin(self._angles)))
            angle = math.atan2(imag, real)
            confidence = clamp(math.hypot(real, imag), 0.0, 1.0)

        # ---- 多声源分量（立体声只有一个声像方向，多声道按簇拆开）----
        if stereo_pan or self.stereo:
            components = [{"angle": angle, "share": 1.0, "confidence": confidence}]
        else:
            components = self._cluster_weights(weights)
            if not components:
                components = [{"angle": angle, "share": 1.0, "confidence": confidence}]

        # ---- 分类特征 ----
        mixed = data.mean(axis=1)
        rms = float(np.sqrt(np.mean(mixed ** 2)))
        peak = float(np.max(np.abs(mixed)))
        peak_ratio = peak / (rms + 1e-9)
        channel_spectrum = power.sum(axis=1)
        spec_total = float(channel_spectrum.sum())
        if spec_total > 0.0:
            centroid = float(np.sum(self._freqs * channel_spectrum) / spec_total)
        else:
            centroid = 1000.0

        return {
            "angle": angle,
            "confidence": confidence,
            "level": total_level,
            "snr": snr,
            "rms": rms,
            "peak_ratio": peak_ratio,
            "centroid": centroid,
            "weights": weights.copy(),
            "components": components,
        }


# ============================ 声源跟踪 ============================
class SourceTracker:
    """
    把逐块方向串成"声源光点"：
      - 新方向与已有声源相差不大 → 同一声源，做圆周平滑（光点跟着声音移动）
      - 方向突变 → 新建光点，旧光点自然淡出（不会从左边瞬间跳到右边）
      - 连续 hold 秒没有新数据 → 声源结束
    """

    def __init__(self, match_deg=60.0, hold=0.12, smooth=0.45, max_tracks=6):
        self.match_angle = math.radians(float(match_deg))
        self.hold = float(hold)
        self.smooth = clamp(float(smooth), 0.05, 0.95)
        self.max_tracks = max(1, int(max_tracks))
        self._tracks = {}
        self._next_id = 1

    def reset(self):
        self._tracks.clear()
        self._next_id = 1

    def update(self, source, now):
        """单声源入口（向后兼容）：source 为 process() 的返回值或 None。"""
        return self.update_multi([source] if source is not None else [], now)

    def update_multi(self, sources, now):
        """
        多声源入口：每个分量单独匹配/新建光点，返回本轮所有事件。

        匹配是贪心的：按强度从高到低，每个分量匹配角度最近、且本轮还没被占用的
        光点，所以"左后 + 正前"同时响时会稳定各占一个光点，不会互相抢。
        """
        for track_id in [tid for tid, tr in self._tracks.items()
                         if now - tr["updated"] > self.hold]:
            del self._tracks[track_id]

        patches = []
        if not sources:
            return patches

        pending = sorted(sources, key=lambda item: -float(item.get("intensity", 0.0)))
        matched = set()
        for source in pending:
            angle = float(source["angle"])
            best_id = None
            best_delta = self.match_angle
            for track_id, track in self._tracks.items():
                if track_id in matched:
                    continue
                delta = abs(wrap_angle(angle - track["angle"]))
                if delta <= best_delta:
                    best_id, best_delta = track_id, delta

            if best_id is None:
                track = self._new_track(angle, now)
            else:
                track = self._tracks[best_id]
                self._smooth_angle(track, angle)
            matched.add(track["id"])

            self._absorb(track, source, now)
            patches.append(self._patch(track, now))
        return patches

    def _new_track(self, angle, now):
        track_id = self._next_id
        self._next_id += 1
        track = {
            "id": track_id,
            "angle": float(angle),
            "intensity": 0.0,
            "confidence": 0.0,
            "blocks": 0,
            "rms": 0.0,
            "peak_ratio": 0.0,
            "centroid": 1000.0,
            "type": "other",
            "updated": now,
        }
        self._tracks[track_id] = track
        if len(self._tracks) > self.max_tracks:
            oldest = min(self._tracks.values(), key=lambda tr: tr["updated"])
            self._tracks.pop(oldest["id"], None)
        return track

    def _smooth_angle(self, track, angle):
        alpha = self.smooth
        old = track["angle"]
        vx = (1.0 - alpha) * math.cos(old) + alpha * math.cos(angle)
        vy = (1.0 - alpha) * math.sin(old) + alpha * math.sin(angle)
        if abs(vx) > 1e-12 or abs(vy) > 1e-12:
            track["angle"] = math.atan2(vy, vx)

    def _absorb(self, track, source, now):
        track["confidence"] = float(source.get("confidence", 1.0))
        intensity = float(source.get("intensity", 0.0))
        if track["blocks"] == 0:
            track["intensity"] = intensity
            track["rms"] = float(source.get("rms", 0.0))
            track["centroid"] = float(source.get("centroid", 1000.0))
            track["peak_ratio"] = float(source.get("peak_ratio", 0.0))
        else:
            track["intensity"] = track["intensity"] * 0.6 + intensity * 0.4
            track["rms"] = track["rms"] * 0.8 + float(source.get("rms", 0.0)) * 0.2
            track["centroid"] = track["centroid"] * 0.8 + float(source.get("centroid", 1000.0)) * 0.2
            track["peak_ratio"] = max(track["peak_ratio"] * 0.98,
                                      float(source.get("peak_ratio", 0.0)))
        track["blocks"] += 1
        track["updated"] = now
        # 分类只允许"保持或升级"，不允许把已经认出的枪响/脚步改回"其他"，
        # 否则光点颜色会在声音持续期间闪来闪去。
        candidate = classify_sound(
            track["rms"], track["peak_ratio"], track["centroid"], track["blocks"]
        )
        if candidate != "other" and (track["blocks"] <= 2 or track["type"] == "other"):
            track["type"] = candidate

    def _patch(self, track, now):
        return {
            "id": track["id"],
            "angle": track["angle"],
            "intensity": clamp(track["intensity"], 0.12, 1.0),
            "confidence": clamp(track["confidence"], 0.0, 1.0),
            "type": track["type"],
            "timestamp": now,
        }
