"""
Western Electric / Nelson 判异规则模块
=======================================
实现全部 8 条 Nelson 规则，用于 SPC 控制图的异常模式自动检测。
参考: Lloyd S. Nelson, "The Shewhart Control Chart—Tests for Special Causes"
"""

import numpy as np


def apply_all_rules(data, center, ucl, lcl, sigma, n_consecutive_same_side=7,
                    n_trend=6, n_alternating=14):
    """
    对一组数据应用全部 8 条 Nelson 判异规则。

    参数:
        data: 1D numpy array，数据点序列
        center: 中心线值 (CL)
        ucl: 控制上限 (UCL)，可以是标量或与 data 等长的数组
        lcl: 控制下限 (LCL)，可以是标量或与 data 等长的数组
        sigma: 过程标准差 σ
        n_consecutive_same_side: 同侧连续点数阈值（默认7）
        n_trend: 趋势点数阈值（默认6）
        n_alternating: 交替点数阈值（默认14）

    返回:
        dict: {
            'violations': {规则编号: {'description': str, 'indices': [违规点索引列表]}},
            'total_violations': 总违规规则数,
            'ooc_points': 超出控制限的点索引列表
        }
    """
    n = len(data)
    violations = {}

    # 确保 ucl/lcl 是数组
    if np.isscalar(ucl):
        ucl_arr = np.full(n, ucl)
    else:
        ucl_arr = np.asarray(ucl)
    if np.isscalar(lcl):
        lcl_arr = np.full(n, lcl)
    else:
        lcl_arr = np.asarray(lcl)

    # sigma 统一为标量（规则5-8 需要标量 sigma）
    if sigma is not None:
        if not np.isscalar(sigma):
            sigma = float(np.mean(sigma))  # 取平均作为代表值

    # ---- 规则 1: 任一点超出 3σ 控制限 ----
    above = data > ucl_arr
    below = data < lcl_arr
    ooc = np.where(above | below)[0]
    if len(ooc) > 0:
        violations[1] = {
            'description': f'超出控制限: {len(ooc)} 个点',
            'indices': ooc.tolist(),
            'detail': [f'点 {i+1}: 值={data[i]:.4f} (UCL={ucl_arr[i]:.4f}, LCL={lcl_arr[i]:.4f})' for i in ooc]
        }

    # ---- 规则 2: 中心线同侧连续 N 点 ----
    same_side_runs = _find_runs_same_side(data, center, n_consecutive_same_side)
    if same_side_runs:
        violations[2] = {
            'description': f'连续 {n_consecutive_same_side} 点同侧: {len(same_side_runs)} 段',
            'indices': same_side_runs,
            'detail': [f'点 {s}-{e} (连续{e-s+1}点)' for s, e in same_side_runs]
        }

    # ---- 规则 3: 连续 N 点递增或递减 ----
    trend_runs = _find_trends(data, n_trend)
    if trend_runs:
        violations[3] = {
            'description': f'连续 {n_trend} 点趋势: {len(trend_runs)} 段',
            'indices': trend_runs,
            'detail': [f'点 {s}-{e} ({"递增" if asc else "递减"}趋势)' for s, e, asc in trend_runs]
        }

    # ---- 规则 4: 连续 N 点交替上下 ----
    alt_runs = _find_alternating(data, n_alternating)
    if alt_runs:
        violations[4] = {
            'description': f'连续 {n_alternating} 点交替: {len(alt_runs)} 段',
            'indices': alt_runs,
            'detail': [f'点 {s}-{e} (连续{e-s+1}点交替)' for s, e in alt_runs]
        }

    # ---- 规则 5: 连续3点中2点超出2σ（同侧）- 需要 sigma ----
    if sigma is not None and sigma > 0:
        two_sigma_runs = _find_2_of_3_beyond_ksigma(data, center, sigma, k=2, m=3, count=2)
        if two_sigma_runs:
            violations[5] = {
                'description': f'连续3点中2点超出2σ: {len(two_sigma_runs)} 段',
                'indices': two_sigma_runs,
                'detail': [f'点{i+1}-{i+3}区间' for i in two_sigma_runs]
            }

        # ---- 规则 6: 连续5点中4点超出1σ（同侧）----
        one_sigma_runs = _find_2_of_3_beyond_ksigma(data, center, sigma, k=1, m=5, count=4)
        if one_sigma_runs:
            violations[6] = {
                'description': f'连续5点中4点超出1σ: {len(one_sigma_runs)} 段',
                'indices': one_sigma_runs,
                'detail': [f'点{i+1}-{i+5}区间' for i in one_sigma_runs]
            }

        # ---- 规则 7: 连续15点在1σ内（C区）----
        c_zone_runs = _find_in_k_sigma(data, center, sigma, k=1, min_run=15)
        if c_zone_runs:
            violations[7] = {
                'description': f'连续15点在±1σ内: {len(c_zone_runs)} 段',
                'indices': c_zone_runs,
                'detail': [f'点 {s}-{e} (连续{e-s+1}点)' for s, e in c_zone_runs]
            }

        # ---- 规则 8: 连续8点全部在1σ外（两侧均可）----
        out_one_sigma_runs = _find_out_k_sigma(data, center, sigma, k=1, min_run=8)
        if out_one_sigma_runs:
            violations[8] = {
                'description': f'连续8点在±1σ外: {len(out_one_sigma_runs)} 段',
                'indices': out_one_sigma_runs,
                'detail': [f'点 {s}-{e} (连续{e-s+1}点)' for s, e in out_one_sigma_runs]
            }

    return {
        'violations': violations,
        'total_violations': len(violations),
        'ooc_points': ooc.tolist() if len(ooc) > 0 else []
    }


