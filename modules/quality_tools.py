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
    鱼骨图 — 交互式石川图
    - problem: 问题描述
    - categories: dict, {大类: [原因1, 原因2, ...]}
    """
    fig = go.Figure()

    # 主干线 (水平)
    fig.add_shape(type='line', x0=-4.5, y0=0, x1=4.5, y1=0,
                  line=dict(color='#333', width=3))

    # 鱼头 (问题)
    fig.add_trace(go.Scatter(x=[5], y=[0], mode='markers+text',
                             marker=dict(size=30, color='#d62728', symbol='triangle-right'),
                             text=[problem], textposition='middle right',
                             textfont=dict(size=13, color='#d62728'),
                             showlegend=False))

    # 大骨 (分类)
    cat_names = list(categories.keys())
    n_cats = len(cat_names)
    angles = np.linspace(60, 120, n_cats) * np.pi / 180  # 角度分布

    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#8c564b', '#e377c2']

    for i, (cat_name, causes) in enumerate(categories.items()):
        angle = angles[i]
        # 大骨起点在主干上
        spine_x = -2 + i * 6 / max(n_cats - 1, 1)  # 沿主干分布
        dx = 2.5 * np.cos(angle)
        dy = 2.5 * np.sin(angle)

        # 大骨线
        fig.add_shape(type='line', x0=spine_x, y0=0,
                      x1=spine_x + dx, y1=dy,
                      line=dict(color=colors[i % len(colors)], width=2))

        # 分类标签
        fig.add_annotation(x=spine_x + dx + 0.3 * np.cos(angle),
                           y=dy + 0.3 * np.sin(angle),
                           text=f'<b>{cat_name}</b>',
                           showarrow=False, font=dict(size=12, color=colors[i % len(colors)]))

        # 小骨 (原因)
        for j, cause in enumerate(causes):
            cx = spine_x + dx * (0.3 + 0.5 * j / max(len(causes), 1))
            cy = dy * (0.3 + 0.5 * j / max(len(causes), 1))
            small_dx = 0.6 * np.cos(angle + (-1)**j * 0.3)
            small_dy = 0.6 * np.sin(angle + (-1)**j * 0.3)

            fig.add_shape(type='line', x0=cx, y0=cy,
                          x1=cx + small_dx, y1=cy + small_dy,
                          line=dict(color=colors[i % len(colors)], width=1))

            fig.add_annotation(x=cx + small_dx + 0.1,
                               y=cy + small_dy + 0.05,
                               text=cause, showarrow=False,
                               font=dict(size=9, color='#555'))

    # 隐藏坐标轴
    fig.update_xaxes(visible=False, range=[-6, 8])
    fig.update_yaxes(visible=False, range=[-4, 4])
    fig.update_layout(
        title=f'鱼骨图 — {problem}',
        template='plotly_white',
        height=500,
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )

    return {'chart': fig, 'problem': problem, 'categories': categories}
