"""
高级 MSA — Cg/Cgk 检具能力 / 计数型 GRR (Kappa) / 测量不确定度
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ==================== MSA Type 1 — Cg / Cgk ====================

def cg_cgk(data, tolerance, ref_value=None, n_trials=None):
    """
    MSA Type 1 检具能力指数
    - data: 重复测量值
    - tolerance: 公差 (USL - LSL)
    - ref_value: 参考值/标准值（如未提供则用数据均值）
    - n_trials: 重复次数（如未提供则按全部）
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)

    if n < 5:
        return {'error': '至少需要 5 次测量'}

    mean_val = np.mean(data)
    std_val = np.std(data, ddof=1)
    if ref_value is None:
        ref_value = mean_val

    # 使用 0.2 * tolerance 作为能力范围
    Cap = 0.2 * tolerance
    Cg = Cap / (6 * std_val) if std_val > 0 else np.inf
    Cgk_num = Cap - abs(mean_val - ref_value)
    Cgk = Cgk_num / (3 * std_val) if std_val > 0 else np.inf

    # 评估
    def evaluate(val):
        if val is None or np.isinf(val):
            return 'N/A'
        if val >= 1.33:
            return '优秀 (合格)'
        elif val >= 1.0:
            return '临界 (可接受)'
        else:
            return '不合格 (需改进)'

    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=('重复测量运行图', '偏倚分析'),
                        column_widths=[0.55, 0.45])

    idx = list(range(1, n + 1))

    # 运行图
    fig.add_trace(go.Scatter(x=idx, y=data, mode='lines+markers', name='测量值',
                             marker=dict(color='#1f77b4', size=8)), row=1, col=1)
    mean_line = np.full(n, mean_val)
    fig.add_trace(go.Scatter(x=idx, y=mean_line, mode='lines',
                             name=f'均值={mean_val:.4f}', line=dict(color='green', width=2)), row=1, col=1)
    if ref_value is not None:
        fig.add_hline(y=ref_value, line_dash='dash', line_color='red', row=1, col=1,
                      annotation_text=f'参考={ref_value}')
    fig.add_hline(y=mean_val + 3*std_val, line_dash='dot', line_color='orange', row=1, col=1)
    fig.add_hline(y=mean_val - 3*std_val, line_dash='dot', line_color='orange', row=1, col=1)

    # 偏倚分析柱状图
    bias = abs(mean_val - ref_value)
    bias_pct = (bias / tolerance * 100) if tolerance > 0 else 0
    fig.add_trace(go.Bar(x=['偏倚'], y=[bias], name='偏倚',
                         marker=dict(color='#d62728'), text=[f'{bias:.5f}'], textposition='outside'), row=1, col=2)
    fig.add_hline(y=0.1 * tolerance, line_dash='dash', line_color='orange', row=1, col=2,
                  annotation_text='10%容差')

    fig.update_layout(title='MSA Type 1 — Cg/Cgk 检具能力分析', template='plotly_white', height=400)
    fig.update_xaxes(title_text='测量次数', row=1, col=1)
    fig.update_yaxes(title_text='测量值', row=1, col=1)
    fig.update_yaxes(title_text='', row=1, col=2)

    return {
        'chart': fig,
        'stats': {
            'Cg': f'{Cg:.4f}', 'Cgk': f'{Cgk:.4f}',
            'Cg 评级': evaluate(Cg), 'Cgk 评级': evaluate(Cgk),
        },
        'details': {
            '样本量 n': n, '均值': f'{mean_val:.5f}',
            '标准差 σ': f'{std_val:.5f}',
            '参考值': f'{ref_value:.5f}',
            '公差 T': f'{tolerance:.5f}',
            '能力范围 0.2T': f'{Cap:.5f}',
            '偏倚': f'{bias:.5f}',
            '偏倚占比': f'{bias_pct:.2f}%',
        }
    }


# ==================== 计数型 Gage R&R (Attribute) ====================

