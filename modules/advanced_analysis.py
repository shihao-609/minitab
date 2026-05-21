"""
高级分析 — DOE / Weibull 可靠性 / 抽样方案 / FMEA
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats, special


# ==================== DOE 试验设计 ====================

def doe_full_factorial(df, response_col):
    """
    全因子试验设计分析
    - df: 含因子列和响应列的 DataFrame
    - response_col: 响应变量列名
    """
    factor_cols = [c for c in df.columns if c != response_col]
    if len(factor_cols) < 1:
        return {'error': '至少需要一个因子列'}

    y = df[response_col].values
    n = len(y)

    if n < 4:
        return {'error': '至少需要 4 次试验'}

    # 主效应计算 (2水平简化)
    effects = {}
    for col in factor_cols:
        vals = df[col].unique()
        if len(vals) == 2:
            high = np.mean(y[df[col] == vals[1]])
            low = np.mean(y[df[col] == vals[0]])
            effect = high - low
            effects[col] = (effect, low, high, vals[0], vals[1])

    # 效应帕累托图
    if effects:
        eff_sorted = sorted(effects.items(), key=lambda x: abs(x[1][0]), reverse=True)
        eff_names = [e[0] for e in eff_sorted]
        eff_values = [abs(e[1][0]) for e in eff_sorted]
        eff_raw = [e[1][0] for e in eff_sorted]

        fig = make_subplots(rows=2, cols=1,
                            subplot_titles=('因子效应帕累托图', '主效应图'),
                            vertical_spacing=0.18, row_heights=[0.45, 0.55])

        # 帕累托图
        colors_pareto = ['#d62728' if v > 0 else '#1f77b4' for v in eff_raw]
        fig.add_trace(go.Bar(x=eff_names, y=eff_values, marker=dict(color=colors_pareto),
                             text=[f'{v:.4f}' for v in eff_raw], textposition='outside'), row=1, col=1)
        fig.add_hline(y=np.mean(eff_values) * 2, line_dash='dash', line_color='gray', row=1, col=1,
                      annotation_text='Lenth PSE')

        # 主效应图
        for col_name, (eff, lo, hi, lo_label, hi_label) in eff_sorted:
            fig.add_trace(go.Scatter(x=[str(lo_label), str(hi_label)], y=[lo, hi], mode='lines+markers',
                                     name=col_name, marker=dict(size=10),
                                     line=dict(width=2)), row=2, col=1)

        fig.update_layout(title='DOE 全因子分析', template='plotly_white', height=600)
        fig.update_xaxes(title_text='因子', row=1, col=1)
        fig.update_yaxes(title_text='|效应|', row=1, col=1)
        fig.update_xaxes(title_text='因子水平', row=2, col=1)
        fig.update_yaxes(title_text='响应均值', row=2, col=1)

        return {
            'chart': fig,
            'effects_table': pd.DataFrame({
                '因子': eff_names,
                '效应': [f'{v:.4f}' for v in eff_raw],
                '低水平均值': [f'{e[1]:.4f}' for _, e in eff_sorted],
                '高水平均值': [f'{e[2]:.4f}' for _, e in eff_sorted],
            }),
            'total_runs': n,
            'factors': len(factor_cols),
        }
    else:
        return {'error': '因子需要为2水平设计'}


# ==================== Weibull 可靠性分析 ====================

def weibull_analysis(data, confidence=0.95):
    """
    Weibull 分布拟合 & 可靠性分析
    - data: 失效时间数据
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    data = data[data > 0]
    n = len(data)

    if n < 5:
        return {'error': '至少需要 5 个失效数据点'}

    # 极大似然估计 Weibull 参数
    shape, loc, scale = stats.weibull_min.fit(data, floc=0)

    # 可靠性函数（从微小正值开始，避免 t=0 时除法/幂运算警告）
    t_range = np.linspace(max(np.min(data) * 0.01, 1e-6), np.max(data) * 1.5, 200)
    reliability = np.exp(-(t_range / scale) ** shape)
    pdf_vals = (shape / scale) * (t_range / scale) ** (shape - 1) * np.exp(-(t_range / scale) ** shape)

    # B10 寿命 (10% 失效)
    b10 = scale * (-np.log(0.9)) ** (1 / shape)
    mttf = scale * np.exp(special.gammaln(1 + 1 / shape))  # 平均失效时间
    median_life = scale * (np.log(2)) ** (1 / shape)     # 中位寿命 (B50)

    # 概率图
    sorted_data = np.sort(data)
    ranks = (np.arange(1, n + 1) - 0.3) / (n + 0.4)
    log_data = np.log(sorted_data)
    log_minus_log = np.log(-np.log(1 - ranks))

    fig = make_subplots(rows=2, cols=2,
                        subplot_titles=('Weibull 概率图', '可靠度曲线 R(t)',
                                        '失效概率密度 f(t)', '失效率函数 h(t)'),
                        vertical_spacing=0.15, horizontal_spacing=0.10)

    # Weibull 概率图
    fig.add_trace(go.Scatter(x=log_data, y=log_minus_log, mode='markers', name='数据',
                             marker=dict(color='#1f77b4', size=6)), row=1, col=1)
    fit_y = shape * (log_data - np.log(scale))
    fig.add_trace(go.Scatter(x=log_data, y=fit_y, mode='lines', name='拟合线',
                             line=dict(color='red', width=2)), row=1, col=1)

    # 可靠度曲线
    fig.add_trace(go.Scatter(x=t_range, y=reliability, mode='lines', name='R(t)',
                             line=dict(color='#2ca02c', width=2), fill='tozeroy',
                             fillcolor='rgba(44,160,44,0.1)'), row=1, col=2)
    fig.add_hline(y=0.9, line_dash='dash', line_color='gray', row=1, col=2,
                  annotation_text=f'B10={b10:.1f}')

    # 概率密度
    fig.add_trace(go.Scatter(x=t_range, y=pdf_vals, mode='lines', name='f(t)',
                             line=dict(color='#1f77b4', width=2), fill='tozeroy'), row=2, col=1)
    fig.add_vline(x=mttf, line_dash='dash', line_color='green', row=2, col=1,
                  annotation_text=f'MTTF={mttf:.1f}')

    # 失效率
    h_vals = (shape / scale) * (t_range / scale) ** (shape - 1)
    fig.add_trace(go.Scatter(x=t_range, y=h_vals, mode='lines', name='h(t)',
                             line=dict(color='#d62728', width=2)), row=2, col=2)

    fig.update_layout(title=f'Weibull 可靠性分析 (β={shape:.4f}, η={scale:.4f})',
                      template='plotly_white', height=650, showlegend=False)
    fig.update_xaxes(title_text='ln(t)', row=1, col=1)
    fig.update_yaxes(title_text='ln(-ln(1-F))', row=1, col=1)
    fig.update_xaxes(title_text='时间', row=1, col=2)
    fig.update_yaxes(title_text='可靠度', row=1, col=2)
    fig.update_xaxes(title_text='时间', row=2, col=1)
    fig.update_yaxes(title_text='概率密度', row=2, col=1)
    fig.update_xaxes(title_text='时间', row=2, col=2)
    fig.update_yaxes(title_text='失效率', row=2, col=2)

    return {
        'chart': fig,
        'params': {
            '形状参数 β': f'{shape:.4f}',
            '尺度参数 η': f'{scale:.4f}',
            'MTTF': f'{mttf:.2f}',
            'B10 寿命': f'{b10:.2f}',
            '中位寿命 B50': f'{median_life:.2f}',
        },
        'failures': int(n),
    }


