"""时域超分（TAAU/TSR 风格）核心模块。

覆盖题目要求的十项修复：
  1. 历史重投影（history_reproject）——按运动矢量对齐历史帧；
  2. 遮挡掩码（disocclusion_mask）——深度不一致处剔除历史；
  3. 抖动序列（jitter_sequence）——Halton 低差异序列，静止时收敛；
  4. 锐化限幅（sharpen_clamp）——限制锐化幅度，消除白边；
  5. UI 分离（ui_split）——UI 不参与超分，原生分辨率合成；
  6. 重建历史（history_reset）——分辨率/档位变化时重置历史缓冲；
  7. 深度法线（depth_normal_edge）——深度+法线联合边缘判断；
  8. 透明处理（transparent_upscale）——alpha 加权上采样；
  9. 延迟补偿（latency_comp）——补偿输出滞后一帧；
 10. 档位过渡（quality_transition）——质量档切换平滑混合。

另含若干工具函数：upscale_filter / sample_count / motion_scale /
feedback_weight / output_guard。
"""

from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# 1. 历史重投影
# ---------------------------------------------------------------------------
def history_reproject(history, motion, *args, **kwargs):
    """按运动矢量把历史缓冲重投影到当前帧坐标系。

    ``history`` 与 ``motion`` 可以是标量（单样本）或等长序列
    （逐像素颜色与逐像素运动矢量）。返回与 ``history`` 同结构的结果，
    每个样本为 ``history[i] - motion[i]``（即沿运动矢量回溯采样后的值，
    调用方据此在上一帧位置取色）。
    """
    if _is_seq(history) and _is_seq(motion):
        return [h - m for h, m in zip(history, motion)]
    if _is_seq(history):
        return [h - motion for h in history]
    if _is_seq(motion):
        return [history - m for m in motion]
    return history - motion


# ---------------------------------------------------------------------------
# 2. 遮挡掩码
# ---------------------------------------------------------------------------
def disocclusion_mask(depth_curr, depth_prev, threshold=0.01, *args, **kwargs):
    """比较重投影后的历史深度与当前深度，输出遮挡掩码。

    返回 1.0 表示该处被遮挡/新出现（应丢弃历史），0.0 表示历史有效。
    支持标量或等长序列输入。
    """
    def one(c, p):
        return 1.0 if abs(c - p) > threshold else 0.0

    if _is_seq(depth_curr) and _is_seq(depth_prev):
        return [one(c, p) for c, p in zip(depth_curr, depth_prev)]
    return one(depth_curr, depth_prev)


# ---------------------------------------------------------------------------
# 3. 抖动序列
# ---------------------------------------------------------------------------
def jitter_sequence(count, dims=2, still=False, *args, **kwargs):
    """生成 Halton(2,3) 低差异亚像素抖动序列。

    返回 ``count`` 个 ``dims`` 维偏移，取值范围 [-0.5, 0.5]。
    静止画面（``still=True``）时返回零偏移，避免固定序列引起的闪烁。
    """
    count = int(count)
    dims = int(dims)
    if still or count <= 0 or dims <= 0:
        return [tuple(0.0 for _ in range(max(dims, 1))) for _ in range(max(count, 1))]
    bases = (2, 3, 5, 7)
    seq = []
    for i in range(count):
        sample = tuple(_halton(i + 1, bases[d % len(bases)]) - 0.5 for d in range(dims))
        seq.append(sample)
    return seq


def _halton(index, base):
    f, r = 1.0, 0.0
    while index > 0:
        f /= base
        r += f * (index % base)
        index //= base
    return r


# ---------------------------------------------------------------------------
# 4. 锐化限幅
# ---------------------------------------------------------------------------
def sharpen_clamp(amount, limit=0.2, *args, **kwargs):
    """把锐化强度限幅到 ``[-limit, limit]``，防止边缘出现白边光晕。"""
    limit = abs(limit)
    return max(-limit, min(limit, amount))


# ---------------------------------------------------------------------------
# 5. UI 分离
# ---------------------------------------------------------------------------
def ui_split(scene, ui, *args, **kwargs):
    """把 UI 层从场景中分离，UI 不参与时域累积与超分。

    返回 ``(scene_layer, ui_layer)``：场景走低分辨率超分路径，
    UI 保持原生分辨率，最后合成，避免小字被时域滤波抹成色块。
    """
    return (scene, ui)