def attribute_gage_rr(reference, appraisers, n_trials=2):
    """
    计数型 Gage R&R — 属性一致性分析 (Kappa 法)
    - reference: 参考判定结果 (0/1, OK/NG)
    - appraisers: dict, key=操作员名, value=各次判定结果列表
    - n_trials: 每操作员每零件重复次数
    """
    ref = np.array(reference)
    n_parts = len(ref)
    detected_trials = 1

    results = {}
    kappa_summary = []

    for op_name, decisions in appraisers.items():
        dec = np.array(decisions)

        # 对齐长度
        min_len = min(len(ref), len(dec))
        if min_len < 2:
            continue
        ref_aligned = ref[:min_len]
        dec_aligned = dec[:min_len]

        # 与参考值对比
        match = (dec_aligned == ref_aligned)
        accuracy = np.mean(match)

        # 自动检测实际试验次数
        actual_trials = min_len // n_parts if n_parts > 0 and min_len >= n_parts else 1
        actual_n_parts = min_len // actual_trials if actual_trials > 0 else min_len
        detected_trials = actual_trials  # 记录最后一次检测到的次数

        # 自身一致性（各次试验之间，仅当有多次试验时）
        if actual_trials > 1 and actual_n_parts * actual_trials <= len(dec_aligned):
            trial_cols = dec_aligned[:actual_n_parts * actual_trials].reshape(actual_n_parts, actual_trials)
            self_match = np.all(trial_cols == trial_cols[:, [0]], axis=1)
            self_consistent = np.mean(self_match)
        else:
            self_consistent = 1.0

        # Kappa 计算
        po = accuracy  # observed agreement
        p_ref_pos = np.mean(ref_aligned == 1) if np.any(ref_aligned == 1) else 0
        p_ref_neg = np.mean(ref_aligned == 0) if np.any(ref_aligned == 0) else 0
        p_dec_pos = np.mean(dec_aligned == 1) if np.any(dec_aligned == 1) else 0
        p_dec_neg = np.mean(dec_aligned == 0) if np.any(dec_aligned == 0) else 0
        pe = p_ref_pos * p_dec_pos + p_ref_neg * p_dec_neg  # expected agreement
        kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0

        def kappa_eval(k):
            if k >= 0.9:
                return '几乎完美'
            elif k >= 0.8:
                return '强'
            elif k >= 0.6:
                return '中等'
            elif k >= 0.4:
                return '弱'
            elif k >= 0.0:
                return '轻微'
            else:
                return '差'

        results[op_name] = {
            'accuracy': accuracy,
            'self_consistent': self_consistent,
            'kappa': kappa,
            'kappa_eval': kappa_eval(kappa),
        }
        kappa_summary.append({
            '操作员': op_name,
            '准确性': f'{accuracy:.1%}',
            '自一致性': f'{self_consistent:.1%}',
            'Kappa': f'{kappa:.4f}',
            '评级': kappa_eval(kappa),
        })

    if not results:
        return {'error': '数据量不足，无法进行计数型 GRR 分析'}

    # 全体操作员间一致性
    all_decisions = np.array([appraisers[op] for op in results.keys()])
    n_ops = len(results)
    if n_ops > 1:
        ops_match = np.mean([np.mean(all_decisions[i] == all_decisions[j])
                            for i in range(n_ops) for j in range(i+1, n_ops)])
    else:
        ops_match = 1.0

    # 图表
    fig = go.Figure()
    op_names = list(appraisers.keys())
    kappas = [results[op]['kappa'] for op in op_names]
    accuracies = [results[op]['accuracy'] for op in op_names]
    colors_bar = ['#2ca02c' if k >= 0.8 else ('#ff7f0e' if k >= 0.6 else '#d62728') for k in kappas]

    fig.add_trace(go.Bar(x=op_names, y=kappas, name='Kappa 值', marker=dict(color=colors_bar),
                         text=[f'{k:.3f}' for k in kappas], textposition='outside'))
    fig.add_hline(y=0.9, line_dash='dash', line_color='green', annotation_text='0.9 (几乎完美)')
    fig.add_hline(y=0.6, line_dash='dash', line_color='orange', annotation_text='0.6 (中等)')

    # 添加准确性副轴
    fig.add_trace(go.Scatter(x=op_names, y=accuracies, mode='markers+lines', name='准确性',
                             marker=dict(size=10, symbol='diamond', color='#1f77b4'),
                             line=dict(color='#1f77b4', dash='dash'), yaxis='y2'))

    fig.update_layout(
        title='计数型 GRR — Kappa 统计分析',
        template='plotly_white', height=400,
        yaxis=dict(title='Kappa 值', range=[-0.2, 1.1]),
        yaxis2=dict(title='准确性', overlaying='y', side='right', range=[0, 1.1])
    )
    fig.update_xaxes(title_text='操作员')

    return {
        'chart': fig,
        'kappa_summary': kappa_summary,
        'between_operators_agreement': ops_match,
        'stats_summary': {
            '零件数': n_parts,
            '操作员数': n_ops,
            '重复次数': detected_trials,
            '操作员间一致性': f'{ops_match:.1%}',
        }
    }


