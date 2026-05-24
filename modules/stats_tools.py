"""
统计推断工具 — 假设检验 / 多元回归 / 相关性矩阵
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy import stats


# ==================== 假设检验 ====================

def t_test_one_sample(data, mu0=0):
    """单样本 t 检验"""
    data = np.array(data, dtype=float)
    data = data[~np.isnan(data)]
    n = len(data)
    if n < 3:
        return {'error': '至少需要 3 个数据点'}

    t_stat, p_val = stats.ttest_1samp(data, mu0)
    mean_val = np.mean(data)
    std_val = np.std(data, ddof=1)
    ci = stats.t.interval(0.95, n - 1, loc=mean_val, scale=std_val / np.sqrt(n))

    return {
        't_stat': t_stat,
        'p_val': p_val,
        'mean': mean_val,
        'std': std_val,
        'n': n,
        'ci_95': ci,
        'mu0': mu0,
        'significant': p_val < 0.05,
    }


def t_test_two_sample(data1, data2, paired=False):
    """双样本 t 检验（独立或配对）"""
    d1 = np.array(data1, dtype=float)
    d2 = np.array(data2, dtype=float)
    d1, d2 = d1[~np.isnan(d1)], d2[~np.isnan(d2)]

    if len(d1) < 3 or len(d2) < 3:
        return {'error': '每组至少需要 3 个数据点'}

    if paired:
        min_len = min(len(d1), len(d2))
        d1, d2 = d1[:min_len], d2[:min_len]
        t_stat, p_val = stats.ttest_rel(d1, d2)
        test_type = '配对'
    else:
        t_stat, p_val = stats.ttest_ind(d1, d2, equal_var=False)
        test_type = '独立'

    return {
        't_stat': t_stat, 'p_val': p_val, 'test_type': test_type,
        'mean1': np.mean(d1), 'mean2': np.mean(d2),
        'std1': np.std(d1, ddof=1), 'std2': np.std(d2, ddof=1),
        'n1': len(d1), 'n2': len(d2),
        'significant': p_val < 0.05,
    }


def one_way_anova(groups_dict):
    """单因素方差分析 (ANOVA)"""
    groups = [np.array(v, dtype=float) for v in groups_dict.values()]
    groups = [g[~np.isnan(g)] for g in groups]
    if any(len(g) < 2 for g in groups):
        return {'error': '每组至少需要 2 个数据点'}

    f_stat, p_val = stats.f_oneway(*groups)
    names = list(groups_dict.keys())

    # 组间/组内平方和
    all_data = np.concatenate(groups)
    grand_mean = np.mean(all_data)
    ss_between = sum(len(g) * (np.mean(g) - grand_mean)**2 for g in groups)
    ss_within = sum(np.sum((g - np.mean(g))**2) for g in groups)
    ss_total = ss_between + ss_within

    df_between = len(groups) - 1
    df_within = len(all_data) - len(groups)

    ms_between = ss_between / df_between
    ms_within = ss_within / df_within

    # 事后比较 (Tukey HSD 简化版)
    group_means = [np.mean(g) for g in groups]
    group_stds = [np.std(g, ddof=1) for g in groups]
    group_ns = [len(g) for g in groups]

    return {
        'f_stat': f_stat, 'p_val': p_val, 'significant': p_val < 0.05,
        'anova_table': {
            '来源': ['组间', '组内', '总计'],
            'SS': [f'{ss_between:.4f}', f'{ss_within:.4f}', f'{ss_total:.4f}'],
            'df': [df_between, df_within, df_between + df_within],
            'MS': [f'{ms_between:.4f}', f'{ms_within:.4f}', '—'],
            'F': [f'{f_stat:.4f}', '—', '—'],
            'p': [f'{p_val:.6f}', '—', '—'],
        },
        'group_stats': {n: {'均值': f'{m:.4f}', '标准差': f'{s:.4f}', 'n': ns}
                        for n, m, s, ns in zip(names, group_means, group_stds, group_ns)},
        'chart': anova_chart(groups, names),
    }


def anova_chart(groups, names):
    """ANOVA 箱线图"""
    fig = go.Figure()
    for i, (g, name) in enumerate(zip(groups, names)):
        fig.add_trace(go.Box(y=g, name=name, boxmean='sd', marker=dict(color=[
            '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b'
        ][i % 6])))

    fig.update_layout(title='各组分布比较 (Box Plot)', template='plotly_white', height=400)
    fig.update_xaxes(title_text='组别')
    fig.update_yaxes(title_text='数值')
    return fig


def equal_variance_test(groups_dict):
    """等方差检验 (Levene + Bartlett)"""
    groups = [np.array(v, dtype=float) for v in groups_dict.values()]
    groups = [g[~np.isnan(g)] for g in groups]
    if any(len(g) < 2 for g in groups):
        return {'error': '每组至少需要 2 个数据点'}

    names = list(groups_dict.keys())
    l_stat, l_p = stats.levene(*groups)
    b_stat, b_p = stats.bartlett(*groups)

    return {
        'Levene': {'statistic': l_stat, 'p_value': l_p, 'equal': l_p > 0.05},
        'Bartlett': {'statistic': b_stat, 'p_value': b_p, 'equal': b_p > 0.05},
        'group_stds': {n: f'{np.std(g, ddof=1):.4f}' for n, g in zip(names, groups)},
    }


# ==================== 多元线性回归 ====================

def multiple_regression(df, y_col):
    """
    多元线性回归
    - df: DataFrame
    - y_col: 因变量列名
    """
    X_cols = [c for c in df.columns if c != y_col]
    numeric_X = df[X_cols].select_dtypes(include=[np.number])
    if numeric_X.shape[1] == 0:
        return {'error': '没有可用的数值自变量'}

    y = df[y_col].values
    X = numeric_X.values

    mask = ~(np.isnan(y) | np.any(np.isnan(X), axis=1))
    y, X = y[mask], X[mask]

    if len(y) < 5 or len(numeric_X.columns) > len(y) - 2:
        return {'error': '数据量不足'}

    X_with_const = np.column_stack([np.ones(len(y)), X])
    n, k = X_with_const.shape

    try:
        beta = np.linalg.lstsq(X_with_const, y, rcond=None)[0]
        y_pred = X_with_const @ beta
        residuals = y - y_pred

        ss_res = np.sum(residuals**2)
        ss_tot = np.sum((y - np.mean(y))**2)
        r_squared = 1 - ss_res / ss_tot
        adj_r2 = 1 - (1 - r_squared) * (n - 1) / (n - k)

        # 标准误差
        sigma2 = ss_res / (n - k)
        cov_matrix = sigma2 * np.linalg.inv(X_with_const.T @ X_with_const)
        se = np.sqrt(np.diag(cov_matrix))
        t_stats = beta / se
        p_values = 2 * (1 - stats.t.cdf(np.abs(t_stats), n - k))

        coef_df = pd.DataFrame({
            '变量': ['截距'] + list(numeric_X.columns),
            '系数': beta,
            '标准误': se,
            't 值': t_stats,
            'p 值': p_values,
        })

        # 残差图
        fig = make_subplots(rows=1, cols=2,
                            subplot_titles=('实际值 vs 预测值', '残差图'),
                            column_widths=[0.5, 0.5])

        fig.add_trace(go.Scatter(x=y, y=y_pred, mode='markers', name='预测',
                                 marker=dict(color='#1f77b4', opacity=0.6)), row=1, col=1)
        rng = [min(y.min(), y_pred.min()), max(y.max(), y_pred.max())]
        fig.add_trace(go.Scatter(x=rng, y=rng, mode='lines', name='完美预测',
                                 line=dict(color='gray', dash='dash')), row=1, col=1)

        fig.add_trace(go.Scatter(x=y_pred, y=residuals, mode='markers', name='残差',
                                 marker=dict(color='#d62728', opacity=0.6)), row=1, col=2)
        fig.add_hline(y=0, line_color='gray', row=1, col=2)

        fig.update_layout(title=f'多元回归 (R²={r_squared:.4f}, Adj R²={adj_r2:.4f})',
                          template='plotly_white', height=400)
        fig.update_xaxes(title_text='实际值', row=1, col=1)
        fig.update_yaxes(title_text='预测值', row=1, col=1)
        fig.update_xaxes(title_text='预测值', row=1, col=2)
        fig.update_yaxes(title_text='残差', row=1, col=2)

        return {
            'chart': fig,
            'coef_df': coef_df,
            'r_squared': r_squared,
            'adj_r2': adj_r2,
            'n': n,
            'fitted': True,
        }
    except Exception as e:
        return {'error': f'回归失败: {str(e)}'}


# ==================== 多 Y-X 批量回归对比 ====================

def yx_pair_analysis(df, y_cols, x_cols, show_scatter_grid=False, exclude_self=False):
    """
    多 Y vs 多 X 批量回归对比分析

    对每一对 (Y, X) 做一元线性回归，汇总 R² / p值 / 斜率 / 截距，
    同时输出 R² 热力图和可选的散点图网格。

    Parameters
    ----------
    df : pd.DataFrame
        数据
    y_cols : list[str]
        因变量列名列表
    x_cols : list[str]
        自变量列名列表
    show_scatter_grid : bool
        是否生成散点图网格
    exclude_self : bool
        是否排除 Y==X 的自对比对（一键全对比模式用）

    Returns
    -------
    dict:
        - summary_df : 汇总表 DataFrame（行 = Y-X 对）
        - heatmap : R² 热力图 Plotly Figure（行=Y, 列=X）
        - scatter_grid : 散点图网格（仅 show_scatter_grid=True 时返回）
        - error : 错误信息（如有）
    """
    if not y_cols or not x_cols:
        return {'error': '请选择至少一个 Y 变量和一个 X 变量'}

    # 验证列名存在于 DataFrame 中
    missing_y = [c for c in y_cols if c not in df.columns]
    missing_x = [c for c in x_cols if c not in df.columns]
    if missing_y:
        return {'error': f'Y 变量不存在: {", ".join(missing_y)}'}
    if missing_x:
        return {'error': f'X 变量不存在: {", ".join(missing_x)}'}

    rows = []
    for yc in y_cols:
        for xc in x_cols:
            # 一键全对比模式下跳过自对比
            if exclude_self and yc == xc:
                continue

            # 提取有效数据对
            x_vals = df[xc].values
            y_vals = df[yc].values
            mask = ~(np.isnan(x_vals) | np.isnan(y_vals))
            x_clean = x_vals[mask]
            y_clean = y_vals[mask]
            n_valid = len(x_clean)

            if n_valid < 3:
                rows.append({
                    'Y': yc, 'X': xc, 'n': n_valid,
                    'R²': None, '调整R²': None, 'Pearson r': None,
                    '斜率': None, '截距': None,
                    'p值': None, '显著性': '数据不足',
                })
                continue

            slope, intercept, r_value, p_value, std_err = stats.linregress(x_clean, y_clean)
            r_squared = r_value ** 2
            adj_r2 = 1 - (1 - r_squared) * (n_valid - 1) / max(n_valid - 2, 1)
            significance = '✓ 显著' if p_value < 0.05 else '不显著'

            rows.append({
                'Y': yc,
                'X': xc,
                'n': n_valid,
                'R²': round(r_squared, 4),
                '调整R²': round(adj_r2, 4),
                'Pearson r': round(r_value, 4),
                '斜率': round(slope, 4),
                '截距': round(intercept, 4),
                'p值': round(p_value, 6),
                '显著性': significance,
            })

    summary_df = pd.DataFrame(rows)

    # ============ R² 热力图 ============
    r2_matrix = np.full((len(y_cols), len(x_cols)), np.nan)
    for i, yc in enumerate(y_cols):
        for j, xc in enumerate(x_cols):
            if exclude_self and yc == xc:
                continue
            row = summary_df[(summary_df['Y'] == yc) & (summary_df['X'] == xc)]
            if len(row) > 0 and row['R²'].values[0] is not None:
                r2_matrix[i, j] = row['R²'].values[0]

    heatmap_fig = go.Figure(data=go.Heatmap(
        z=r2_matrix,
        x=x_cols,
        y=y_cols,
        colorscale='RdYlGn',
        zmin=0, zmax=1,
        text=np.round(r2_matrix, 3),
        texttemplate='%{text}',
        textfont=dict(size=12),
        colorbar=dict(title='R²'),
        hovertemplate='Y=%{y}<br>X=%{x}<br>R²=%{z:.4f}<extra></extra>',
    ))
    heatmap_fig.update_layout(
        title='多 Y-X 回归 R² 热力图',
        template='plotly_white',
        height=max(200, 60 * len(y_cols) + 80),
        xaxis=dict(title='X 变量', side='bottom'),
        yaxis=dict(title='Y 变量'),
    )

    result = {
        'summary_df': summary_df,
        'heatmap': heatmap_fig,
        'n_pairs': len(rows),
        'n_significant': sum(1 for r in rows if r.get('显著性') == '✓ 显著'),
    }

    # ============ 散点图网格（可选） ============
    if show_scatter_grid:
        n_y, n_x = len(y_cols), len(x_cols)
        subplot_titles = [f'{yc} vs {xc}' for yc in y_cols for xc in x_cols]

        scatter_fig = make_subplots(
            rows=n_y, cols=n_x,
            subplot_titles=subplot_titles,
            horizontal_spacing=0.06,
            vertical_spacing=0.08,
        )

        for i, yc in enumerate(y_cols):
            for j, xc in enumerate(x_cols):
                if exclude_self and yc == xc:
                    continue

                x_vals = df[xc].values
                y_vals = df[yc].values
                mask = ~(np.isnan(x_vals) | np.isnan(y_vals))
                x_clean = x_vals[mask]
                y_clean = y_vals[mask]

                if len(x_clean) < 3:
                    continue

                slope, intercept, r_value, p_value, _ = stats.linregress(x_clean, y_clean)
                r_squared = r_value ** 2

                x_ln = np.linspace(x_clean.min(), x_clean.max(), 80)
                y_ln = slope * x_ln + intercept

                scatter_fig.add_trace(
                    go.Scatter(x=x_clean, y=y_clean, mode='markers',
                               marker=dict(size=4, opacity=0.5, color='#4472C4'),
                               showlegend=False,
                               hovertemplate=f'{xc}=%{{x:.3f}}<br>{yc}=%{{y:.3f}}<extra></extra>'),
                    row=i + 1, col=j + 1,
                )
                scatter_fig.add_trace(
                    go.Scatter(x=x_ln, y=y_ln, mode='lines',
                               line=dict(color='red', width=1.5),
                               showlegend=False,
                               name=f'R²={r_squared:.2f}'),
                    row=i + 1, col=j + 1,
                )

        scatter_fig.update_layout(
            title='多 Y-X 散点图矩阵（含回归线）',
            template='plotly_white',
            height=280 * n_y + 60,
            width=None,
        )
        result['scatter_grid'] = scatter_fig

    return result


# ==================== 相关性矩阵 ====================

def correlation_matrix(df):
    """
    相关系数矩阵 & 热力图
    """
    numeric_df = df.select_dtypes(include=[np.number])
    if numeric_df.shape[1] < 2:
        return {'error': '至少需要两个数值列'}

    corr = numeric_df.corr()
    cols = corr.columns.tolist()

    # 热力图
    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=('相关系数热力图', '相关系数柱状图'),
                        column_widths=[0.55, 0.45],
                        specs=[[{'type': 'heatmap'}, {'type': 'xy'}]])

    fig.add_trace(go.Heatmap(
        z=corr.values,
        x=cols, y=cols,
        colorscale='RdBu_r',
        zmin=-1, zmax=1,
        text=np.round(corr.values, 3),
        texttemplate='%{text}',
        colorbar=dict(title='r', x=0.47),
    ), row=1, col=1)

    # 柱状图展示各变量与第一变量的相关性
    if len(cols) > 1:
        ref_col = cols[0]
        corr_with_ref = corr[ref_col].drop(ref_col)
        colors_bars = ['#2ca02c' if v > 0 else '#d62728' for v in corr_with_ref.values]
        fig.add_trace(go.Bar(x=corr_with_ref.index.tolist(), y=corr_with_ref.values,
                             marker=dict(color=colors_bars),
                             text=[f'{v:.3f}' for v in corr_with_ref.values],
                             textposition='outside'), row=1, col=2)
        fig.add_hline(y=0, line_color='gray', row=1, col=2)

    fig.update_layout(title='相关性矩阵分析', template='plotly_white', height=450)
    fig.update_yaxes(title_text='与 ' + cols[0] + ' 的相关系数', row=1, col=2)

    return {
        'chart': fig,
        'corr_df': corr,
        'n': len(numeric_df),
        'n_cols': len(cols),
    }
