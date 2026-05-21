"""
帕累托图和直方图模块
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats


def pareto_chart(categories, counts, title='帕累托图'):
    """帕累托图 - 用柱状图+累积折线显示"""
    df = pd.DataFrame({'类别': categories, '频数': counts})
    df = df.sort_values('频数', ascending=False).reset_index(drop=True)

    total = df['频数'].sum()
    df['占比 (%)'] = (df['频数'] / total * 100)
    df['累积 (%)'] = df['占比 (%)'].cumsum()

    fig = make_subplots(specs=[[{'secondary_y': True}]])

    fig.add_trace(go.Bar(x=df['类别'], y=df['频数'], name='频数',
                         marker=dict(color='#4472C4', opacity=0.8),
                         text=df['频数'], textposition='outside'), secondary_y=False)

    fig.add_trace(go.Scatter(x=df['类别'], y=df['累积 (%)'], name='累积百分比 (%)',
                             mode='lines+markers', line=dict(color='red', width=2),
                             marker=dict(size=6, color='red')), secondary_y=True)

    fig.add_hline(y=80, line_dash='dash', line_color='gray', line_width=1,
                  secondary_y=True, annotation_text='80%线')

    fig.update_layout(title=title, template='plotly_white',
                      hovermode='x unified', height=450)
    fig.update_xaxes(title_text='类别')
    fig.update_yaxes(title_text='频数', secondary_y=False)
    fig.update_yaxes(title_text='累积百分比 (%)', range=[0, 105], secondary_y=True)

    results = {
        'chart': fig,
        'data': df,
        'total': total,
    }
    return results


def histogram_with_stats(data, title='直方图'):
    """带统计摘要的直方图"""
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]

    if len(data) == 0:
        return {'error': '无有效数据'}

    mean_v = np.mean(data)
    median_v = np.median(data)
    std_v = np.std(data, ddof=1)
    skew_v = stats.skew(data)
    kurt_v = stats.kurtosis(data)
    min_v = np.min(data)
    max_v = np.max(data)
    n = len(data)

    # 正态性检验
    if n >= 8:
        stat_val, p_val = stats.normaltest(data)
        is_normal = p_val > 0.05
    elif n >= 3:
        stat_val, p_val = stats.shapiro(data)
        is_normal = p_val > 0.05
    else:
        p_val = None
        is_normal = None

    fig = go.Figure()
    num_bins = min(int(np.sqrt(n)), 30)
    fig.add_trace(go.Histogram(x=data, nbinsx=num_bins, name='数据',
                               marker=dict(color='#4472C4', opacity=0.7,
                                           line=dict(color='white', width=1))))

    # 添加正态拟合曲线
    if std_v > 0:
        x_range = np.linspace(min_v - std_v, max_v + std_v, 200)
        y_normal = stats.norm.pdf(x_range, mean_v, std_v) * n * (x_range[1] - x_range[0]) * num_bins / (
            (max_v + std_v) - (min_v - std_v)) * (max_v - min_v) / num_bins
        scale_factor = n * (max_v - min_v) / num_bins
        y_normal = stats.norm.pdf(x_range, mean_v, std_v) * scale_factor
        fig.add_trace(go.Scatter(x=x_range, y=y_normal, mode='lines',
                                 name=f'正态拟合 (μ={mean_v:.3f})',
                                 line=dict(color='red', width=2)))

    fig.add_vline(x=mean_v, line_color='green', line_width=2, line_dash='solid',
                  annotation_text=f'均值={mean_v:.4f}')
    fig.add_vline(x=median_v, line_color='orange', line_width=2, line_dash='dash',
                  annotation_text=f'中位数={median_v:.4f}')

    fig.update_layout(title=title, template='plotly_white',
                      xaxis_title='数值', yaxis_title='频数',
                      height=400, bargap=0.05)

    stats_summary = {
        '样本量 n': n,
        '均值 Mean': f'{mean_v:.4f}',
        '中位数 Median': f'{median_v:.4f}',
        '标准差 Std': f'{std_v:.4f}',
        '最小值 Min': f'{min_v:.4f}',
        '最大值 Max': f'{max_v:.4f}',
        '偏度 Skewness': f'{skew_v:.4f}',
        '峰度 Kurtosis': f'{kurt_v:.4f}',
    }
    if p_val is not None:
        stats_summary['正态性 p 值'] = f'{p_val:.4f}'
        stats_summary['正态分布'] = '是' if is_normal else '否'

    results = {
        'chart': fig,
        'stats': stats_summary,
    }
    return results


def box_plot(data, group_labels=None, title='箱线图'):
    """箱线图分析"""
    if isinstance(data, np.ndarray):
        data = [data]

    fig = go.Figure()

    for i, d in enumerate(data):
        name = group_labels[i] if group_labels and i < len(group_labels) else f'组 {i+1}'
        fig.add_trace(go.Box(y=d, name=name, boxmean='sd',
                             marker=dict(color='#4472C4')))

    fig.update_layout(title=title, template='plotly_white',
                      yaxis_title='数值', height=400)

    return {'chart': fig}


def scatter_plot(x, y, x_label='X', y_label='Y', title='散点图'):
    """带回归线的散点图"""
    x = np.array(x, dtype=float)
    y = np.array(y, dtype=float)

    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    if len(x) < 3:
        return {'error': '有效数据点不足'}

    # 线性回归
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
    r_squared = r_value ** 2

    x_range = np.linspace(x.min(), x.max(), 100)
    y_fit = slope * x_range + intercept

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode='markers', name='数据点',
                             marker=dict(color='#4472C4', size=8, opacity=0.6)))
    fig.add_trace(go.Scatter(x=x_range, y=y_fit, mode='lines', name='回归线',
                             line=dict(color='red', width=2)))

    fig.update_layout(
        title=f'{title}<br>Y = {slope:.4f}X + {intercept:.4f}, R² = {r_squared:.4f}',
        template='plotly_white', xaxis_title=x_label, yaxis_title=y_label, height=400)

    results = {
        'chart': fig,
        'slope': slope, 'intercept': intercept,
        'r_squared': r_squared, 'r_value': r_value, 'p_value': p_value,
    }
    return results


def normality_test(data, alpha=0.05):
    """正态性检验"""
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]

    if len(data) < 3:
        return {'error': '数据量不足'}

    results = {}

    # Shapiro-Wilk 检验
    if len(data) <= 5000 and len(data) >= 3:
        sw_stat, sw_p = stats.shapiro(data[:5000])  # Shapiro 限制5000以内
        results['Shapiro-Wilk'] = {'statistic': sw_stat, 'p_value': sw_p,
                                   'normal': sw_p > alpha}

    # Anderson-Darling 检验
    ad_stat, ad_crit, ad_significance = stats.anderson(data, dist='norm')
    results['Anderson-Darling'] = {
        'statistic': ad_stat,
        'critical_values': dict(zip(ad_significance, ad_crit)),
        'normal': all(ad_stat < v for v in ad_crit)
    }

    # D'Agostino's K-squared 检验
    if len(data) >= 8:
        k2_stat, k2_p = stats.normaltest(data)
        results["D'Agostino-K2"] = {'statistic': k2_stat, 'p_value': k2_p,
                                     'normal': k2_p > alpha}

    return results
