"""
基本质量工具 — 运行图 (Run Chart) / 鱼骨图 (石川图)
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ==================== 运行图 (Run Chart) ====================

def run_chart(data, target=None):
    """
    运行图 — 含中位数线 + 游程检验
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)

    if n < 3:
        return {'error': '至少需要 3 个数据点'}

    median_val = np.median(data)
    mean_val = np.mean(data)

    # 游程检验 (围绕中位数)
    above = data > median_val
    below = data < median_val
    runs_list = above[data != median_val].astype(int)
    n_runs = 1
    for i in range(1, len(runs_list)):
        if runs_list[i] != runs_list[i - 1]:
            n_runs += 1

    n1 = np.sum(above)
    n2 = np.sum(below)

    # 游程数期望和标准差
    if n1 > 0 and n2 > 0:
        exp_runs = 1 + 2 * n1 * n2 / (n1 + n2)
        std_runs = np.sqrt(2 * n1 * n2 * (2 * n1 * n2 - n1 - n2) /
                           ((n1 + n2) ** 2 * (n1 + n2 - 1)))
        z_runs = (n_runs - exp_runs) / std_runs if std_runs > 0 else 0
        run_test_sig = abs(z_runs) > 1.96  # 0.05 显著性
    else:
        exp_runs, std_runs, z_runs, run_test_sig = 0, 0, 0, False

    # 判断趋势
    from scipy import stats
    slope, _, _, p_val, _ = stats.linregress(range(n), data)
    has_trend = p_val < 0.05

    fig = go.Figure()
    idx = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=idx, y=data, mode='lines+markers', name='数据',
                             marker=dict(color='#1f77b4', size=6), line=dict(width=2)))
    fig.add_hline(y=median_val, line_color='green', line_width=2, line_dash='solid',
                  annotation_text=f'中位数={median_val:.4f}')
    fig.add_hline(y=mean_val, line_color='orange', line_width=1.5, line_dash='dash',
                  annotation_text=f'均值={mean_val:.4f}')
    if target is not None:
        fig.add_hline(y=target, line_color='red', line_width=1.5, line_dash='dot',
                      annotation_text=f'目标={target}')

    # 异常点标注 (3σ 以外)
    std_val = np.std(data, ddof=1)
    outliers = np.abs(data - mean_val) > 3 * std_val
    if outliers.any():
        outlier_idx = np.where(outliers)[0] + 1
        fig.add_trace(go.Scatter(x=outlier_idx.tolist(), y=data[outliers], mode='markers',
                                 name=f'异常点', marker=dict(color='red', size=12, symbol='x')))

    fig.update_layout(title='运行图 (Run Chart)', template='plotly_white', height=400)
    fig.update_xaxes(title_text='观测序号')
    fig.update_yaxes(title_text='数值')

    return {
        'chart': fig,
        'stats': {
            '样本量 n': n,
            '中位数': f'{median_val:.4f}',
            '均值': f'{mean_val:.4f}',
            '标准差': f'{std_val:.4f}',
            '游程数': n_runs,
            '期望游程': f'{exp_runs:.1f}',
            'z 值': f'{z_runs:.3f}',
            '游程检验': '非随机 (可能存在模式)' if run_test_sig else '随机 (无异常模式)',
            '趋势检验': f'显著趋势 (p={p_val:.4f})' if has_trend else '无显著趋势',
        }
    }


# ==================== 鱼骨图 (石川图) ====================

def _draw_bone(fig, x0, y0, angle_deg, length, color, width=2):
    """画一根斜线骨，返回端点坐标"""
    rad = np.radians(angle_deg)
    x1 = x0 + length * np.cos(rad)
    y1 = y0 + length * np.sin(rad)
    fig.add_shape(type='line', x0=x0, y0=y0, x1=x1, y1=y1,
                  line=dict(color=color, width=width))
    return x1, y1


def _draw_label(fig, x, y, text, color, font_size=10, align='left'):
    """在指定位置添加文字标签"""
    fig.add_annotation(x=x, y=y, text=text, showarrow=False,
                       font=dict(size=font_size, color=color),
                       align=align)


