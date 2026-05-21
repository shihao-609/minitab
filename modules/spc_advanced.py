"""
高级 SPC 控制图 — EWMA / CUSUM / 多变量 Hotelling T²
"""
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ==================== EWMA 控制图 ====================

def ewma_chart(data, lam=0.2, L=2.7):
    """
    EWMA (指数加权移动平均) 控制图
    - lam: 平滑系数 (0 < λ ≤ 1)
    - L: 控制限宽度倍数
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)
    if n < 2:
        return {'error': '至少需要 2 个数据点'}

    # 计算 EWMA 值
    z = np.zeros(n)
    z[0] = data[0]
    for i in range(1, n):
        z[i] = lam * data[i] + (1 - lam) * z[i - 1]

    # 整体均值和标准差
    mu = np.mean(data)
    sigma = np.std(data, ddof=1)

    # EWMA 控制限（稳态）
    # Var(z_i) = sigma² * (λ/(2-λ)) * [1 - (1-λ)^(2i)]
    indices = np.arange(1, n + 1)
    var_multiplier = (lam / (2 - lam)) * (1 - (1 - lam) ** (2 * indices))
    sigma_z = sigma * np.sqrt(var_multiplier)

    ucl = mu + L * sigma_z
    cl = np.full(n, mu)
    lcl = mu - L * sigma_z

    # 标注超限点
    above = z > ucl
    below = z < lcl
    ooc_indices = np.where(above | below)[0]

    fig = go.Figure()
    idx = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=idx, y=data, mode='lines+markers', name='观测值',
                             marker=dict(color='#b0b0b0', size=5), line=dict(color='#ccc', width=1)))
    fig.add_trace(go.Scatter(x=idx, y=z, mode='lines+markers', name=f'EWMA (λ={lam})',
                             marker=dict(color='#1f77b4', size=6), line=dict(color='#1f77b4', width=2)))
    fig.add_trace(go.Scatter(x=idx, y=cl, mode='lines', name=f'CL={mu:.4f}',
                             line=dict(color='green', width=2)))
    fig.add_trace(go.Scatter(x=idx, y=ucl, mode='lines', name='UCL',
                             line=dict(color='red', dash='dash', width=1.5)))
    fig.add_trace(go.Scatter(x=idx, y=lcl, mode='lines', name='LCL',
                             line=dict(color='red', dash='dash', width=1.5)))

    # 高亮超限点
    if len(ooc_indices) > 0:
        fig.add_trace(go.Scatter(x=(ooc_indices + 1).tolist(), y=z[ooc_indices], mode='markers',
                                 name=f'超限点 ({len(ooc_indices)})',
                                 marker=dict(color='red', size=10, symbol='x')))

    fig.update_layout(title=f'EWMA 控制图 (λ={lam}, L={L})', template='plotly_white',
                      height=450, hovermode='x unified')
    fig.update_xaxes(title_text='观测序号')
    fig.update_yaxes(title_text='EWMA 值')

    return {
        'chart': fig,
        'stats': {
            'λ (平滑系数)': lam, 'L (控制限倍数)': L,
            '均值 μ': f'{mu:.4f}', '标准差 σ': f'{sigma:.4f}',
            '超限点数': len(ooc_indices),
            '稳态 UCL': f'{mu + L * sigma * np.sqrt(lam / (2 - lam)):.4f}',
            '稳态 LCL': f'{mu - L * sigma * np.sqrt(lam / (2 - lam)):.4f}',
        }
    }


# ==================== CUSUM 控制图 ====================

def cusum_chart(data, target=None, k=1.0, h=4.0):
    """
    CUSUM (累积和) 控制图 (双侧表格法)
    - target: 目标均值（默认取数据均值）
    - k: 参考值/松弛因子 (通常为 0.5σ)
    - h: 决策区间 (通常为 4~5σ)
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)
    if n < 2:
        return {'error': '至少需要 2 个数据点'}

    if target is None:
        target = np.mean(data)
    sigma = np.std(data, ddof=1)
    # 使用实际 k 和 h
    k_val = k * sigma if sigma > 0 else k
    h_val = h * sigma if sigma > 0 else h

    # CUSUM 双侧累积和
    C_plus = np.zeros(n)
    C_minus = np.zeros(n)
    ooc_plus = []
    ooc_minus = []

    for i in range(n):
        if i == 0:
            C_plus[i] = max(0, data[i] - target - k_val)
            C_minus[i] = max(0, target - data[i] - k_val)
        else:
            C_plus[i] = max(0, C_plus[i - 1] + data[i] - target - k_val)
            C_minus[i] = max(0, C_minus[i - 1] + target - data[i] - k_val)

        if C_plus[i] > h_val:
            ooc_plus.append(i)
            C_plus[i] = 0
        if C_minus[i] > h_val:
            ooc_minus.append(i)
            C_minus[i] = 0

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=('CUSUM 双侧累积和', '原始数据'),
                        vertical_spacing=0.12, row_heights=[0.6, 0.4])

    idx = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=idx, y=C_plus, mode='lines+markers', name='C⁺ (上侧)',
                             marker=dict(color='#d62728', size=4), line=dict(color='#d62728')), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=C_minus, mode='lines+markers', name='C⁻ (下侧)',
                             marker=dict(color='#1f77b4', size=4), line=dict(color='#1f77b4')), row=1, col=1)
    fig.add_hline(y=h_val, line_dash='dash', line_color='red', row=1, col=1,
                  annotation_text=f'H={h_val:.3f}')
    fig.add_hline(y=0, line_color='gray', row=1, col=1)

    fig.add_trace(go.Scatter(x=idx, y=data, mode='lines+markers', name='观测值',
                             marker=dict(color='#2ca02c', size=4), line=dict(color='#2ca02c')), row=2, col=1)
    fig.add_hline(y=target, line_dash='dot', line_color='green', row=2, col=1,
                  annotation_text=f'目标={target:.4f}')

    fig.update_layout(title=f'CUSUM 控制图 (k={k}σ  h={h}σ)', template='plotly_white',
                      height=550, hovermode='x unified')
    fig.update_xaxes(title_text='观测序号', row=2, col=1)
    fig.update_yaxes(title_text='累积和', row=1, col=1)
    fig.update_yaxes(title_text='观测值', row=2, col=1)

    return {
        'chart': fig,
        'stats': {
            '目标值 Target': f'{target:.4f}',
            'σ': f'{sigma:.4f}',
            'k (参考值)': f'{k_val:.4f}',
            'h (决策区间)': f'{h_val:.4f}',
            '上侧报警次数': len(ooc_plus),
            '下侧报警次数': len(ooc_minus),
        }
    }