# ==================== 测量不确定度 ====================

def measurement_uncertainty(data, resolution=0.001, cal_uncertainty=0.0,
                            temperature_range=5.0, temp_coefficient=0.0):
    """
    测量不确定度评定 (基于 GUM)
    - data: 重复测量数据
    - resolution: 仪器分辨率
    - cal_uncertainty: 校准证书不确定度 (k=2)
    - temperature_range: 温度波动范围 (°C)
    - temp_coefficient: 温度系数 (/°C)
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)

    if n < 3:
        return {'error': '至少需要 3 次测量'}

    mean_val = np.mean(data)
    std_val = np.std(data, ddof=1)

    # A 类评定 — 重复性引入
    u_a = std_val / np.sqrt(n)

    # B 类评定
    u_resolution = resolution / (2 * np.sqrt(3))  # 分辨率
    u_cal = cal_uncertainty / 2                     # 校准 (k=2)
    u_temp = (temp_range * temp_coefficient * mean_val / 2) / np.sqrt(3) if temp_coefficient > 0 else 0  # 温度

    # 合成标准不确定度
    u_combined = np.sqrt(u_a**2 + u_resolution**2 + u_cal**2 + u_temp**2)

    # 扩展不确定度 (k=2, 约95%置信)
    u_expanded = 2 * u_combined

    # 分量贡献
    components = {
        'A类-重复性 uA': (u_a, u_a**2),
        'B类-分辨率 uRes': (u_resolution, u_resolution**2),
        'B类-校准 uCal': (u_cal, u_cal**2),
        'B类-温度 uTemp': (u_temp, u_temp**2),
    }
    total_var = u_combined ** 2

    # 图表 — 不确定度分量占比
    labels = [k for k, v in components.items() if v[1] > 0]
    values = [v[1] for k, v in components.items() if v[1] > 0]

    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=('不确定度分量占比', '测量结果与扩展区间'),
                        column_widths=[0.4, 0.6],
                        specs=[[{'type': 'domain'}, {'type': 'xy'}]])

    if values:
        fig.add_trace(go.Pie(labels=labels, values=values, hole=0.4,
                             textinfo='label+percent'), row=1, col=1)

    # 测量结果区间
    idx = list(range(1, n + 1))
    fig.add_trace(go.Scatter(x=idx, y=data, mode='markers', name='测量值',
                             marker=dict(color='#1f77b4', size=6)), row=1, col=2)
    fig.add_hline(y=mean_val, line_color='green', line_width=2, row=1, col=2,
                  annotation_text=f'均值={mean_val:.5f}')
    fig.add_hline(y=mean_val + u_expanded, line_dash='dash', line_color='red', row=1, col=2)
    fig.add_hline(y=mean_val - u_expanded, line_dash='dash', line_color='red', row=1, col=2)

    fig.update_layout(title='测量不确定度评定', template='plotly_white', height=400)
    fig.update_xaxes(title_text='测量次数', row=1, col=2)
    fig.update_yaxes(title_text='测量值', row=1, col=2)

    return {
        'chart': fig,
        'result': {
            '测量均值': f'{mean_val:.6f}',
            '合成标准不确定度 uc': f'{u_combined:.6f}',
            '扩展不确定度 U (k=2)': f'{u_expanded:.6f}',
            '相对扩展不确定度': f'{u_expanded / mean_val * 100:.4f}%' if mean_val != 0 else 'N/A',
        },
        'budget': {
            k: f'{v[0]:.6f}' for k, v in components.items()
        }
    }
