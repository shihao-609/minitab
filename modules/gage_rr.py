"""
量具重复性和再现性 (Gage R&R) 分析模块
支持交叉型 (Crossed) 和平均值-极差法
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def gage_rr_crossed(parts, operators, measurements, tolerance=None):
    """
    交叉型 Gage R&R (平均值-极差法)
    
    参数:
        parts: 部件编号列表
        operators: 操作员列表  
        measurements: 测量值列表
        tolerance: 公差 (USL-LSL), 可选, 用于计算 %Tolerance
    
    返回包含方差分量和图形的字典
    """
    df = pd.DataFrame({
        'Part': parts,
        'Operator': operators,
        'Measurement': measurements
    })

    n_parts = df['Part'].nunique()
    n_operators = df['Operator'].nunique()

    # 校验数据平衡性：所有 Part-Operator 组合的试验次数必须一致
    trial_counts = df.groupby(['Part', 'Operator']).size()
    if trial_counts.nunique() > 1:
        return {'error': '数据不平衡：不同 Part-Operator 组合的试验次数不一致，请检查数据'}
    n_trials = trial_counts.iloc[0]

    # 计算每个部件-操作员组合的平均值和极差
    summary = df.groupby(['Part', 'Operator']).agg(
        avg=('Measurement', 'mean'),
        range_=('Measurement', lambda x: x.max() - x.min())
    ).reset_index()

    # 各操作员的 R_bar
    R_bar_by_op = summary.groupby('Operator')['range_'].mean()
    R_bar = R_bar_by_op.mean()

    # d2常数 (标准表, g ≥ 15): EV用, 因 g = n_parts × n_operators ≥ 15
    d2_table = {2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534,
                7: 2.704, 8: 2.847, 9: 2.970, 10: 3.078}
    d2 = d2_table.get(n_trials, d2_table[3])

    # 重复性 (Repeatability) = Equipment Variation (EV)
    EV = R_bar / d2
    var_EV = EV ** 2

    # 再现性 (Reproducibility) = Appraiser Variation (AV)
    xbar_by_op = summary.groupby('Operator')['avg'].mean()
    X_bar_diff = xbar_by_op.max() - xbar_by_op.min()
    # d₂* 表 (AIAG MSA 第4版, g=1): 操作员均值的极差只有1个子组
    d2_star_ops = {2: 1.414, 3: 1.911, 4: 2.240, 5: 2.481,
                   6: 2.673, 7: 2.830, 8: 2.963, 9: 3.078, 10: 3.179}
    d2_op = d2_star_ops.get(n_operators, 1.911)

    AV_sq = (X_bar_diff / d2_op) ** 2 - var_EV / (n_parts * n_trials)
    AV = np.sqrt(max(AV_sq, 0))
    var_AV = AV ** 2

    # GRR (Gage R&R)
    var_GRR = var_EV + var_AV
    GRR = np.sqrt(var_GRR)

    # 部件间变异 (Part Variation, PV)
    part_avgs = summary.groupby('Part')['avg'].mean()
    Rp = part_avgs.max() - part_avgs.min()

    # d₂* 表 (AIAG MSA 第4版, g=1): 部件均值的极差只有1个子组
    d2_star_parts = {2: 1.414, 3: 1.911, 4: 2.240, 5: 2.481,
                     6: 2.673, 7: 2.830, 8: 2.963, 9: 3.078, 10: 3.179,
                     11: 3.269, 12: 3.350, 13: 3.424, 14: 3.491, 15: 3.553,
                     16: 3.610, 17: 3.663, 18: 3.713, 19: 3.760, 20: 3.804}
    d2_part = d2_star_parts.get(n_parts, 3.179)
    PV = Rp / d2_part
    var_PV = PV ** 2

    # 总变异 (Total Variation, TV)
    var_TV = var_GRR + var_PV
    TV = np.sqrt(var_TV)

    # 各分量的百分比 (%StudyVar = 标准差比值, %Contribution = 方差比值)
    pct_EV = (EV / TV * 100) if TV > 0 else 0
    pct_AV = (AV / TV * 100) if TV > 0 else 0
    pct_GRR = (GRR / TV * 100) if TV > 0 else 0
    pct_PV = (PV / TV * 100) if TV > 0 else 0

    # %Contribution (Minitab 方差分量占比)
    total_var = var_TV if var_TV > 0 else 1
    contrib_EV = var_EV / total_var * 100
    contrib_AV = var_AV / total_var * 100
    contrib_GRR = var_GRR / total_var * 100
    contrib_PV = var_PV / total_var * 100

    # 区分数 (Number of Distinct Categories, ndc)
    ndc = int(np.floor(1.41 * PV / GRR)) if GRR > 0 else np.inf

    # %Tolerance (Minitab: 5.15σ / Tolerance × 100, 5.15σ 覆盖99%测量变异)
    pct_tol = None
    if tolerance is not None and tolerance > 0:
        tol_EV = 5.15 * EV / tolerance * 100
        tol_AV = 5.15 * AV / tolerance * 100
        tol_GRR = 5.15 * GRR / tolerance * 100
        tol_PV = 5.15 * PV / tolerance * 100
        pct_tol = {
            '%Tol EV': f'{tol_EV:.2f}%',
            '%Tol AV': f'{tol_AV:.2f}%',
            '%Tol GRR': f'{tol_GRR:.2f}%',
            '%Tol PV': f'{tol_PV:.2f}%',
        }

    # 评估
    def evaluate_grr(pct):
        if pct < 10:
            return '优秀 (可接受)'
        elif pct < 30:
            return '临界 (可能需要改进)'
        else:
            return '不可接受 (需要改进)'

    # 图表
    chart = gage_rr_chart(df, summary, n_parts, n_operators, n_trials,
                          pct_EV=pct_EV, pct_AV=pct_AV, pct_PV=pct_PV)

    results = {
        'chart': chart,
        'variance_components': {
            '重复性 (EV)': EV, '再现性 (AV)': AV,
            'GRR': GRR, '部件间 (PV)': PV, '总变异 (TV)': TV,
        },
        'stddev_contributions': {
            '重复性 (EV)': f'{EV:.5f}',
            '再现性 (AV)': f'{AV:.5f}',
            'GRR': f'{GRR:.5f}',
            '部件间 (PV)': f'{PV:.5f}',
            '总变异 (TV)': f'{TV:.5f}',
        },
        'percent_studyvar': {  # %StudyVar = 100 × σ_component / σ_total
            '%EV': f'{pct_EV:.2f}%',
            '%AV': f'{pct_AV:.2f}%',
            '%GRR': f'{pct_GRR:.2f}%',
            '%PV': f'{pct_PV:.2f}%',
        },
        'percent_contribution': {  # %Contribution = 100 × σ²_component / σ²_total
            '%EV': f'{contrib_EV:.2f}%',
            '%AV': f'{contrib_AV:.2f}%',
            '%GRR': f'{contrib_GRR:.2f}%',
            '%PV': f'{contrib_PV:.2f}%',
        },
        'percent_tolerance': pct_tol,  # %Tolerance = 100 × 5.15σ / Tolerance
        # 向后兼容: 保留旧键名
        'percent_contributions': {
            '重复性占比 %EV': f'{pct_EV:.2f}%',
            '再现性占比 %AV': f'{pct_AV:.2f}%',
            'GRR占比 %GRR': f'{pct_GRR:.2f}%',
            '部件间占比 %PV': f'{pct_PV:.2f}%',
        },
        'ndc': ndc,
        'evaluation': evaluate_grr(pct_GRR),
        'n_parts': n_parts, 'n_operators': n_operators, 'n_trials': n_trials,
    }
    return results


def gage_rr_chart(df, summary, n_parts, n_operators, n_trials,
                  pct_EV=0, pct_AV=0, pct_PV=0):
    """生成 Gage R&R 分析图表"""
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=[
            f'各操作员测量值分布 (按部件)',
            '部件 × 操作员 交互作用图 (均值)',
            '各操作员平均差值',
            '方差分量占比'
        ],
        specs=[[{'type': 'xy'}, {'type': 'xy'}],
               [{'type': 'xy'}, {'type': 'domain'}]],
        vertical_spacing=0.18, horizontal_spacing=0.10
    )

    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']

    # 图表1: 各操作员箱线图
    for i, op in enumerate(sorted(df['Operator'].unique())):
        op_data = summary[summary['Operator'] == op]['avg']
        fig.add_trace(go.Box(y=op_data, name=f'操作员{op}',
                             marker=dict(color=colors[i % len(colors)]),
                             boxmean='sd'), row=1, col=1)

    # 图表2: 交互作用图
    parts_sorted = sorted(df['Part'].unique())
    for i, op in enumerate(sorted(df['Operator'].unique())):
        op_summary = summary[summary['Operator'] == op].set_index('Part')
        avgs = [op_summary.loc[p, 'avg'] for p in parts_sorted if p in op_summary.index]
        fig.add_trace(go.Scatter(x=parts_sorted[:len(avgs)], y=avgs, mode='lines+markers',
                                 name=f'操作员{op}',
                                 line=dict(color=colors[i % len(colors)]),
                                 marker=dict(size=6)), row=1, col=2)

    # 图表3: 各操作员平均值比较
    op_means = []
    op_names = []
    op_colors_list = []
    for i, op in enumerate(sorted(df['Operator'].unique())):
        op_means.append(summary[summary['Operator'] == op]['avg'].mean())
        op_names.append(f'操作员{op}')
        op_colors_list.append(colors[i % len(colors)])
    fig.add_trace(go.Bar(x=op_names, y=op_means, marker=dict(color=op_colors_list),
                         text=[f'{m:.4f}' for m in op_means], textposition='outside'),
                  row=2, col=1)

    # 图表4: 方差分量占比扇形图（使用实际计算值）
    pie_values = [pct_EV, pct_AV, pct_PV]
    # 如果全部为零则使用等分占位
    if sum(pie_values) <= 0:
        pie_values = [1, 1, 1]
    fig.add_trace(go.Pie(
        labels=['重复性(EV)', '再现性(AV)', '部件间(PV)'],
        values=pie_values,
        marker=dict(colors=['#3498db', '#e74c3c', '#2ecc71']),
        textinfo='label+percent', hole=0.3
    ), row=2, col=2)

    fig.update_layout(height=700, template='plotly_white', showlegend=True)
    fig.update_xaxes(title_text='部件编号', row=1, col=2)
    fig.update_yaxes(title_text='测量平均值', row=1, col=1)
    fig.update_yaxes(title_text='测量平均值', row=1, col=2)

    return fig