# ==================== 多变量 Hotelling T² 控制图 ====================

def t2_chart(df, alpha=0.0027):
    """
    Hotelling T² 多变量控制图
    - df: pandas DataFrame（每行一个观测，每列一个变量）
    - alpha: 显著性水平
    """
    from scipy import stats

    df = df.select_dtypes(include=[np.number]).dropna()
    n = len(df)
    p = df.shape[1]

    if p < 2:
        return {'error': 'T² 控制图需要至少 2 个数值变量（多变量分析），当前仅有 1 个，请使用 I-MR 或 EWMA'}
    if n <= p:
        return {'error': f'样本量 ({n}) 必须大于变量数 ({p})'}
    if n < 3:
        return {'error': f'至少需要 3 个观测点，当前仅 {n} 个'}

    X = df.values
    mean_vec = np.mean(X, axis=0)
    S = np.cov(X, rowvar=False)

    # 确保 S 为 2D 矩阵
    S = np.atleast_2d(S)
    if S.shape[0] != p or S.shape[1] != p:
        return {'error': f'协方差矩阵维度异常: {S.shape}，请检查数据'}

    # 检查协方差矩阵是否奇异
    det = np.linalg.det(S)
    if abs(det) < 1e-12:
        return {'error': '协方差矩阵接近奇异（变量间高度线性相关），无法计算 T²'}

    S_inv = np.linalg.inv(S)

    # 计算 T² 统计量
    T2 = np.zeros(n)
    for i in range(n):
        dev = X[i] - mean_vec
        T2[i] = dev @ S_inv @ dev

    # 控制限 (Phase I)
    UCL = (p * (n - 1) * (n + 1)) / (n * (n - p)) * stats.f.ppf(1 - alpha, p, n - p)
    LCL = 0

    ooc = np.sum(T2 > UCL)

    fig = go.Figure()
    idx = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=idx, y=T2, mode='lines+markers', name='T²',
                             marker=dict(color='#1f77b4', size=6), line=dict(color='#1f77b4', width=2)))
    fig.add_hline(y=UCL, line_dash='dash', line_color='red', line_width=2,
                  annotation_text=f'UCL={UCL:.4f}')
    fig.add_hline(y=0, line_color='gray')

    # 标注超限点
    ooc_idx = np.where(T2 > UCL)[0]
    if len(ooc_idx) > 0:
        fig.add_trace(go.Scatter(x=(ooc_idx + 1).tolist(), y=T2[ooc_idx], mode='markers',
                                 name=f'超限 ({len(ooc_idx)})',
                                 marker=dict(color='red', size=10, symbol='x')))

    fig.update_layout(title=f'Hotelling T² 多变量控制图 (p={p}, α={alpha})',
                      template='plotly_white', height=450, hovermode='x unified')
    fig.update_xaxes(title_text='观测序号')
    fig.update_yaxes(title_text='T² 统计量')

    return {
        'chart': fig,
        'stats': {
            '变量数 p': p,
            '样本量 n': n,
            'α': alpha,
            'UCL (控制上限)': f'{UCL:.4f}',
            '超限点数': int(ooc),
            '最大 T²': f'{np.max(T2):.4f}',
        }
    }