# ==================== 抽样方案 (AQL / LTPD) ====================

def sampling_plan_oc_curve(N, n, c, aql=1.0):
    """
    OC 曲线 / 抽样方案评估
    - N: 批数量
    - n: 样本量
    - c: 合格判定数 (Ac)
    - aql: AQL 值 (%)
    """
    if n <= 0 or c < 0:
        return {'error': '样本量 > 0, 判定数 ≥ 0'}

    # OC 曲线
    p_range = np.linspace(0, min(0.2, c * 3 / n + 0.05), 200)
    Pa = [stats.hypergeom.cdf(c, N, int(p * N), n) if int(p * N) > 0 else 1.0
          for p in p_range]

    # 计算关键点
    def find_p(target_Pa):
        for i, pa in enumerate(Pa):
            if pa < target_Pa:
                if i > 0:
                    return p_range[i - 1] + (p_range[i] - p_range[i - 1]) * (target_Pa - Pa[i - 1]) / (Pa[i] - Pa[i - 1])
                return p_range[i]
        return p_range[-1]

    aql_pa = np.interp(aql / 100, p_range, Pa)
    p_aql = aql / 100

    ltpd_index = np.argmin(np.abs(np.array(Pa) - 0.10))
    ltpd = p_range[ltpd_index]

    # AOQ 和 ATI
    aoq = []
    for i, p in enumerate(p_range):
        if N > n:
            aoq.append(p * Pa[i] * (N - n) / N)
        else:
            aoq.append(p * Pa[i])
    aoql = max(aoq) if aoq else 0

    fig = make_subplots(rows=2, cols=1,
                        subplot_titles=('OC 操作特性曲线', 'AOQ 平均出厂质量曲线'),
                        vertical_spacing=0.15)

    fig.add_trace(go.Scatter(x=p_range * 100, y=Pa, mode='lines', name='OC 曲线',
                             line=dict(color='#1f77b4', width=2),
                             fill='tozeroy', fillcolor='rgba(31,119,180,0.1)'), row=1, col=1)
    fig.add_vline(x=aql, line_dash='dash', line_color='green', row=1, col=1,
                  annotation_text=f'AQL={aql}%')
    fig.add_vline(x=ltpd * 100, line_dash='dash', line_color='red', row=1, col=1,
                  annotation_text=f'LTPD≈{ltpd*100:.1f}%')
    fig.add_hline(y=0.95, line_dash='dot', line_color='green', row=1, col=1)
    fig.add_hline(y=0.10, line_dash='dot', line_color='red', row=1, col=1)

    fig.add_trace(go.Scatter(x=p_range * 100, y=aoq, mode='lines', name='AOQ',
                             line=dict(color='#2ca02c', width=2)), row=2, col=1)
    fig.add_vline(x=ltpd * 100, line_dash='dash', line_color='gray', row=2, col=1)

    fig.update_layout(title=f'抽样方案 OC 曲线 (N={N}, n={n}, Ac={c})',
                      template='plotly_white', height=550)
    fig.update_xaxes(title_text='不合格品率 p (%)', row=2, col=1)
    fig.update_yaxes(title_text='接收概率 Pa', row=1, col=1)
    fig.update_yaxes(title_text='AOQ', row=2, col=1)

    return {
        'chart': fig,
        'stats': {
            '批数量 N': N, '样本量 n': n, '合格判定数 Ac': c,
            'AQL': f'{aql}%',
            f'AQL({aql}%) 处 Pa': f'{aql_pa:.4f}',
            'LTPD (Pa≈0.1)': f'{ltpd*100:.2f}%',
            'AOQL': f'{aoql*100:.4f}%',
        }
    }


