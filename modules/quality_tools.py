"""
基本质量工具 — 运行图 (Run Chart) / 鱼骨图 (石川图)
"""
import numpy as np
import plotly.graph_objects as go


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
        exp_runs, std_runs, z_runs, run_test_sig = 0, 0, 0, True  # True 表示全相同值，视为非随机模式

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
            '游程检验': '数据全相等 (无法检验)' if (n1 == 0 or n2 == 0) else ('非随机 (可能存在模式)' if run_test_sig else '随机 (无异常模式)'),
            '趋势检验': f'显著趋势 (p={p_val:.4f})' if has_trend else '无显著趋势',
        }
    }


# ==================== 鱼骨图 (石川图) ====================

def fishbone_diagram(problem, categories):
    """
    鱼骨图 — 经典石川图（大骨约50°角，小骨平行于主干）
    - problem: 问题描述
    - categories: dict
        {大类: [原因1, 原因2, ...]}  或
        {大类: ['原因A', {'二级分类': ['子原因1', '子原因2']}, '原因B']}
    """
    fig = go.Figure()

    cat_names = list(categories.keys())
    n_cats = len(cat_names)
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#9467bd', '#8c564b', '#e377c2',
              '#bcbd22', '#17becf']

    # 主干
    spine_start = -7.0
    spine_end = 7.5
    fig.add_shape(type='line', x0=spine_start, y0=0, x1=spine_end, y1=0,
                  line=dict(color='#333', width=3))

    # 鱼头
    fig.add_trace(go.Scatter(x=[spine_end], y=[0], mode='markers+text',
                             marker=dict(size=28, color='#d62728', symbol='triangle-right'),
                             text=[f'<b>{problem}</b>'], textposition='middle right',
                             textfont=dict(size=14, color='#d62728'),
                             showlegend=False))

    # 分布参数
    usable = spine_end - spine_start - 2.0
    spacing = usable / max(n_cats, 1)
    big_len = 3.2
    small_len = 1.6
    sub_len = 1.0

    for i, (cat_name, causes) in enumerate(categories.items()):
        col = colors[i % len(colors)]
        is_upper = (i % 2 == 0)

        # 大骨终点在主干上
        ex = spine_start + 1.0 + i * spacing

        # 大骨起点：向左外侧延伸，约50°角（不陡）
        angle = 50
        rad = np.radians(angle)
        dx = big_len * np.cos(rad)
        dy = big_len * np.sin(rad)

        if is_upper:
            sx, sy = ex - dx, dy
        else:
            sx, sy = ex - dx, -dy

        # 画大骨
        fig.add_shape(type='line', x0=sx, y0=sy, x1=ex, y1=0,
                      line=dict(color=col, width=2.5))

        # 分类标签（大骨起点外侧 = 最左侧）
        lx = sx - 0.35 * np.cos(rad)
        ly = sy + (0.35 * np.sin(rad) if is_upper else -0.35 * np.sin(rad))
        fig.add_annotation(x=lx, y=ly, text=f'<b>{cat_name}</b>',
                           showarrow=False, font=dict(size=12, color=col))

        if not causes:
            continue

        n = len(causes)
        for j, item in enumerate(causes):
            # 小骨起点：沿大骨均匀分布
            ratio = 0.2 + 0.6 * (j / max(n - 1, 1)) if n > 1 else 0.4
            bx = sx + (ex - sx) * ratio
            by = sy + (0 - sy) * ratio

            # 小骨水平向右延伸（平行于主干，指向鱼头）
            s2x = bx + small_len
            s2y = by

            fig.add_shape(type='line', x0=bx, y0=by, x1=s2x, y1=s2y,
                          line=dict(color=col, width=1.5))

            # 标签上下交替偏移，避免重叠
            label_dy = 0.14 if j % 2 == 0 else -0.14

            if isinstance(item, str):
                fig.add_annotation(x=s2x + 0.1, y=s2y + label_dy, text=item,
                                   showarrow=False, font=dict(size=9, color='#555'))

            elif isinstance(item, dict):
                sub_name = list(item.keys())[0]
                sub_causes = item[sub_name]

                fig.add_annotation(x=s2x + 0.1, y=s2y + label_dy,
                                   text=f'<b>{sub_name}</b>',
                                   showarrow=False, font=dict(size=9, color=col))

                if sub_causes:
                    m = len(sub_causes)
                    for k, sc in enumerate(sub_causes):
                        sub_ratio = 0.3 + 0.5 * (k / max(m - 1, 1)) if m > 1 else 0.5
                        sbx = bx + (s2x - bx) * sub_ratio
                        sby = by

                        ssx = sbx + sub_len
                        ssy = sby

                        fig.add_shape(type='line', x0=sbx, y0=sby, x1=ssx, y1=ssy,
                                      line=dict(color=col, width=1.0))
                        fig.add_annotation(x=ssx + 0.08, y=ssy + 0.03,
                                           text=sc, showarrow=False,
                                           font=dict(size=8, color='#777'))

    # 隐藏坐标轴
    fig.update_xaxes(visible=False, range=[-11, 10])
    fig.update_yaxes(visible=False, range=[-5.5, 5.5])
    fig.update_layout(
        title=f'鱼骨图 — {problem}',
        template='plotly_white',
        height=540,
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )

    return {'chart': fig, 'problem': problem, 'categories': categories}
