"""
量具重复性和再现性 (Gage R&R) 分析模块
支持交叉型 (Crossed)：
  - 平均值-极差法 (Mean-Range)   — AIAG MSA 第4版
  - ANOVA 法 (双因素随机效应)      — Method of Moments 方差分量估计
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import f as f_dist


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

    # 校验数据平衡性：所有 Part-Operator 组合必须齐全，且试验次数一致
    trial_counts = df.groupby(['Part', 'Operator']).size()
    if len(trial_counts) < n_parts * n_operators:
        return {'error': '数据不完整：存在缺失的 Part×Operator 组合，请补充该组合的测量数据后再分析'}
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
    d2 = d2_table.get(n_trials)
    if d2 is None:
        return {'error': f'重复试验次数 {n_trials} 超出常数表支持范围 (2~10)，请减少试验次数后再分析'}

    # 重复性 (Repeatability) = Equipment Variation (EV)
    EV = R_bar / d2
    var_EV = EV ** 2

    # 再现性 (Reproducibility) = Appraiser Variation (AV)
    xbar_by_op = summary.groupby('Operator')['avg'].mean()
    X_bar_diff = xbar_by_op.max() - xbar_by_op.min()
    # d₂* 表 (AIAG MSA 第4版, g=1): 操作员均值的极差只有1个子组
    d2_star_ops = {2: 1.414, 3: 1.911, 4: 2.240, 5: 2.481,
                   6: 2.673, 7: 2.830, 8: 2.963, 9: 3.078, 10: 3.179}
    d2_op = d2_star_ops.get(n_operators)
    if d2_op is None:
        return {'error': f'操作员数量 {n_operators} 超出常数表支持范围 (2~10)，请减少操作员数量后再分析'}

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
    d2_part = d2_star_parts.get(n_parts)
    if d2_part is None:
        return {'error': f'部件数量 {n_parts} 超出常数表支持范围 (2~20)，请减少部件数量后再分析'}
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


# ============================================================
# ANOVA 法 Gage R&R (双因素随机效应模型)
# ============================================================

def gage_rr_anova(parts, operators, measurements, tolerance=None, alpha_interaction=0.25):
    """
    ANOVA 法 Gage R&R 分析 — 双因素随机效应方差分析 (Minitab 兼容)

    模型: Y_ijk = μ + P_i + O_j + (PO)_ij + ε_ijk
      P_i ~ N(0, σ²_P)      部件 (随机效应)
      O_j ~ N(0, σ²_O)      操作员 (随机效应)
      (PO)_ij ~ N(0, σ²_PO) 交互效应 (随机)
      ε_ijk ~ N(0, σ²_E)    重复性误差

    相比均值-极差法:
    - 可检测 Part×Operator 交互效应 (F 检验)
    - 方差分量估计更精确 (Method of Moments)
    - 交互不显著时自动合并到误差 (pooling)

    参数:
        parts:           部件编号列表
        operators:       操作员列表
        measurements:    测量值列表
        tolerance:       公差 (USL-LSL), 可选
        alpha_interaction: 交互效应显著性阈值 (默认 0.25, AIAG 推荐)

    返回:
        dict: 包含方差分量、ANOVA 表、图表、评级等
    """
    df = pd.DataFrame({
        'Part': parts, 'Operator': operators, 'Measurement': measurements
    })

    # ─── 1. 数据准备 ───
    n_parts = df['Part'].nunique()
    n_operators = df['Operator'].nunique()

    trial_counts = df.groupby(['Part', 'Operator']).size()
    if len(trial_counts) < n_parts * n_operators:
        return {'error': '数据不完整：存在缺失的 Part×Operator 组合，请补充该组合的测量数据后再分析'}
    if trial_counts.nunique() > 1:
        return {'error': '数据不平衡：不同 Part-Operator 组合的试验次数不一致，请检查数据'}
    n_trials = trial_counts.iloc[0]

    parts_unique = sorted(df['Part'].unique())
    ops_unique = sorted(df['Operator'].unique())
    p, o, r = len(parts_unique), len(ops_unique), n_trials

    # ─── 2. 计算均值 ───
    grand_mean = df['Measurement'].mean()

    part_means = df.groupby('Part')['Measurement'].mean()
    op_means = df.groupby('Operator')['Measurement'].mean()
    cell_means = df.groupby(['Part', 'Operator'])['Measurement'].mean()

    # ─── 3. 平方和分解 (SS) ───
    # SS_Part = o·r·Σ(ȳ_i.. - ȳ... )²
    SS_Part = o * r * sum(
        (part_means[p_] - grand_mean) ** 2 for p_ in parts_unique
    )

    # SS_Operator = p·r·Σ(ȳ_.j. - ȳ... )²
    SS_Operator = p * r * sum(
        (op_means[op_] - grand_mean) ** 2 for op_ in ops_unique
    )

    # SS_Interaction = r·Σ(ȳ_ij. - ȳ_i.. - ȳ_.j. + ȳ... )²
    SS_Interaction = 0
    for p_ in parts_unique:
        for op_ in ops_unique:
            cell_val = cell_means.get((p_, op_), 0)
            SS_Interaction += (cell_val - part_means[p_] - op_means[op_] + grand_mean) ** 2
    SS_Interaction *= r

    # SS_Error = Σ(y_ijk - ȳ_ij.)²
    SS_Error = 0
    for _, row in df.iterrows():
        cell_val = cell_means.get((row['Part'], row['Operator']), 0)
        SS_Error += (row['Measurement'] - cell_val) ** 2

    # SS_Total = SS_Part + SS_Operator + SS_Interaction + SS_Error (用于验证)
    SS_Total = SS_Part + SS_Operator + SS_Interaction + SS_Error

    # ─── 4. 自由度 ───
    df_Part = p - 1
    df_Operator = o - 1
    df_Interaction = (p - 1) * (o - 1)
    df_Error = p * o * (r - 1)
    df_Total = p * o * r - 1

    # ─── 5. 均方 (MS) ───
    def safe_div(a, b):
        return a / b if b > 0 else 0

    MS_Part = safe_div(SS_Part, df_Part)
    MS_Operator = safe_div(SS_Operator, df_Operator)
    MS_Interaction = safe_div(SS_Interaction, df_Interaction)
    MS_Error = safe_div(SS_Error, df_Error)

    # ─── 6. F 检验 ───
    def calc_f_p(ms_num, ms_denom, df_num, df_denom):
        """计算 F 值和 p 值"""
        if ms_denom <= 0 or df_num <= 0 or df_denom <= 0:
            return np.nan, 1.0
        f_val = ms_num / ms_denom
        p_val = 1 - f_dist.cdf(f_val, df_num, df_denom)
        return f_val, p_val

    if r > 1 and df_Interaction > 0:
        # 标准情况：有重复试验，交互效应有自由度
        F_Interaction, p_Interaction = calc_f_p(
            MS_Interaction, MS_Error, df_Interaction, df_Error
        )
        # Part 和 Operator 的 F 检验用交互 MS 作分母（随机效应模型）
        F_Part, p_Part = calc_f_p(
            MS_Part, MS_Interaction, df_Part, df_Interaction
        )
        F_Operator, p_Operator = calc_f_p(
            MS_Operator, MS_Interaction, df_Operator, df_Interaction
        )
    else:
        # r=1 → 无纯误差；交互 MS 即为误差估计
        F_Interaction = np.nan
        p_Interaction = 1.0
        F_Part, p_Part = calc_f_p(MS_Part, MS_Interaction, df_Part, df_Interaction)
        F_Operator, p_Operator = calc_f_p(
            MS_Operator, MS_Interaction, df_Operator, df_Interaction
        )

    # ─── 7. 交互效应显著性判断 ───
    if r > 1 and df_Interaction > 0:
        interaction_significant = (p_Interaction < alpha_interaction)
    else:
        interaction_significant = False  # r=1 时无法分离交互

    # ─── 8. 方差分量估计 (Method of Moments) ───
    if r > 1 and df_Interaction > 0 and interaction_significant:
        # 交互显著 → 保留交互项
        var_E = MS_Error
        var_PO = max((MS_Interaction - MS_Error) / r, 0)
        var_O = max((MS_Operator - MS_Interaction) / (p * r), 0)
        var_P = max((MS_Part - MS_Interaction) / (o * r), 0)
        pooling_msg = None
    else:
        # 交互不显著 或 r=1 → 合并交互到误差
        SS_Error_pooled = SS_Interaction + SS_Error
        df_Error_pooled = df_Interaction + df_Error
        MS_Error_pooled = safe_div(SS_Error_pooled, df_Error_pooled)

        var_E = MS_Error_pooled
        var_PO = 0  # 合并后无独立交互分量
        var_O = max((MS_Operator - MS_Error_pooled) / (p * r), 0)
        var_P = max((MS_Part - MS_Error_pooled) / (o * r), 0)
        if r > 1 and df_Interaction > 0:
            pooling_msg = f'交互效应不显著 (p={p_Interaction:.3f} ≥ {alpha_interaction})，已合并至误差项'
        else:
            pooling_msg = 'r=1，交互项作为误差估计'

    # ─── 9. 合成标准差 ───
    EV = np.sqrt(var_E)                          # 重复性 σ_E
    AV = np.sqrt(var_O + var_PO)                 # 再现性: √(σ²_O + σ²_PO)
    GRR = np.sqrt(var_E + var_O + var_PO)        # σ_GRR
    PV = np.sqrt(var_P)                          # 部件间 σ_P
    TV = np.sqrt(var_E + var_O + var_PO + var_P)  # σ_Total

    var_GRR = var_E + var_O + var_PO
    var_TV = var_GRR + var_P

    # ─── 10. 百分比计算 ───
    pct_EV = (EV / TV * 100) if TV > 0 else 0
    pct_AV = (AV / TV * 100) if TV > 0 else 0
    pct_GRR = (GRR / TV * 100) if TV > 0 else 0
    pct_PV = (PV / TV * 100) if TV > 0 else 0

    total_var = var_TV if var_TV > 0 else 1
    contrib_EV = var_E / total_var * 100
    contrib_AV = (var_O + var_PO) / total_var * 100
    contrib_GRR = var_GRR / total_var * 100
    contrib_PV = var_P / total_var * 100

    ndc = int(np.floor(1.41 * PV / GRR)) if GRR > 0 else np.inf

    # ─── 11. %Tolerance ───
    pct_tol = None
    if tolerance is not None and tolerance > 0:
        pct_tol = {
            '%Tol EV': f'{5.15 * EV / tolerance * 100:.2f}%',
            '%Tol AV': f'{5.15 * AV / tolerance * 100:.2f}%',
            '%Tol GRR': f'{5.15 * GRR / tolerance * 100:.2f}%',
            '%Tol PV': f'{5.15 * PV / tolerance * 100:.2f}%',
        }

    # ─── 12. 评级 ───
    def evaluate_grr(pct):
        if pct < 10:
            return '优秀 (可接受)'
        elif pct < 30:
            return '临界 (可能需要改进)'
        else:
            return '不可接受 (需要改进)'

    # ─── 13. ANOVA 表 ───
    def fmt_p(p_val):
        if np.isnan(p_val):
            return '—'
        if p_val < 0.0001:
            return '<0.0001'
        return f'{p_val:.4f}'

    def fmt_f(f_val):
        if np.isnan(f_val):
            return '—'
        return f'{f_val:.2f}'

    anova_rows = [
        {'来源': '部件 (Part)',       'SS': f'{SS_Part:.6f}',      'df': df_Part,
         'MS': f'{MS_Part:.6f}',      'F': fmt_f(F_Part),          'p': fmt_p(p_Part)},
        {'来源': '操作员 (Operator)',  'SS': f'{SS_Operator:.6f}',  'df': df_Operator,
         'MS': f'{MS_Operator:.6f}',  'F': fmt_f(F_Operator),      'p': fmt_p(p_Operator)},
        {'来源': '部件×操作员',        'SS': f'{SS_Interaction:.6f}','df': df_Interaction,
         'MS': f'{MS_Interaction:.6f}','F': fmt_f(F_Interaction),   'p': fmt_p(p_Interaction)},
        {'来源': '误差 (Repeat.)',     'SS': f'{SS_Error:.6f}',     'df': df_Error,
         'MS': f'{MS_Error:.6f}',     'F': '—',                     'p': '—'},
        {'来源': '合计',              'SS': f'{SS_Total:.6f}',     'df': df_Total,
         'MS': '—',                   'F': '—',                     'p': '—'},
    ]
    anova_table = pd.DataFrame(anova_rows)

    # ─── 14. 图表 ───
    summary = df.groupby(['Part', 'Operator']).agg(
        avg=('Measurement', 'mean'),
        range_=('Measurement', lambda x: x.max() - x.min())
    ).reset_index()

    chart = gage_rr_chart(df, summary, n_parts, n_operators, n_trials,
                          pct_EV=pct_EV, pct_AV=pct_AV, pct_PV=pct_PV)

    # ─── 15. 详细方差分量 ───
    var_detail = {
        'σ²_重复性 (EV²)':         f'{var_E:.8f}',
        'σ²_操作员':               f'{var_O:.8f}',
        'σ²_部件×操作员 (PO²)':    f'{var_PO:.8f}',
        'σ²_再现性 (AV²)':         f'{var_O + var_PO:.8f}',
        'σ²_GRR':                  f'{var_GRR:.8f}',
        'σ²_部件 (PV²)':           f'{var_P:.8f}',
        'σ²_总变异 (TV²)':         f'{var_TV:.8f}',
    }

    # ─── 16. 组装结果 ───
    results = {
        'method': 'ANOVA',
        'chart': chart,
        'anova_table': anova_table,
        'pooling_msg': pooling_msg,
        'interaction_significant': interaction_significant,
        'interaction_p_value': p_Interaction,
        'f_part': F_Part, 'p_part': p_Part,
        'f_operator': F_Operator, 'p_operator': p_Operator,
        'f_interaction': F_Interaction,
        'variance_components': {
            '重复性 (EV)': EV, '再现性 (AV)': AV,
            'GRR': GRR, '部件间 (PV)': PV, '总变异 (TV)': TV,
        },
        'variance_components_detail': var_detail,
        'stddev_contributions': {
            '重复性 (EV)': f'{EV:.5f}',
            '再现性 (AV)': f'{AV:.5f}',
            'GRR': f'{GRR:.5f}',
            '部件间 (PV)': f'{PV:.5f}',
            '总变异 (TV)': f'{TV:.5f}',
        },
        'percent_studyvar': {
            '%EV': f'{pct_EV:.2f}%',
            '%AV': f'{pct_AV:.2f}%',
            '%GRR': f'{pct_GRR:.2f}%',
            '%PV': f'{pct_PV:.2f}%',
        },
        'percent_contribution': {
            '%EV': f'{contrib_EV:.2f}%',
            '%AV': f'{contrib_AV:.2f}%',
            '%GRR': f'{contrib_GRR:.2f}%',
            '%PV': f'{contrib_PV:.2f}%',
        },
        'percent_contributions': {
            '重复性占比 %EV': f'{pct_EV:.2f}%',
            '再现性占比 %AV': f'{pct_AV:.2f}%',
            'GRR占比 %GRR': f'{pct_GRR:.2f}%',
            '部件间占比 %PV': f'{pct_PV:.2f}%',
        },
        'percent_tolerance': pct_tol,
        'ndc': ndc,
        'evaluation': evaluate_grr(pct_GRR),
        'n_parts': n_parts, 'n_operators': n_operators, 'n_trials': n_trials,
    }
    return results