# ==================== FMEA ====================

def fmea_analysis(fmea_data):
    """
    FMEA 失效模式与影响分析
    - fmea_data: list of dicts, 每个含 (模式, 严重度S, 发生度O, 探测度D)
    """
    if not fmea_data:
        return {'error': '请输入至少一条 FMEA 记录'}

    df = pd.DataFrame(fmea_data)
    required_cols = ['模式', '严重度', '发生度', '探测度']
    for col in required_cols:
        if col not in df.columns:
            return {'error': f'缺少列: {col}'}

    df['RPN'] = df['严重度'] * df['发生度'] * df['探测度']
    df = df.sort_values('RPN', ascending=False).reset_index(drop=True)

    # 风险评级
    def risk_level(rpn):
        if rpn >= 200:
            return '🔴 高风险'
        elif rpn >= 100:
            return '🟡 中风险'
        else:
            return '🟢 低风险'

    df['风险等级'] = df['RPN'].apply(risk_level)

    # 图表 — RPN 柱状图 + SO 散点
    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=('RPN 排名', '严重度 vs 发生度'),
                        column_widths=[0.55, 0.45])

    colors_rpn = ['#d62728' if r >= 200 else ('#ff7f0e' if r >= 100 else '#2ca02c')
                  for r in df['RPN']]
    fig.add_trace(go.Bar(x=df['模式'], y=df['RPN'], marker=dict(color=colors_rpn),
                         text=df['RPN'].astype(str), textposition='outside'), row=1, col=1)
    fig.add_hline(y=200, line_dash='dash', line_color='red', row=1, col=1,
                  annotation_text='高风险线 200')
    fig.add_hline(y=100, line_dash='dash', line_color='orange', row=1, col=1,
                  annotation_text='中风险线 100')

    # 严重度 vs 发生度 气泡图
    fig.add_trace(go.Scatter(x=df['严重度'], y=df['发生度'], mode='markers+text',
                             marker=dict(size=df['RPN'] / 5, color=colors_rpn,
                                         opacity=0.7, line=dict(width=1, color='#333')),
                             text=df['模式'], textposition='top center',
                             textfont=dict(size=9)), row=1, col=2)

    fig.update_layout(title='FMEA 分析报告', template='plotly_white', height=450)
    fig.update_xaxes(title_text='失效模式', row=1, col=1)
    fig.update_yaxes(title_text='RPN', row=1, col=1)
    fig.update_xaxes(title_text='严重度 S', row=1, col=2, range=[0, 11])
    fig.update_yaxes(title_text='发生度 O', row=1, col=2, range=[0, 11])

    high_risk = len(df[df['RPN'] >= 200])
    med_risk = len(df[(df['RPN'] >= 100) & (df['RPN'] < 200)])
    low_risk = len(df[df['RPN'] < 100])

    return {
        'chart': fig,
        'fmea_df': df,
        'summary': {
            '总失效模式': len(df),
            '高风险 (RPN≥200)': high_risk,
            '中风险 (100≤RPN<200)': med_risk,
            '低风险 (RPN<100)': low_risk,
            '最大 RPN': df['RPN'].max(),
        },
        'top_risks': df.head(3)[['模式', 'RPN', '风险等级']].to_dict('records'),
    }
