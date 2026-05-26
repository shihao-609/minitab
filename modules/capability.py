"""
过程能力分析模块 - Cp, Cpk, Pp, Ppk 计算
"""
import numpy as np
from scipy import stats
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# d2 常数表（用于将子组极差转换为组内标准差估计）
D2_CONSTANTS = {
    1:  1.128,   # 单值用移动极差，等同于 n=2 的 d2
    2:  1.128,
    3:  1.693,
    4:  2.059,
    5:  2.326,
    6:  2.534,
    7:  2.704,
    8:  2.847,
    9:  2.970,
    10: 3.078,
}

# c4 常数表（用于将子组样本标准差转换为无偏组内标准差估计, S̄/c₄）
C4_CONSTANTS = {
    2:  0.7979,
    3:  0.8862,
    4:  0.9213,
    5:  0.9400,
    6:  0.9515,
    7:  0.9594,
    8:  0.9650,
    9:  0.9693,
    10: 0.9727,
}


def process_capability(data, usl=None, lsl=None, target=None, subgroup_size=1,
                       within_method='Rbar'):
    """
    过程能力分析
    - Cp/Cpk: 短期能力指数（基于组内变异）
    - Pp/Ppk: 长期能力指数（基于整体变异）

    subgroup_size: 子组大小，1=单值（移动极差法），>1=子组法
    within_method: 组内标准差估计方法 'Rbar' (R̄/d₂) 或 'Sbar' (S̄/c₄)
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)

    if n < 2:
        return {'error': '数据量不足，至少需要2个数据点'}

    mean = np.mean(data)
    std_overall = np.std(data, ddof=1)  # 整体标准差 (长期)
    std_within = estimate_within_sigma(data, subgroup_size, within_method)  # 组内标准差估计

    # 存储原始结果
    raw_results = {'mean': mean, 'std_overall': std_overall, 'std_within': std_within,
                   'n': n, 'data': data, 'subgroup_size': subgroup_size,
                   'within_method': within_method}

    if usl is None and lsl is None:
        raw_results['error'] = '请至少提供一个规格限 (USL 或 LSL)'
        return raw_results

    # 计算 Cp/Cpk (使用组内标准差)
    has_usl = usl is not None
    has_lsl = lsl is not None

    if has_usl and has_lsl:
        Cp = (usl - lsl) / (6 * std_within) if std_within > 0 else np.inf
        Cpl = (mean - lsl) / (3 * std_within) if std_within > 0 else np.inf
        Cpu = (usl - mean) / (3 * std_within) if std_within > 0 else np.inf
        Cpk = min(Cpl, Cpu)
    elif has_lsl:
        Cp = None
        Cpl = (mean - lsl) / (3 * std_within) if std_within > 0 else np.inf
        Cpu = None
        Cpk = Cpl
    else:
        Cp = None
        Cpl = None
        Cpu = (usl - mean) / (3 * std_within) if std_within > 0 else np.inf
        Cpk = Cpu

    # 计算 Pp/Ppk (使用整体标准差)
    if has_usl and has_lsl:
        Pp = (usl - lsl) / (6 * std_overall) if std_overall > 0 else np.inf
        Ppl = (mean - lsl) / (3 * std_overall) if std_overall > 0 else np.inf
        Ppu = (usl - mean) / (3 * std_overall) if std_overall > 0 else np.inf
        Ppk = min(Ppl, Ppu)
    elif has_lsl:
        Pp = None
        Ppl = (mean - lsl) / (3 * std_overall) if std_overall > 0 else np.inf
        Ppu = None
        Ppk = Ppl
    else:
        Pp = None
        Ppl = None
        Ppu = (usl - mean) / (3 * std_overall) if std_overall > 0 else np.inf
        Ppk = Ppu

    # 计算超出规格限的比例 (PPM)
    if lsl is not None:
        ppm_lsl = stats.norm.cdf(lsl, loc=mean, scale=std_overall) * 1_000_000
        obs_lsl = np.sum(data < lsl) / n * 1_000_000
    else:
        ppm_lsl = 0
        obs_lsl = 0
    if usl is not None:
        ppm_usl = (1 - stats.norm.cdf(usl, loc=mean, scale=std_overall)) * 1_000_000
        obs_usl = np.sum(data > usl) / n * 1_000_000
    else:
        ppm_usl = 0
        obs_usl = 0
    ppm_total = ppm_lsl + ppm_usl
    obs_ppm_total = obs_lsl + obs_usl

    # 能力评级
    def evaluate_score(cpk):
        if cpk is None or np.isinf(cpk):
            return 'N/A'
        if cpk >= 1.67:
            return '优秀 (Excellent)'
        elif cpk >= 1.33:
            return '良好 (Good)'
        elif cpk >= 1.00:
            return '尚可 (Capable)'
        elif cpk >= 0.67:
            return '不足 (Marginally Capable)'
        else:
            return '差 (Not Capable)'

    results = {
        **raw_results,
        'Cp': Cp, 'Cpk': Cpk, 'Pp': Pp, 'Ppk': Ppk,
        'Cpl': Cpl, 'Cpu': Cpu, 'Ppl': Ppl, 'Ppu': Ppu,
        'ppm_lsl': ppm_lsl, 'ppm_usl': ppm_usl, 'ppm_total': ppm_total,
        'ppm_observed_total': obs_ppm_total,
        'usl': usl, 'lsl': lsl, 'target': target,
        'cpk_level': evaluate_score(Cpk),
        'ppk_level': evaluate_score(Ppk),
        'chart': capability_chart(data, mean, std_overall, std_within, usl, lsl, target, subgroup_size),
    }
    return results


def estimate_within_sigma(data, subgroup_size=1, method='Rbar'):
    """
    估计组内标准差（短期变异）

    subgroup_size == 1: 单值数据，使用移动极差法（MR̄ / d₂）
    subgroup_size > 1:
        method='Rbar': 子组极差法 R̄ / d₂(n)（Minitab 默认）
        method='Sbar': 子组标准差法 S̄ / c₄(n)，对正态数据更高效
    """
    if len(data) < 2:
        return np.std(data, ddof=1)

    if subgroup_size <= 1:
        # 单值数据：移动极差法（等同于 I-MR 控制图的方法）
        mr = np.abs(np.diff(data))
        mr_bar = np.mean(mr)
        return mr_bar / 1.128  # d2 for n=2

    # 有子组结构
    n = len(data)
    n_subgroups = n // subgroup_size
    if n_subgroups < 2:
        # 数据不足以形成至少2个子组，回退到移动极差法
        mr = np.abs(np.diff(data))
        mr_bar = np.mean(mr)
        return mr_bar / 1.128

    # 截断多余数据，保证完整子组
    data_trimmed = data[:n_subgroups * subgroup_size]
    subgroups = data_trimmed.reshape(n_subgroups, subgroup_size)

    if method == 'Sbar':
        # S̄ / c₄ 方法：每子组样本标准差，修正为无偏估计
        S = np.std(subgroups, axis=1, ddof=1)
        S_bar = np.mean(S)
        c4 = C4_CONSTANTS.get(subgroup_size, 0.9400)
        return S_bar / c4
    else:
        # R̄ / d₂ 方法（默认）
        R = np.ptp(subgroups, axis=1)
        R_bar = np.mean(R)
        d2 = D2_CONSTANTS.get(subgroup_size, 2.326)
        return R_bar / d2


def capability_chart(data, mean, std_overall, std_within, usl, lsl, target, subgroup_size=1):
    """绘制过程能力图"""
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=('过程能力直方图', '正态概率图'),
        vertical_spacing=0.15,
        row_heights=[0.65, 0.35]
    )

    # 直方图
    x_range = np.linspace(min(data) - 3*std_overall, max(data) + 3*std_overall, 200)
    normal_fit = stats.norm.pdf(x_range, mean, std_overall)

    fig.add_trace(go.Histogram(x=data, nbinsx=min(int(len(data)/5), 30),
                               histnorm='probability density', name='数据分布',
                               marker=dict(color='#4472C4', opacity=0.6)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x_range, y=normal_fit, mode='lines',
                             name=f'正态拟合 (整体 σ_overall={std_overall:.4f})',
                             line=dict(color='red', width=2)), row=1, col=1)
    # 组内变异拟合线
    method_label = f'子组极差法' if subgroup_size > 1 else f'移动极差法'
    fig.add_trace(go.Scatter(x=x_range, y=stats.norm.pdf(x_range, mean, std_within),
                             mode='lines',
                             name=f'组内变异 ({method_label}, σ_within={std_within:.4f})',
                             line=dict(color='#2ca02c', width=2, dash='dash')), row=1, col=1)

    # 规格限线
    if lsl is not None:
        fig.add_vline(x=lsl, line_color='red', line_dash='dash', line_width=2,
                      annotation_text=f'LSL={lsl}', row=1, col=1)
    if usl is not None:
        fig.add_vline(x=usl, line_color='red', line_dash='dash', line_width=2,
                      annotation_text=f'USL={usl}', row=1, col=1)
    if target is not None:
        fig.add_vline(x=target, line_color='blue', line_dash='dot', line_width=2,
                      annotation_text=f'目标={target}', row=1, col=1)

    # 正态概率图 (Q-Q plot)
    sorted_data = np.sort(data)
    n = len(data)
    theoretical_quantiles = stats.norm.ppf((np.arange(1, n+1) - 0.5) / n,
                                           loc=mean, scale=std_overall)

    fig.add_trace(go.Scatter(x=theoretical_quantiles, y=sorted_data, mode='markers',
                             name='Q-Q Plot', marker=dict(size=5, color='#4472C4')), row=2, col=1)
    min_val = min(theoretical_quantiles.min(), sorted_data.min())
    max_val = max(theoretical_quantiles.max(), sorted_data.max())
    fig.add_trace(go.Scatter(x=[min_val, max_val], y=[min_val, max_val], mode='lines',
                             name='参考线', line=dict(color='gray', dash='dash')), row=2, col=1)

    fig.update_layout(height=600, template='plotly_white', showlegend=True)
    fig.update_xaxes(title_text='数值', row=1, col=1)
    fig.update_xaxes(title_text='理论分位数', row=2, col=1)
    fig.update_yaxes(title_text='密度', row=1, col=1)
    fig.update_yaxes(title_text='实际分位数', row=2, col=1)

    return fig


def process_capability_boxcox(data, usl=None, lsl=None, target=None,
                              subgroup_size=1, within_method='Rbar'):
    """
    非正态过程能力分析 (Box-Cox 变换法, Minitab 兼容)

    使用 Box-Cox 幂变换将数据转换为近似正态分布,
    在变换后的尺度上计算 Cp/Cpk/Pp/Ppk。

    参数同 process_capability。
    """
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)

    if n < 5:
        return {'error': 'Box-Cox 变换至少需要 5 个数据点'}

    if usl is None and lsl is None:
        return {'error': '请至少提供一个规格限 (USL 或 LSL)'}

    # 需求: 所有数据必须 > 0 (Box-Cox 要求)
    if np.any(data <= 0):
        # 尝试平移: 使 min = 0.001
        shift = abs(min(data)) + 0.001 if min(data) <= 0 else 0
        data_shifted = data + shift
        if usl is not None:
            usl_shifted = usl + shift
        else:
            usl_shifted = None
        if lsl is not None:
            lsl_shifted = lsl + shift
        else:
            lsl_shifted = None
        shift_msg = f'(数据平移 +{shift:.4f})'
    else:
        data_shifted = data
        usl_shifted = usl
        lsl_shifted = lsl
        shift_msg = ''

    # 寻找最优 λ (最大似然)
    from scipy.stats import boxcox
    try:
        fitted_data, lam = boxcox(data_shifted.flatten())
    except Exception:
        return {'error': 'Box-Cox 变换失败，数据可能不适合变换'}

    # 变换规格限
    if usl_shifted is not None:
        usl_t = boxcox_transform(usl_shifted, lam)
    else:
        usl_t = None
    if lsl_shifted is not None:
        lsl_t = boxcox_transform(lsl_shifted, lam)
    else:
        lsl_t = None

    # 在变换后尺度计算标准能力指数
    result = process_capability(fitted_data, usl_t, lsl_t, target=None,
                                subgroup_size=subgroup_size,
                                within_method=within_method)

    if 'error' in result:
        return result

    # 附上变换信息
    result['transformation'] = {
        'method': 'Box-Cox',
        'lambda': f'{lam:.4f}',
        'shift': shift_msg,
    }

    # 更新图表标题
    result['chart'].update_layout(
        title=f'非正态过程能力分析 (Box-Cox 变换, λ={lam:.4f}) {shift_msg}')

    # 标记为变换后的结果
    result['is_transformed'] = True

    return result


def boxcox_transform(value, lam):
    """对单个值应用 Box-Cox 变换"""
    if abs(lam) < 1e-10:
        return np.log(value)
    return (value ** lam - 1) / lam