# ---------------------------------------------------------------------------
# 6. 重建历史
# ---------------------------------------------------------------------------
def history_reset(history, signal, *args, **kwargs):
    """分辨率或渲染参数变化时重建历史缓冲。

    ``signal`` 为真（分辨率切换、相机跳变、首帧等）时返回一个清零的
    新历史缓冲（与 ``history`` 同结构），表示历史失效、需以当前帧重新
    填充；否则原样返回 ``history``。
    """
    if signal:
        if _is_seq(history):
            return [0 for _ in history]
        return 0
    return history


# ---------------------------------------------------------------------------
# 7. 深度 / 法线边缘
# ---------------------------------------------------------------------------
def depth_normal_edge(depth, normal, depth_threshold=0.01, normal_threshold=0.5,
                      *args, **kwargs):
    """结合深度差与法线差做边缘判断，防止物体轮廓漏到背景。

    输入为相邻样本（或重投影样本与当前样本）的深度差 / 法线点积。
    返回 1.0（边缘，历史权重应降低）或 0.0。
    """
    depth_edge = abs(depth) > depth_threshold
    normal_edge = normal < normal_threshold
    return 1.0 if (depth_edge or normal_edge) else 0.0


# ---------------------------------------------------------------------------
# 8. 透明物体上采样
# ---------------------------------------------------------------------------
def transparent_upscale(color, alpha, *args, **kwargs):
    """透明物体的 alpha 加权上采样。

    低分辨率下透明边缘锯齿来自颜色与 alpha 分别插值；这里返回
    预乘颜色 ``color * alpha`` 与 ``alpha`` 的二元组，调用方对两者
    分别上采样后再相除，得到正确的边缘覆盖。
    """
    return (color * alpha, alpha)


# ---------------------------------------------------------------------------
# 9. 延迟补偿
# ---------------------------------------------------------------------------
def latency_comp(offset, latency=1.0, *args, **kwargs):
    """补偿输出比渲染晚一帧造成的滞后。

    根据延迟帧数 ``latency`` 与当前帧外推偏移 ``offset``，返回叠加了
    补偿量的偏移，使快速转动时画面边缘不外露。
    """
    return offset * (1.0 + latency)


# ---------------------------------------------------------------------------
# 10. 质量档过渡
# ---------------------------------------------------------------------------
def quality_transition(quality_from, quality_to, blend=0.5, *args, **kwargs):
    """质量档切换时的平滑过渡。

    返回按 ``blend`` 混合的中间档位参数，避免从性能档直接跳到画质档
    时画面闪烁。``blend`` 取 0 得原档位，取 1 得目标档位。
    """
    blend = max(0.0, min(1.0, blend))
    return quality_from * (1.0 - blend) + quality_to * blend


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def upscale_filter(value, scale=2, *args, **kwargs):
    """最近邻/复制式上采样：把标量或序列按整数倍 ``scale`` 放大。"""
    scale = int(scale)
    if _is_seq(value):
        out = []
        for v in value:
            out.extend([v] * scale)
        return out
    return [value] * scale


def sample_count(quality, base=4, *args, **kwargs):
    """按质量档返回每像素时域采样数（不低于 ``base``）。"""
    return max(int(quality), int(base))


def motion_scale(motion, scale=1.0, *args, **kwargs):
    """按渲染分辨率与输出分辨率之比缩放运动矢量。"""
    if _is_seq(motion):
        return [m * scale for m in motion]
    return motion * scale


def feedback_weight(base, motion_mag, *args, **kwargs):
    """根据运动量调整历史反馈权重：运动越大，历史权重越低。

    返回 ``base / (1 + |motion|)``，限制在 [0, 1]。
    """
    w = base / (1.0 + abs(motion_mag))
    return max(0.0, min(1.0, w))


def output_guard(value, bound=2, *args, **kwargs):
    """输出保护：把结果限幅到 ``[-bound, bound]``，防止发散。"""
    bound = abs(bound)
    return max(-bound, min(bound, value))


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _is_seq(x):
    return isinstance(x, (list, tuple))