def fishbone_diagram(problem, categories):
    """
    鱼骨图 — 经典石川图（所有鱼刺指向鱼头/左侧）
    - problem: 问题描述
    - categories: dict
        {大类: [原因1, 原因2, ...]}  或
        {大类: ['原因A', {'二级分类': ['子原因1', '子原因2']}, '原因B']}
      支持 str（一级原因）和 dict（二级分类+子原因）混合
    """
    fig = go.Figure()

    cat_names = list(categories.keys())
    n_cats = len(cat_names)
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#8c564b', '#e377c2',
              '#bcbd22', '#17becf']

    # 主干水平线（从左到右指向鱼头）
    spine_start = -5.5
    spine_end = 6.5
    fig.add_shape(type='line', x0=spine_start, y0=0, x1=spine_end, y1=0,
                  line=dict(color='#333', width=3))

    # 鱼头（问题在右端）
    fig.add_trace(go.Scatter(x=[spine_end], y=[0], mode='markers+text',
                             marker=dict(size=28, color='#d62728', symbol='triangle-right'),
                             text=[f'<b>{problem}</b>'], textposition='middle right',
                             textfont=dict(size=14, color='#d62728'),
                             showlegend=False))

    # 大骨分布参数 — 沿主干从左到右，上下交替
    usable = spine_end - spine_start - 2.0
    spacing = usable / max(n_cats, 1)
    big_bone_len = 2.4
    small_bone_len = 1.5
    sub_bone_len = 1.1

    # 大骨角度：都指向左（鱼头方向）
    # 上侧 ~130°（向左上方），下侧 ~-130°（向左下方）
    upper_big_angle = 130
    lower_big_angle = -130

    for i, (cat_name, causes) in enumerate(categories.items()):
        col = colors[i % len(colors)]
        is_upper = (i % 2 == 0)
        big_angle = upper_big_angle if is_upper else lower_big_angle

        # 大骨起点（沿主干从左到右）
        sx = spine_start + 1.0 + i * spacing

        # 画大骨（向左上方或左下方）
        ex, ey = _draw_bone(fig, sx, 0, big_angle, big_bone_len, col, width=2.5)

        # 大骨分类标签（放在大骨末端外侧）
        label_dx = 0.45 * np.cos(np.radians(big_angle))
        label_dy = 0.45 * np.sin(np.radians(big_angle))
        _draw_label(fig, ex + label_dx, ey + label_dy,
                    f'<b>{cat_name}</b>', col, font_size=12)

        # 画小骨（原因）— 都从大骨上向左（鱼头方向）延伸
        if not causes:
            continue

        n = len(causes)
        for j, item in enumerate(causes):
            # 小骨起点：沿大骨均匀分布（靠近大骨根部到末端）
            ratio = 0.25 + 0.55 * (j / max(n - 1, 1)) if n > 1 else 0.45
            bx = sx + (ex - sx) * ratio
            by = 0 + (ey - 0) * ratio

            # 小骨角度：比大骨更平缓，更指向左
            # 上侧 ~165°（更水平向左），下侧 ~-165°
            small_angle = (165 if is_upper else -165) + (10 if j % 2 == 0 else -10)
            sx2, sy2 = _draw_bone(fig, bx, by, small_angle, small_bone_len, col, width=1.5)

            if isinstance(item, str):
                # 简单原因 — 直接标注在小骨末端
                _draw_label(fig, sx2 + 0.12, sy2 + 0.05, item, '#555', font_size=9)

            elif isinstance(item, dict):
                # 二级分类 — 小骨末端是分类名，再画子小骨
                sub_cat_name = list(item.keys())[0]
                sub_causes = item[sub_cat_name]

                # 分类名标签（小骨末端）
                _draw_label(fig, sx2 + 0.12, sy2 + 0.05,
                            f'<b>{sub_cat_name}</b>', col, font_size=9)

                # 子小骨 — 从分类名位置继续向左细分
                if sub_causes:
                    m = len(sub_causes)
                    for k, sub_cause in enumerate(sub_causes):
                        # 子小骨起点：沿小骨分布
                        sub_ratio = 0.3 + 0.5 * (k / max(m - 1, 1)) if m > 1 else 0.5
                        sbx = bx + (sx2 - bx) * sub_ratio
                        sby = by + (sy2 - by) * sub_ratio

                        # 子小骨角度更平缓
                        sub_angle = (170 if is_upper else -170) + (8 if k % 2 == 0 else -8)
                        ssx, ssy = _draw_bone(fig, sbx, sby, sub_angle, sub_bone_len, col, width=1.0)

                        _draw_label(fig, ssx + 0.1, ssy + 0.03, sub_cause, '#777', font_size=8)

    # 隐藏坐标轴
    fig.update_xaxes(visible=False, range=[-9, 10])
    fig.update_yaxes(visible=False, range=[-5.5, 5.5])
    fig.update_layout(
        title=f'鱼骨图 — {problem}',
        template='plotly_white',
        height=560,
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )

    return {'chart': fig, 'problem': problem, 'categories': categories}