def _find_runs_same_side(data, center, min_run):
    """找中心线同侧连续 min_run 点的区间 [(start, end), ...]"""
    runs = []
    side = np.sign(data - center)
    start = 0
    for i in range(1, len(side)):
        if side[i] == 0:
            side[i] = side[i - 1] if i > 0 else 1
        if side[i] != side[i - 1]:
            run_len = i - start
            if run_len >= min_run:
                runs.append((start + 1, i))  # 1-based
            start = i
    run_len = len(side) - start
    if run_len >= min_run:
        runs.append((start + 1, len(side)))
    return runs


def _find_trends(data, min_run):
    """找连续 min_run 点递增或递减的区间 [(start, end, ascending), ...]"""
    if len(data) < min_run:
        return []
    runs = []
    diff = np.diff(data)
    start = 0
    for i in range(len(diff)):
        if diff[i] > 0:
            direction = True  # 递增
        elif diff[i] < 0:
            direction = False  # 递减
        else:
            # 相等时延续之前方向
            continue

        if i == start:
            current_dir = direction
            continue

        if direction != current_dir:
            run_len = i - start + 1
            if run_len >= min_run:
                runs.append((start + 1, i + 1, current_dir))
            start = i
            current_dir = direction

    run_len = len(data) - start
    if run_len >= min_run and start < len(data) - 1:
        runs.append((start + 1, len(data), current_dir))
    return runs


def _find_alternating(data, min_run):
    """找连续 min_run 点交替上下波动的区间"""
    if len(data) < min_run:
        return []
    runs = []
    n = len(data)
    signs = np.zeros(n - 1)
    for i in range(n - 1):
        if data[i + 1] > data[i]:
            signs[i] = 1
        elif data[i + 1] < data[i]:
            signs[i] = -1
        else:
            signs[i] = 0

    start = 0
    for i in range(1, len(signs)):
        if signs[i] == 0 or signs[i] == signs[i - 1]:
            run_len = i - start + 1
            if run_len >= min_run:
                runs.append((start + 1, i + 1))
            start = i

    run_len = len(signs) - start + 1
    if run_len >= min_run and start < len(signs):
        runs.append((start + 1, len(data)))
    return runs


def _find_2_of_3_beyond_ksigma(data, center, sigma, k=2, m=3, count=2):
    """滑动窗口检测: 连续m点中count点超出 k*sigma（同侧）"""
    violations = []
    if len(data) < m:
        return violations
    upper = center + k * sigma
    lower = center - k * sigma
    for i in range(len(data) - m + 1):
        window = data[i:i + m]
        above = np.sum(window > upper)
        below = np.sum(window < lower)
        if above >= count or below >= count:
            violations.append(i)
    return violations


def _find_in_k_sigma(data, center, sigma, k=1, min_run=15):
    """找连续 min_run 点都在 ±k*sigma 内的区间"""
    runs = []
    upper = center + k * sigma
    lower = center - k * sigma
    inside = (data >= lower) & (data <= upper)
    start = None
    for i in range(len(inside)):
        if inside[i]:
            if start is None:
                start = i
        else:
            if start is not None:
                run_len = i - start
                if run_len >= min_run:
                    runs.append((start + 1, i))
                start = None
    if start is not None:
        run_len = len(inside) - start
        if run_len >= min_run:
            runs.append((start + 1, len(inside)))
    return runs


def _find_out_k_sigma(data, center, sigma, k=1, min_run=8):
    """找连续 min_run 点都在 ±k*sigma 外的区间"""
    runs = []
    upper = center + k * sigma
    lower = center - k * sigma
    outside = (data > upper) | (data < lower)
    start = None
    for i in range(len(outside)):
        if outside[i]:
            if start is None:
                start = i
        else:
            if start is not None:
                run_len = i - start
                if run_len >= min_run:
                    runs.append((start + 1, i))
                start = None
    if start is not None:
        run_len = len(outside) - start
        if run_len >= min_run:
            runs.append((start + 1, len(outside)))
    return runs


def add_ooc_markers(fig, indices, values, row=None, col=None, name='超限点'):
    """在 Plotly 图表上添加超限点红色 X 标记"""
    if len(indices) == 0:
        return fig
    fig.add_trace(go_scatter_ooc(indices, values, name), row=row, col=col)
    return fig


def go_scatter_ooc(indices, values, name='超限点'):
    """创建超限点 scatter trace"""
    import plotly.graph_objects as go
    return go.Scatter(
        x=indices, y=values,
        mode='markers',
        name=f'{name} ({len(indices)})',
        marker=dict(color='red', size=12, symbol='x-thin', line=dict(width=2))
    )


def compute_target_deviation(center, target):
    """计算中心线与目标值的偏差"""
    if target is None:
        return None
    deviation = center - target
    pct = (deviation / target * 100) if target != 0 else 0
    return {
        'center': center,
        'target': target,
        'deviation': deviation,
        'deviation_pct': pct,
        'abs_deviation': abs(deviation)
    }
