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

def fishbone_diagram(problem, categories):
    """
    鱼骨图 — 经典石川图（上下交替鱼骨样式）
    - problem: 问题描述
    - categories: dict, {大类: [原因1, 原因2, ...]}
    """
    fig = go.Figure()

    cat_names = list(categories.keys())
    n_cats = len(cat_names)
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#8c564b', '#e377c2',
              '#bcbd22', '#17becf']

    # 主干水平线
    spine_start = -4.5
    spine_end = 5.0
    fig.add_shape(type='line', x0=spine_start, y0=0, x1=spine_end, y1=0,
                  line=dict(color='#333', width=3))

    # 鱼头（问题在右端）
    fig.add_trace(go.Scatter(x=[spine_end], y=[0], mode='markers+text',
                             marker=dict(size=28, color='#d62728', symbol='triangle-right'),
                             text=[f'<b>{problem}</b>'], textposition='middle right',
                             textfont=dict(size=14, color='#d62728'),
                             showlegend=False))

    # 大骨上下交替分布
    spacing = (spine_end - spine_start - 1.0) / max(n_cats, 1)
    bone_length = 2.2
    cause_bone_length = 1.4

    for i, (cat_name, causes) in enumerate(categories.items()):
        col = colors[i % len(colors)]
        is_upper = (i % 2 == 0)  # 偶数在上，奇数在下
        direction = 1 if is_upper else -1

        # 大骨起点（沿主干从左到右均匀分布）
        sx = spine_start + 0.5 + i * spacing
        # 大骨角度（上约55度，下约-55度）
        angle = 55 if is_upper else -55
        rad = np.radians(angle)
        ex = sx + bone_length * np.cos(rad)
        ey = 0 + bone_length * np.sin(rad)

        # 大骨线
        fig.add_shape(type='line', x0=sx, y0=0, x1=ex, y1=ey,
                      line=dict(color=col, width=2.5))

        # 分类标签（在大骨末端）
        fig.add_annotation(x=ex + 0.35 * np.cos(rad),
                           y=ey + 0.35 * np.sin(rad),
                           text=f'<b>{cat_name}</b>',
                           showarrow=False,
                           font=dict(size=12, color=col))

        # 小骨（原因）从大骨上延伸
        n_causes = len(causes)
        if n_causes > 0:
            cause_step = bone_length / max(n_causes + 1, 2)
            for j, cause in enumerate(causes):
                # 小骨起点：沿大骨分布
                ratio = (j + 1) / (n_causes + 1) if n_causes > 1 else 0.5
                cx = sx + (ex - sx) * ratio
                cy = 0 + (ey - 0) * ratio

                # 小骨角度（从大骨向外延伸，交替微调）
                cause_angle = angle + (30 if j % 2 == 0 else -30) * (1 if is_upper else -1)
                cause_rad = np.radians(cause_angle)
                ce_x = cx + cause_bone_length * np.cos(cause_rad)
                ce_y = cy + cause_bone_length * np.sin(cause_rad)

                fig.add_shape(type='line', x0=cx, y0=cy, x1=ce_x, y1=ce_y,
                              line=dict(color=col, width=1.2))

                fig.add_annotation(x=ce_x + 0.08 * np.cos(cause_rad),
                                   y=ce_y + 0.08 * np.sin(cause_rad),
                                   text=cause, showarrow=False,
                                   font=dict(size=9, color='#555'))

    # 隐藏坐标轴
    fig.update_xaxes(visible=False, range=[-6, 10])
    fig.update_yaxes(visible=False, range=[-4.5, 4.5])
    fig.update_layout(
        title=f'鱼骨图 — {problem}',
        template='plotly_white',
        height=520,
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )

    return {'chart': fig, 'problem': problem, 'categories': categories}
