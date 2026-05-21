"""
SPC控制图模块 - 支持各类休哈特控制图
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats


# 控制图常数表 (A2, D3, D4, A3, B3, B4)
def get_control_chart_constants(subgroup_size):
    """返回给定子组大小的控制图常数"""
    constants = {
        2:  {'A2': 1.880, 'D3': 0,     'D4': 3.267, 'A3': 2.659, 'B3': 0,     'B4': 3.267, 'd2': 1.128, 'c4': 0.7979},
        3:  {'A2': 1.023, 'D3': 0,     'D4': 2.574, 'A3': 1.954, 'B3': 0,     'B4': 2.568, 'd2': 1.693, 'c4': 0.8862},
        4:  {'A2': 0.729, 'D3': 0,     'D4': 2.282, 'A3': 1.628, 'B3': 0,     'B4': 2.266, 'd2': 2.059, 'c4': 0.9213},
        5:  {'A2': 0.577, 'D3': 0,     'D4': 2.114, 'A3': 1.427, 'B3': 0,     'B4': 2.089, 'd2': 2.326, 'c4': 0.9400},
        6:  {'A2': 0.483, 'D3': 0,     'D4': 2.004, 'A3': 1.287, 'B3': 0.030, 'B4': 1.970, 'd2': 2.534, 'c4': 0.9515},
        7:  {'A2': 0.419, 'D3': 0.076, 'D4': 1.924, 'A3': 1.182, 'B3': 0.118, 'B4': 1.882, 'd2': 2.704, 'c4': 0.9594},
        8:  {'A2': 0.373, 'D3': 0.136, 'D4': 1.864, 'A3': 1.099, 'B3': 0.185, 'B4': 1.815, 'd2': 2.847, 'c4': 0.9650},
        9:  {'A2': 0.337, 'D3': 0.184, 'D4': 1.816, 'A3': 1.032, 'B3': 0.239, 'B4': 1.761, 'd2': 2.970, 'c4': 0.9693},
        10: {'A2': 0.308, 'D3': 0.223, 'D4': 1.777, 'A3': 0.975, 'B3': 0.284, 'B4': 1.716, 'd2': 3.078, 'c4': 0.9727},
    }
    return constants.get(subgroup_size, constants[5])


def xbar_r_chart(data, subgroup_size=5):
    """X-bar R 控制图 (均值-极差图)"""
    n_samples = len(data)
    n_subgroups = n_samples // subgroup_size
    data = data[:n_subgroups * subgroup_size]
    subgroups = data.reshape(n_subgroups, subgroup_size)

    xbar = np.mean(subgroups, axis=1)
    R = np.ptp(subgroups, axis=1)  # Range = max - min

    xbar_bar = np.mean(xbar)
    R_bar = np.mean(R)

    const = get_control_chart_constants(subgroup_size)

    xbar_ucl = xbar_bar + const['A2'] * R_bar
    xbar_lcl = xbar_bar - const['A2'] * R_bar

    R_ucl = const['D4'] * R_bar
    R_lcl = const['D3'] * R_bar

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        subplot_titles=('X-bar 控制图 (均值图)', 'R 控制图 (极差图)'),
        vertical_spacing=0.12
    )

    subgroup_indices = list(range(1, n_subgroups + 1))

    fig.add_trace(go.Scatter(x=subgroup_indices, y=xbar, mode='lines+markers',
                             name='X̄', marker=dict(color='#1f77b4'), line=dict(color='#1f77b4')), row=1, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[xbar_bar]*n_subgroups, mode='lines',
                             name=f'X̄̄ = {xbar_bar:.4f}', line=dict(color='green', dash='solid', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[xbar_ucl]*n_subgroups, mode='lines',
                             name=f'UCL = {xbar_ucl:.4f}', line=dict(color='red', dash='dash', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[xbar_lcl]*n_subgroups, mode='lines',
                             name=f'LCL = {xbar_lcl:.4f}', line=dict(color='red', dash='dash', width=2)), row=1, col=1)

    fig.add_trace(go.Scatter(x=subgroup_indices, y=R, mode='lines+markers',
                             name='R', marker=dict(color='#ff7f0e'), line=dict(color='#ff7f0e')), row=2, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[R_bar]*n_subgroups, mode='lines',
                             name=f'R̄ = {R_bar:.4f}', line=dict(color='green', dash='solid', width=2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[R_ucl]*n_subgroups, mode='lines',
                             name=f'UCL = {R_ucl:.4f}', line=dict(color='red', dash='dash', width=2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[R_lcl]*n_subgroups, mode='lines',
                             name=f'LCL = {R_lcl:.4f}', line=dict(color='red', dash='dash', width=2)), row=2, col=1)

    fig.update_layout(height=600, hovermode='x unified', template='plotly_white')
    fig.update_xaxes(title_text='子组编号', row=2, col=1)
    fig.update_yaxes(title_text='样本均值', row=1, col=1)
    fig.update_yaxes(title_text='样本极差', row=2, col=1)

    results = {
        'chart': fig,
        'stats': {
            'X\bar_bar': xbar_bar, 'R_bar': R_bar,
            'X_bar_UCL': xbar_ucl, 'X_bar_LCL': xbar_lcl,
            'R_UCL': R_ucl, 'R_LCL': R_lcl,
        }
    }
    return results


def xbar_s_chart(data, subgroup_size=5):
    """X-bar S 控制图 (均值-标准差图)"""
    n_samples = len(data)
    n_subgroups = n_samples // subgroup_size
    data = data[:n_subgroups * subgroup_size]
    subgroups = data.reshape(n_subgroups, subgroup_size)

    xbar = np.mean(subgroups, axis=1)
    S = np.std(subgroups, axis=1, ddof=1)

    xbar_bar = np.mean(xbar)
    S_bar = np.mean(S)

    const = get_control_chart_constants(subgroup_size)

    xbar_ucl = xbar_bar + const['A3'] * S_bar
    xbar_lcl = xbar_bar - const['A3'] * S_bar

    S_ucl = const['B4'] * S_bar
    S_lcl = const['B3'] * S_bar

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        subplot_titles=('X-bar 控制图 (均值图)', 'S 控制图 (标准差图)'),
        vertical_spacing=0.12
    )

    subgroup_indices = list(range(1, n_subgroups + 1))

    fig.add_trace(go.Scatter(x=subgroup_indices, y=xbar, mode='lines+markers',
                             name='X̄', marker=dict(color='#1f77b4')), row=1, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[xbar_bar]*n_subgroups, mode='lines',
                             name=f'X̄̄ = {xbar_bar:.4f}', line=dict(color='green', dash='solid', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[xbar_ucl]*n_subgroups, mode='lines',
                             name=f'UCL = {xbar_ucl:.4f}', line=dict(color='red', dash='dash', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[xbar_lcl]*n_subgroups, mode='lines',
                             name=f'LCL = {xbar_lcl:.4f}', line=dict(color='red', dash='dash', width=2)), row=1, col=1)

    fig.add_trace(go.Scatter(x=subgroup_indices, y=S, mode='lines+markers',
                             name='S', marker=dict(color='#2ca02c')), row=2, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[S_bar]*n_subgroups, mode='lines',
                             name=f'S̄ = {S_bar:.4f}', line=dict(color='green', dash='solid', width=2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[S_ucl]*n_subgroups, mode='lines',
                             name=f'UCL = {S_ucl:.4f}', line=dict(color='red', dash='dash', width=2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=subgroup_indices, y=[S_lcl]*n_subgroups, mode='lines',
                             name=f'LCL = {S_lcl:.4f}', line=dict(color='red', dash='dash', width=2)), row=2, col=1)

    fig.update_layout(height=600, hovermode='x unified', template='plotly_white')
    fig.update_xaxes(title_text='子组编号', row=2, col=1)
    fig.update_yaxes(title_text='样本均值', row=1, col=1)
    fig.update_yaxes(title_text='样本标准差', row=2, col=1)

    results = {
        'chart': fig,
        'stats': {
            'X_bar_bar': xbar_bar, 'S_bar': S_bar,
            'X_bar_UCL': xbar_ucl, 'X_bar_LCL': xbar_lcl,
            'S_UCL': S_ucl, 'S_LCL': S_lcl,
        }
    }
    return results


def imr_chart(data):
    """I-MR 控制图 (单值-移动极差图)"""
    n = len(data)
    I = data
    MR = np.abs(np.diff(data))
    MR = np.insert(MR, 0, np.nan)

    I_bar = np.mean(I[~np.isnan(I)])
    MR_bar = np.mean(MR[1:])  # 排除第一个NaN

    # 移动极差使用 n=2 的常数
    I_ucl = I_bar + 2.66 * MR_bar
    I_lcl = I_bar - 2.66 * MR_bar

    MR_ucl = 3.267 * MR_bar
    MR_lcl = 0

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        subplot_titles=('I 控制图 (单值图)', 'MR 控制图 (移动极差图)'),
        vertical_spacing=0.12
    )

    indices = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=indices, y=I, mode='lines+markers',
                             name='单值', marker=dict(color='#1f77b4')), row=1, col=1)
    fig.add_trace(go.Scatter(x=indices, y=[I_bar]*n, mode='lines',
                             name=f'X̄ = {I_bar:.4f}', line=dict(color='green', dash='solid', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=indices, y=[I_ucl]*n, mode='lines',
                             name=f'UCL = {I_ucl:.4f}', line=dict(color='red', dash='dash', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=indices, y=[I_lcl]*n, mode='lines',
                             name=f'LCL = {I_lcl:.4f}', line=dict(color='red', dash='dash', width=2)), row=1, col=1)

    fig.add_trace(go.Scatter(x=indices, y=MR, mode='lines+markers',
                             name='MR', marker=dict(color='#ff7f0e')), row=2, col=1)
    fig.add_trace(go.Scatter(x=indices, y=[MR_bar]*n, mode='lines',
                             name=f'MR̄ = {MR_bar:.4f}', line=dict(color='green', dash='solid', width=2)), row=2, col=1)
    fig.add_trace(go.Scatter(x=indices, y=[MR_ucl]*n, mode='lines',
                             name=f'UCL = {MR_ucl:.4f}', line=dict(color='red', dash='dash', width=2)), row=2, col=1)

    fig.update_layout(height=600, hovermode='x unified', template='plotly_white')
    fig.update_xaxes(title_text='观测序号', row=2, col=1)
    fig.update_yaxes(title_text='单值', row=1, col=1)
    fig.update_yaxes(title_text='移动极差', row=2, col=1)

    results = {
        'chart': fig,
        'stats': {
            'I_bar': I_bar, 'MR_bar': MR_bar,
            'I_UCL': I_ucl, 'I_LCL': I_lcl,
            'MR_UCL': MR_ucl,
        }
    }
    return results


def p_chart(defectives, sample_sizes):
    """P 控制图 (不合格品率图)"""
    n = len(defectives)
    proportions = np.array(defectives) / np.array(sample_sizes)
    p_bar = np.sum(defectives) / np.sum(sample_sizes)

    ucl = []
    lcl = []
    for ni in sample_sizes:
        sigma = np.sqrt(p_bar * (1 - p_bar) / ni)
        ucl.append(max(p_bar + 3 * sigma, 0))
        lcl.append(max(p_bar - 3 * sigma, 0))

    fig = go.Figure()
    indices = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=indices, y=proportions, mode='lines+markers',
                             name='p', marker=dict(color='#1f77b4')))
    fig.add_trace(go.Scatter(x=indices, y=[p_bar]*n, mode='lines',
                             name=f'p̄ = {p_bar:.4f}', line=dict(color='green', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=ucl, mode='lines',
                             name=f'UCL (变化)', line=dict(color='red', dash='dash', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=lcl, mode='lines',
                             name=f'LCL (变化)', line=dict(color='red', dash='dash', width=2)))

    fig.update_layout(title='P 控制图 (不合格品率)', height=500,
                      hovermode='x unified', template='plotly_white')
    fig.update_xaxes(title_text='样本编号')
    fig.update_yaxes(title_text='不合格品率')

    results = {
        'chart': fig,
        'stats': {
            'p_bar': p_bar,
            'total_samples': np.sum(sample_sizes),
            'total_defectives': np.sum(defectives),
        }
    }
    return results


def np_chart(defectives, sample_size):
    """NP 控制图 (不合格品数图)"""
    n = len(defectives)
    np_bar = np.mean(defectives)
    sigma = np.sqrt(np_bar * (1 - np_bar / sample_size)) if sample_size > 0 else 0

    ucl = np_bar + 3 * sigma
    lcl = max(np_bar - 3 * sigma, 0)

    fig = go.Figure()
    indices = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=indices, y=defectives, mode='lines+markers',
                             name='np', marker=dict(color='#1f77b4')))
    fig.add_trace(go.Scatter(x=indices, y=[np_bar]*n, mode='lines',
                             name=f'np̄ = {np_bar:.2f}', line=dict(color='green', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=[ucl]*n, mode='lines',
                             name=f'UCL = {ucl:.2f}', line=dict(color='red', dash='dash', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=[lcl]*n, mode='lines',
                             name=f'LCL = {lcl:.2f}', line=dict(color='red', dash='dash', width=2)))

    fig.update_layout(title='NP 控制图 (不合格品数)', height=500,
                      hovermode='x unified', template='plotly_white')
    fig.update_xaxes(title_text='样本编号')
    fig.update_yaxes(title_text='不合格品数')

    results = {
        'chart': fig,
        'stats': {
            'np_bar': np_bar, 'UCL': ucl, 'LCL': lcl,
        }
    }
    return results


def c_chart(defects):
    """C 控制图 (缺陷数图)"""
    n = len(defects)
    c_bar = np.mean(defects)
    sigma = np.sqrt(c_bar)

    ucl = c_bar + 3 * sigma
    lcl = max(c_bar - 3 * sigma, 0)

    fig = go.Figure()
    indices = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=indices, y=defects, mode='lines+markers',
                             name='c', marker=dict(color='#1f77b4')))
    fig.add_trace(go.Scatter(x=indices, y=[c_bar]*n, mode='lines',
                             name=f'c̄ = {c_bar:.2f}', line=dict(color='green', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=[ucl]*n, mode='lines',
                             name=f'UCL = {ucl:.2f}', line=dict(color='red', dash='dash', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=[lcl]*n, mode='lines',
                             name=f'LCL = {lcl:.2f}', line=dict(color='red', dash='dash', width=2)))

    fig.update_layout(title='C 控制图 (缺陷数)', height=500,
                      hovermode='x unified', template='plotly_white')
    fig.update_xaxes(title_text='样本编号')
    fig.update_yaxes(title_text='缺陷数')

    results = {
        'chart': fig,
        'stats': {
            'c_bar': c_bar, 'UCL': ucl, 'LCL': lcl,
        }
    }
    return results


def u_chart(defects, sample_sizes):
    """U 控制图 (单位缺陷数图)"""
    n = len(defects)
    u_values = np.array(defects) / np.array(sample_sizes)
    u_bar = np.sum(defects) / np.sum(sample_sizes)

    ucl = []
    lcl = []
    for ni in sample_sizes:
        sigma = np.sqrt(u_bar / ni)
        ucl.append(max(u_bar + 3 * sigma, 0))
        lcl.append(max(u_bar - 3 * sigma, 0))

    fig = go.Figure()
    indices = list(range(1, n + 1))

    fig.add_trace(go.Scatter(x=indices, y=u_values, mode='lines+markers',
                             name='u', marker=dict(color='#1f77b4')))
    fig.add_trace(go.Scatter(x=indices, y=[u_bar]*n, mode='lines',
                             name=f'ū = {u_bar:.4f}', line=dict(color='green', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=ucl, mode='lines',
                             name=f'UCL (变化)', line=dict(color='red', dash='dash', width=2)))
    fig.add_trace(go.Scatter(x=indices, y=lcl, mode='lines',
                             name=f'LCL (变化)', line=dict(color='red', dash='dash', width=2)))

    fig.update_layout(title='U 控制图 (单位缺陷数)', height=500,
                      hovermode='x unified', template='plotly_white')
    fig.update_xaxes(title_text='样本编号')
    fig.update_yaxes(title_text='单位缺陷数')

    results = {
        'chart': fig,
        'stats': {
            'u_bar': u_bar,
            'total_units': np.sum(sample_sizes),
            'total_defects': np.sum(defects),
        }
    }
    return results
