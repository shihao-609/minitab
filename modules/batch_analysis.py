"""
批量导入与自动分析报告模块
============================
支持：
  1. 自动识别 CSV 数据类型（缺陷/GRR/成分/性能/尺寸）
  2. 一键批量上传多个文件
  3. 生成综合质量分析报告
"""

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# 导入现有分析模块
from modules import pareto_histogram, gage_rr, spc_charts, capability
from modules import stats_tools, quality_tools


# ============================================================
# 第一部分：数据类型自动识别
# ============================================================

DATA_TYPES = {
    'pareto':    '帕累托（缺陷分析）',
    'grr':       '测量系统分析 GRR',
    'component': '化学成分',
    'mechanics': '力学性能',
    'dimension': '型材尺寸',
    'unknown':   '通用数据',
}

def detect_data_type(df: pd.DataFrame, filename: str = '') -> Tuple[str, float]:
    """
    根据列名模式和文件名自动识别数据类型。
    返回 (类型代码, 置信度 0-1)
    """
    cols = [str(c).strip().lower() for c in df.columns]
    filename_lower = filename.lower()

    scores = {}

    # === 帕累托/缺陷数据检测 ===
    pareto_keywords = ['不良类型', '缺陷类型', '缺陷', '不良', 'defect', '类型']
    count_keywords = ['数量', '频数', '频次', 'count', '个数', '件数']
    has_pareto_cat = any(kw in ' '.join(cols) for kw in pareto_keywords)
    has_pareto_cnt = any(kw in ' '.join(cols) for kw in count_keywords)
    if has_pareto_cat and has_pareto_cnt and len(cols) == 2:
        scores['pareto'] = 0.95
    elif has_pareto_cat and has_pareto_cnt:
        scores['pareto'] = 0.85
    elif '表面缺陷' in filename_lower or 'defect' in filename_lower:
        scores['pareto'] = 0.80
    elif len(cols) == 2 and df.shape[0] <= 30:
        # 小表两列，可能是缺陷数据
        col1_is_str = df.iloc[:, 0].dtype == object
        col2_is_num = np.issubdtype(df.iloc[:, 1].dtype, np.number)
        if col1_is_str and col2_is_num:
            scores['pareto'] = 0.65

    # === GRR 数据检测 ===
    grr_keywords = ['part', 'operator', 'measurement', '部件', '零件', '操作员', '测量值', '测量']
    grr_match_count = sum(1 for kw in grr_keywords if kw in ' '.join(cols))
    if grr_match_count >= 3:
        scores['grr'] = 0.95
    elif grr_match_count >= 2 and len(cols) >= 3:
        scores['grr'] = 0.85
    elif 'grr' in filename_lower or '测量系统' in filename_lower or 'gage' in filename_lower:
        scores['grr'] = 0.85
    # 检查是否有 Part/Operator/Measurement 模式
    has_part = any('part' in c for c in cols) or any('部件' in c for c in cols) or any('零件' in c for c in cols)
    has_oper = any('oper' in c for c in cols) or any('操作' in c for c in cols) or any('工' in c for c in cols)
    has_meas = any('meas' in c for c in cols) or any('测量' in c for c in cols)
    if has_part and has_oper and has_meas:
        scores['grr'] = 0.95
    elif has_part and has_oper and len(cols) == 3:
        scores['grr'] = 0.80

    # === 化学成分数据检测 ===
    element_keywords = ['si', 'mg', 'fe', 'cu', 'zn', 'mn', 'cr', 'ni', 'ti', 'al',
                        '含量', '成分', '化学', '元素']
    element_count = sum(1 for kw in element_keywords if kw in ' '.join(cols))
    has_batch = any('批次' in c or 'batch' in c for c in cols) or '批' in ' '.join(cols)
    if element_count >= 3 and has_batch:
        scores['component'] = 0.95
    elif element_count >= 4:
        scores['component'] = 0.90
    elif '化学成分' in filename_lower or '成分' in filename_lower:
        scores['component'] = 0.80

    # === 力学性能数据检测 ===
    mechanics_keywords = ['抗拉强度', '屈服强度', '延伸率', '硬度', '拉伸',
                          'tensile', 'yield', 'elongation', 'hardness',
                          '挤压温度', '挤压速度']
    mech_count = sum(1 for kw in mechanics_keywords if kw in ' '.join(cols))
    if mech_count >= 3:
        scores['mechanics'] = 0.95
    elif mech_count >= 2 and has_batch:
        scores['mechanics'] = 0.85
    elif '力学性能' in filename_lower or '力学' in filename_lower:
        scores['mechanics'] = 0.85

    # === 型材尺寸数据检测 ===
    dimension_keywords = ['测量值', '尺寸', '厚度', '宽度', '直径', '长度',
                          'measurement', 'dimension', 'thickness', 'width']
    dim_count = sum(1 for kw in dimension_keywords if kw in ' '.join(cols))
    # 检查是否有多列测量值（测量值1, 测量值2, ... 或 meas1, meas2, ...）
    meas_pattern = sum(1 for c in cols if '测量值' in c or ('meas' in c and any(d in c for d in '123456789')))
    if meas_pattern >= 3:
        scores['dimension'] = 0.95
    elif dim_count >= 2 and has_batch:
        scores['dimension'] = 0.85
    elif '型材尺寸' in filename_lower or '尺寸' in filename_lower:
        scores['dimension'] = 0.80

    # 默认
    if not scores:
        scores['unknown'] = 0.5

    # 返回最高分类型
    best_type = max(scores, key=scores.get)
    return best_type, scores[best_type]


# ============================================================
# 第二部分：各类型数据分析
# ============================================================

def analyze_pareto(df: pd.DataFrame) -> dict:
    """帕累托分析（缺陷数据）"""
    cat_col = df.columns[0]
    cnt_col = df.columns[1]
    categories = df[cat_col].astype(str).tolist()
    counts = pd.to_numeric(df[cnt_col], errors='coerce').fillna(0).values
    result = pareto_histogram.pareto_chart(categories, counts)
    return {
        'type': 'pareto',
        'result': result,
        'summary': {
            '总缺陷数': int(sum(counts)),
            '缺陷类别数': len(categories),
            'TOP1缺陷': f'{categories[0]} ({counts[0]}件, {counts[0]/sum(counts)*100:.1f}%)',
            'TOP3占比': f'{sum(sorted(counts, reverse=True)[:3])/sum(counts)*100:.1f}%',
        }
    }


def analyze_grr(df: pd.DataFrame, tolerance: Optional[float] = None) -> dict:
    """GRR 测量系统分析"""
    part_col, op_col, meas_col = df.columns[0], df.columns[1], df.columns[2]
    parts = df[part_col].values
    ops = df[op_col].values
    meas = pd.to_numeric(df[meas_col], errors='coerce').values

    n_parts = len(np.unique(parts))
    n_ops = len(np.unique(ops))

    result_xbar = gage_rr.gage_rr_crossed(parts, ops, meas, tolerance)
    result_anova = gage_rr.gage_rr_anova(parts, ops, meas, tolerance)

    study_var = result_xbar.get('percent_studyvar', {})
    contrib = result_xbar.get('percent_contribution', {})

    return {
        'type': 'grr',
        'result_xbar': result_xbar,
        'result_anova': result_anova,
        'summary': {
            '部件数': n_parts,
            '操作员数': n_ops,
            '总测量次数': len(meas),
            '%GRR (StudyVar)': study_var.get('%GRR', 'N/A'),
            '%GRR (贡献率)': contrib.get('%GRR', 'N/A'),
            'ndc': result_xbar.get('ndc', 'N/A'),
            '评级': result_xbar.get('evaluation', 'N/A'),
        }
    }


def analyze_process_continuous(df: pd.DataFrame, data_label: str = '') -> dict:
    """
    通用连续型过程数据分析：
    - 所有数值列做 SPC I-MR
    - 过程能力分析 (无规格限时用估计)
    - 相关性矩阵
    """
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    results = {}

    # SPC I-MR 对每个数值列
    spc_results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 3:
            try:
                r = spc_charts.imr_chart(data)
                ooc = sum(r.get('ooc_points', {}).values()) if isinstance(r.get('ooc_points'), dict) else 0
                spc_results.append({
                    '列名': col,
                    '均值': np.mean(data),
                    '标准差': np.std(data, ddof=1),
                    '超限点数': ooc,
                    '受控状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                })
            except Exception:
                pass

    # 过程能力
    cap_results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5:
            try:
                usl = np.mean(data) + 3 * np.std(data, ddof=1)
                lsl = np.mean(data) - 3 * np.std(data, ddof=1)
                r = capability.process_capability(data, usl=usl, lsl=lsl, subgroup_size=1)
                cap_results.append({
                    '列名': col,
                    'Cp': f"{r.get('Cp', 0):.2f}" if r.get('Cp') else 'N/A',
                    'Cpk': f"{r.get('Cpk', 0):.2f}" if r.get('Cpk') else 'N/A',
                    'Pp': f"{r.get('Pp', 0):.2f}" if r.get('Pp') else 'N/A',
                    'Ppk': f"{r.get('Ppk', 0):.2f}" if r.get('Ppk') else 'N/A',
                    'Cpk评级': r.get('cpk_level', 'N/A'),
                })
            except Exception:
                pass

    # 相关性矩阵（2列以上）
    corr_result = None
    if len(numeric_cols) >= 2:
        try:
            corr_result = stats_tools.correlation_matrix(df[numeric_cols])
        except Exception:
            pass

    # 回归分析（2列以上，找最显著组合）
    reg_results = []
    if len(numeric_cols) >= 2:
        for i, xc in enumerate(numeric_cols):
            for yc in numeric_cols[i+1:]:
                x = df[xc].dropna().values
                y = df[yc].dropna().values
                ml = min(len(x), len(y))
                if ml >= 5:
                    try:
                        from scipy import stats
                        r_val, p_val = stats.pearsonr(x[:ml], y[:ml])
                        if p_val < 0.05:
                            reg_results.append({
                                'X变量': xc,
                                'Y变量': yc,
                                'Pearson r': f'{r_val:.4f}',
                                'p值': f'{p_val:.4f}',
                                '显著性': '✓ 显著',
                            })
                    except Exception:
                        pass
        # 只保留TOP10
        reg_results = sorted(reg_results, key=lambda x: abs(float(x['Pearson r'])), reverse=True)[:10]

    results['spc'] = spc_results
    results['capability'] = cap_results
    results['correlation'] = corr_result
    results['regression'] = reg_results

    return {
        'type': 'continuous',
        'label': data_label,
        'numeric_cols': numeric_cols,
        'results': results,
        'summary': {
            '数据行数': len(df),
            '数值列数': len(numeric_cols),
            'SPC受控列数': sum(1 for s in spc_results if '受控' in s.get('受控状态', '')),
            'SPC异常列数': sum(1 for s in spc_results if '超限' in s.get('受控状态', '')),
            '显著相关对数': len(reg_results),
        }
    }


def analyze_dimension(df: pd.DataFrame) -> dict:
    """
    型材尺寸数据分析：
    - 将多测量值列合并，做 SPC I-MR
    - 每个批次计算均值和极差
    """
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    # 将除批次列外的所有数值合并
    non_batch_cols = [c for c in numeric_cols if '批' not in str(c).lower() and 'batch' not in str(c).lower()]
    if not non_batch_cols:
        non_batch_cols = numeric_cols

    all_measures = []
    batch_means = []
    batch_ranges = []

    for _, row in df.iterrows():
        vals = pd.to_numeric(row[non_batch_cols], errors='coerce').dropna().values
        if len(vals) > 0:
            all_measures.extend(vals.tolist())
            batch_means.append(np.mean(vals))
            batch_ranges.append(np.max(vals) - np.min(vals))

    all_measures = np.array(all_measures)
    batch_means = np.array(batch_means)
    batch_ranges = np.array(batch_ranges)

    # 整体 SPC
    spc_overall = spc_charts.imr_chart(all_measures) if len(all_measures) >= 2 else None
    # 批次均值 SPC
    spc_means = spc_charts.imr_chart(batch_means) if len(batch_means) >= 2 else None

    # 过程能力（用整体数据）
    cap_result = None
    if len(all_measures) >= 5:
        try:
            usl = np.mean(all_measures) + 3 * np.std(all_measures, ddof=1)
            lsl = np.mean(all_measures) - 3 * np.std(all_measures, ddof=1)
            cap_result = capability.process_capability(all_measures, usl=usl, lsl=lsl, subgroup_size=1)
        except Exception:
            pass

    return {
        'type': 'dimension',
        'summary': {
            '批次数': len(df),
            '每批测量次数': len(non_batch_cols),
            '总测量点数': len(all_measures),
            '整体均值': f'{np.mean(all_measures):.4f}',
            '整体标准差': f'{np.std(all_measures, ddof=1):.4f}',
            '批次均值范围': f'{np.min(batch_means):.4f} ~ {np.max(batch_means):.4f}',
            '批次极差均值': f'{np.mean(batch_ranges):.4f}',
        },
        'spc_overall': spc_overall,
        'spc_means': spc_means,
        'capability': cap_result,
    }


# ============================================================
# 第三部分：综合报告生成
# ============================================================

def generate_report(all_analyses: List[dict], filenames: List[str]) -> str:
    """
    生成综合质量分析报告（Markdown 格式）。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = []
    lines.append(f'# 📊 质量综合分析报告')
    lines.append(f'')
    lines.append(f'**生成时间**: {now}')
    lines.append(f'**分析文件数**: {len(all_analyses)}')
    lines.append(f'')
    lines.append(f'---')
    lines.append(f'')

    # 总体概览
    lines.append(f'## 📋 总体概览')
    lines.append(f'')
    lines.append(f'| 序号 | 文件名 | 数据类型 | 数据量 | 关键发现 |')
    lines.append(f'|------|--------|----------|--------|----------|')
    total_issues = 0

    for i, (analysis, fname) in enumerate(zip(all_analyses, filenames), 1):
        atype = analysis.get('type', 'unknown')
        summary = analysis.get('summary', {})
        key_findings = []

        if atype == 'pareto':
            data_info = f"{summary.get('总缺陷数', '?')}件缺陷/{summary.get('缺陷类别数', '?')}类"
            top1 = summary.get('TOP1缺陷', '')
            top3 = summary.get('TOP3占比', '')
            key_findings.append(f'TOP1: {top1}')
            key_findings.append(f'TOP3占比: {top3}')

        elif atype == 'grr':
            grr_val = summary.get('%GRR (StudyVar)', 'N/A')
            rating = summary.get('评级', 'N/A')
            ndc = summary.get('ndc', 'N/A')
            data_info = f"{summary.get('部件数', '?')}部件×{summary.get('操作员数', '?')}操作员"
            key_findings.append(f'%GRR={grr_val}')
            key_findings.append(f'评级: {rating}')
            key_findings.append(f'ndc={ndc}')
            if rating and '不可' in str(rating):
                total_issues += 1

        elif atype == 'component':
            data_info = f"{summary.get('数据行数', '?')}行×{summary.get('数值列数', '?')}变量"
            spc_ok = summary.get('SPC受控列数', 0)
            spc_bad = summary.get('SPC异常列数', 0)
            sig_corr = summary.get('显著相关对数', 0)
            key_findings.append(f'受控: {spc_ok}列 / 异常: {spc_bad}列')
            key_findings.append(f'显著相关: {sig_corr}对')
            if spc_bad > 0:
                total_issues += 1

        elif atype == 'mechanics':
            data_info = f"{summary.get('数据行数', '?')}行×{summary.get('数值列数', '?')}变量"
            spc_ok = summary.get('SPC受控列数', 0)
            spc_bad = summary.get('SPC异常列数', 0)
            sig_corr = summary.get('显著相关对数', 0)
            key_findings.append(f'受控: {spc_ok}列 / 异常: {spc_bad}列')
            key_findings.append(f'显著相关: {sig_corr}对')
            if spc_bad > 0:
                total_issues += 1

        elif atype == 'dimension':
            data_info = f"{summary.get('批次数', '?')}批×{summary.get('每批测量次数', '?')}次"
            key_findings.append(f"均值: {summary.get('整体均值', 'N/A')}")
            key_findings.append(f"标准差: {summary.get('整体标准差', 'N/A')}")

        elif atype == 'continuous':
            data_info = f"{summary.get('数据行数', '?')}行×{summary.get('数值列数', '?')}变量"
            spc_ok = summary.get('SPC受控列数', 0)
            spc_bad = summary.get('SPC异常列数', 0)
            key_findings.append(f'受控: {spc_ok}列 / 异常: {spc_bad}列')
            if spc_bad > 0:
                total_issues += 1

        else:
            data_info = f"{summary.get('数据行数', '?')}行"

        lines.append(f'| {i} | {fname} | {DATA_TYPES.get(atype, atype)} | {data_info} | {"; ".join(key_findings)} |')

    lines.append(f'')
    lines.append(f'**⚠️ 需关注问题数**: {total_issues}')
    lines.append(f'')

    # === 逐项详细分析 ===
    for i, (analysis, fname) in enumerate(zip(all_analyses, filenames), 1):
        atype = analysis.get('type', 'unknown')
        lines.append(f'---')
        lines.append(f'')
        lines.append(f'## {i}. {fname} — {DATA_TYPES.get(atype, atype)}分析')
        lines.append(f'')

        if atype == 'pareto':
            # 帕累托详细
            summary = analysis.get('summary', {})
            lines.append(f'### 缺陷概览')
            lines.append(f'- **总缺陷数**: {summary.get("总缺陷数", "N/A")}')
            lines.append(f'- **缺陷类别数**: {summary.get("缺陷类别数", "N/A")}')
            lines.append(f'- **TOP1缺陷**: {summary.get("TOP1缺陷", "N/A")}')
            lines.append(f'- **TOP3累计占比**: {summary.get("TOP3占比", "N/A")}')
            lines.append(f'')

            result = analysis.get('result', {})
            data = result.get('data', pd.DataFrame())
            if not data.empty:
                lines.append(f'### 帕累托数据表')
                lines.append(f'')
                lines.append(f'| 缺陷类型 | 数量 | 累计占比 |')
                lines.append(f'|----------|------|----------|')
                for _, row in data.iterrows():
                    lines.append(f'| {row.iloc[0]} | {row.iloc[1]} | {row.iloc[2] if len(row) > 2 else ""} |')
                lines.append(f'')

            # 建议
            lines.append(f'### 💡 改进建议')
            top_cats = data.iloc[:3, 0].tolist() if not data.empty else []
            lines.append(f'- 重点关注 **{", ".join(top_cats[:2])}**，解决后可消除大部分缺陷')
            lines.append(f'- 建议对TOP1缺陷成立专项改善小组')
            lines.append(f'')

        elif atype == 'grr':
            summary = analysis.get('summary', {})
            lines.append(f'### 测量系统评估')
            lines.append(f'- **部件数**: {summary.get("部件数", "N/A")}')
            lines.append(f'- **操作员数**: {summary.get("操作员数", "N/A")}')
            lines.append(f'- **%GRR (StudyVar)**: {summary.get("%GRR (StudyVar)", "N/A")}')
            lines.append(f'- **%GRR (贡献率)**: {summary.get("%GRR (贡献率)", "N/A")}')
            lines.append(f'- **ndc (可区分类别数)**: {summary.get("ndc", "N/A")}')
            lines.append(f'- **综合评级**: {summary.get("评级", "N/A")}')
            lines.append(f'')

            # 方差分量
            result = analysis.get('result_xbar', {})
            std_contrib = result.get('stddev_contributions', {})
            if std_contrib:
                lines.append(f'### 方差分量')
                lines.append(f'')
                lines.append(f'| 分量 | 标准差 σ |')
                lines.append(f'|------|----------|')
                for k, v in std_contrib.items():
                    lines.append(f'| {k} | {v} |')
                lines.append(f'')

            # 建议
            lines.append(f'### 💡 改进建议')
            grr_pct_str = summary.get('%GRR (StudyVar)', '')
            try:
                grr_pct = float(grr_pct_str.replace('%', ''))
            except (ValueError, AttributeError):
                grr_pct = 0
            if grr_pct < 10:
                lines.append(f'- ✅ 测量系统能力优秀，GRR < 10%，可正常使用')
            elif grr_pct < 30:
                lines.append(f'- ⚠️ 测量系统处于临界状态，建议关注操作员培训和量具维护')
            else:
                lines.append(f'- 🔴 测量系统不合格，需立即改进！建议：')
                lines.append(f'  1. 检查量具精度和校准状态')
                lines.append(f'  2. 统一操作员测量方法')
                lines.append(f'  3. 增加重复测量次数')
            lines.append(f'')

        elif atype in ('component', 'mechanics', 'continuous'):
            summary = analysis.get('summary', {})
            results = analysis.get('results', {})

            # SPC 汇总
            spc_list = results.get('spc', [])
            if spc_list:
                lines.append(f'### SPC 控制图分析')
                lines.append(f'')
                lines.append(f'| 变量 | 均值 | 标准差 | 超限点 | 状态 |')
                lines.append(f'|------|------|--------|--------|------|')
                for s in spc_list:
                    lines.append(f'| {s["列名"]} | {s["均值"]:.3f} | {s["标准差"]:.4f} | {s["超限点数"]} | {s["受控状态"]} |')
                lines.append(f'')

            # 过程能力
            cap_list = results.get('capability', [])
            if cap_list:
                lines.append(f'### 过程能力分析（3σ估计规格限）')
                lines.append(f'')
                lines.append(f'| 变量 | Cp | Cpk | Pp | Ppk | 评级 |')
                lines.append(f'|------|-----|-----|-----|-----|------|')
                for c in cap_list:
                    lines.append(f'| {c["列名"]} | {c["Cp"]} | {c["Cpk"]} | {c["Pp"]} | {c["Ppk"]} | {c["Cpk评级"]} |')
                lines.append(f'')

            # 相关性
            corr = results.get('correlation')
            if corr and 'corr_df' in corr:
                corr_df = corr['corr_df']
                lines.append(f'### 相关性矩阵')
                lines.append(f'')
                lines.append(f'| 变量 | ' + ' | '.join(corr_df.columns) + ' |')
                lines.append(f'|------|' + '|'.join(['------'] * len(corr_df.columns)) + '|')
                for _, row in corr_df.iterrows():
                    vals = ' | '.join([f'{v:.2f}' if isinstance(v, float) else str(v) for v in row])
                    lines.append(f'| {row.name} | {vals} |')
                lines.append(f'')

            # 显著回归关系
            reg_list = results.get('regression', [])
            if reg_list:
                lines.append(f'### 显著回归关系 (p < 0.05)')
                lines.append(f'')
                lines.append(f'| X变量 | Y变量 | Pearson r | p值 | 显著性 |')
                lines.append(f'|-------|-------|-----------|------|--------|')
                for r in reg_list:
                    lines.append(f'| {r["X变量"]} | {r["Y变量"]} | {r["Pearson r"]} | {r["p值"]} | {r["显著性"]} |')
                lines.append(f'')

            # 建议
            lines.append(f'### 💡 改进建议')
            bad_spc = [s for s in spc_list if '超限' in s.get('受控状态', '')]
            bad_cpk = [c for c in cap_list if c.get('Cpk评级', '') in ('不足', '差')]
            if bad_spc:
                lines.append(f'- ⚠️ SPC异常变量: {", ".join([s["列名"] for s in bad_spc])}，建议排查过程特殊原因')
            if bad_cpk:
                lines.append(f'- ⚠️ 能力不足变量: {", ".join([c["列名"] for c in bad_cpk])}，Cpk需提升')
            if not bad_spc and not bad_cpk:
                lines.append(f'- ✅ 所有数值变量SPC受控，过程能力满足要求')
            if reg_list:
                lines.append(f'- 📈 发现 {len(reg_list)} 对显著相关关系，可用于过程优化')
            lines.append(f'')

        elif atype == 'dimension':
            summary = analysis.get('summary', {})
            lines.append(f'### 尺寸数据概览')
            lines.append(f'- **批次数**: {summary.get("批次数", "N/A")}')
            lines.append(f'- **每批测量次数**: {summary.get("每批测量次数", "N/A")}')
            lines.append(f'- **总测量点数**: {summary.get("总测量点数", "N/A")}')
            lines.append(f'- **整体均值**: {summary.get("整体均值", "N/A")}')
            lines.append(f'- **整体标准差**: {summary.get("整体标准差", "N/A")}')
            lines.append(f'- **批次均值范围**: {summary.get("批次均值范围", "N/A")}')
            lines.append(f'- **批次极差均值**: {summary.get("批次极差均值", "N/A")}')
            lines.append(f'')

            cap = analysis.get('capability')
            if cap:
                lines.append(f'### 过程能力')
                lines.append(f'- **Cpk**: {cap.get("Cpk", "N/A")}')
                lines.append(f'- **Ppk**: {cap.get("Ppk", "N/A")}')
                lines.append(f'- **评级**: {cap.get("cpk_level", "N/A")}')
                lines.append(f'')

            lines.append(f'### 💡 改进建议')
            try:
                mean_val = float(str(summary.get('整体均值', '0')).split('~')[0])
                std_val = float(str(summary.get('整体标准差', '0')))
                if std_val > 0:
                    cv = std_val / mean_val * 100
                    if cv < 1:
                        lines.append(f'- ✅ 变异系数 CV={cv:.2f}%，尺寸一致性良好')
                    elif cv < 3:
                        lines.append(f'- ℹ️ 变异系数 CV={cv:.2f}%，尺寸波动可接受')
                    else:
                        lines.append(f'- ⚠️ 变异系数 CV={cv:.2f}%，尺寸波动较大，建议排查')
            except Exception:
                pass
            lines.append(f'')

    # === 总结 ===
    lines.append(f'---')
    lines.append(f'')
    lines.append(f'## 📝 综合结论与建议')
    lines.append(f'')
    lines.append(f'基于以上 {len(all_analyses)} 份数据的综合分析：')
    lines.append(f'')

    # 汇总关键指标
    grr_issues = []
    spc_issues = []
    cpk_issues = []
    pareto_insights = []

    for analysis in all_analyses:
        atype = analysis.get('type', '')
        if atype == 'grr':
            s = analysis.get('summary', {})
            try:
                grr_v = float(str(s.get('%GRR (StudyVar)', '0')).replace('%', ''))
            except Exception:
                grr_v = 0
            if grr_v >= 30:
                grr_issues.append(f'🔴 测量系统不合格 (%GRR={grr_v}%)')
            elif grr_v >= 10:
                grr_issues.append(f'⚠️ 测量系统临界 (%GRR={grr_v}%)')
            else:
                grr_issues.append(f'✅ 测量系统优秀 (%GRR={grr_v}%)')

        elif atype in ('component', 'mechanics', 'continuous'):
            results = analysis.get('results', {})
            spc_list = results.get('spc', [])
            bad = [s for s in spc_list if '超限' in s.get('受控状态', '')]
            if bad:
                spc_issues.append(f'⚠️ {len(bad)}个变量SPC异常')
            cap_list = results.get('capability', [])
            bad_cpk = [c for c in cap_list if c.get('Cpk评级', '') in ('不足', '差')]
            if bad_cpk:
                cpk_issues.append(f'⚠️ {len(bad_cpk)}个变量Cpk不足')

        elif atype == 'pareto':
            s = analysis.get('summary', {})
            pareto_insights.append(s.get('TOP1缺陷', ''))

    lines.append(f'### ✅ 合格项')
    if grr_issues:
        good_grr = [g for g in grr_issues if '✅' in g]
        for g in good_grr:
            lines.append(f'- {g}')
    if not spc_issues:
        lines.append(f'- 所有过程参数 SPC 受控')
    if not cpk_issues:
        lines.append(f'- 所有关键指标过程能力满足要求')
    if not any([good_grr for good_grr in grr_issues if '✅' in good_grr]) and spc_issues:
        lines.append(f'- 基础数据完整可用')
    lines.append(f'')

    lines.append(f'### ⚠️ 需关注项')
    bad_grr = [g for g in grr_issues if ('⚠️' in g or '🔴' in g)]
    has_issues = bool(bad_grr or spc_issues or cpk_issues)
    if has_issues:
        for g in bad_grr:
            lines.append(f'- {g}')
        for s in spc_issues:
            lines.append(f'- {s}')
        for c in cpk_issues:
            lines.append(f'- {c}')
    else:
        lines.append(f'- 本次分析未发现需立即处理的异常项')
    lines.append(f'')

    lines.append(f'### 📌 行动建议')
    lines.append(f'')
    if pareto_insights:
        lines.append(f'1. **缺陷改进优先级**: 重点关注 {"; ".join(pareto_insights[:2])}')
    if grr_issues and any('🔴' in g for g in grr_issues):
        lines.append(f'2. **测量系统改进**: GRR超标，需立即开展MSA改进活动')
    if spc_issues:
        lines.append(f'3. **过程控制**: 对SPC异常变量进行根因分析，消除特殊原因')
    if cpk_issues:
        lines.append(f'4. **能力提升**: 针对Cpk不足变量制定改进计划')
    if not has_issues:
        lines.append(f'1. 当前质量状态良好，建议保持现有控制水平')
        lines.append(f'2. 可适当延长抽检周期，降低检验成本')

    lines.append(f'')
    lines.append(f'---')
    lines.append(f'')
    lines.append(f'*本报告由质量管理系统 QMS v2.0 自动生成 · {now}*')

    return '\n'.join(lines)


# ============================================================
# 第四部分：便捷批量导入接口
# ============================================================

def run_single_analysis(df: pd.DataFrame, data_type: str,
                        grr_tolerance: Optional[float] = None) -> dict:
    """
    对单个 DataFrame 执行指定类型的分析。
    可用于手动指定类型（覆盖自动识别）。

    参数:
        df: 数据 DataFrame
        data_type: 类型代码 ('pareto'/'grr'/'component'/'mechanics'/'dimension'/'continuous')
        grr_tolerance: GRR公差 (可选)

    返回:
        分析结果 dict
    """
    if data_type == 'pareto':
        return analyze_pareto(df)
    elif data_type == 'grr':
        return analyze_grr(df, grr_tolerance)
    elif data_type == 'component':
        r = analyze_process_continuous(df, '化学成分')
        r['type'] = 'component'
        return r
    elif data_type == 'mechanics':
        r = analyze_process_continuous(df, '力学性能')
        r['type'] = 'mechanics'
        return r
    elif data_type == 'dimension':
        return analyze_dimension(df)
    else:
        r = analyze_process_continuous(df, '通用')
        r['type'] = 'continuous'
        return r


def batch_import_and_analyze(
    uploaded_files: list,
    grr_tolerance: Optional[float] = None,
    type_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, pd.DataFrame], List[dict], str]:
    """
    批量导入多个CSV文件并自动分析。

    参数:
        uploaded_files: Streamlit UploadedFile 对象列表
        grr_tolerance: GRR分析的公差值 (可选)
        type_overrides: 手动类型覆盖 {文件名: 类型代码} (可选)
                        用于覆盖自动识别结果

    返回:
        (数据字典 {文件名: DataFrame}, 分析结果列表, 报告字符串)
    """
    if type_overrides is None:
        type_overrides = {}

    data_dict = {}
    analyses = []
    filenames = []

    for uploaded_file in uploaded_files:
        fname = uploaded_file.name
        filenames.append(fname)

        # 解析 CSV
        raw_bytes = uploaded_file.getvalue()
        df = None
        for enc in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
            try:
                df = pd.read_csv(BytesIO(raw_bytes), encoding=enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        if df is None:
            analyses.append({'type': 'error', 'error': f'无法解析文件: {fname}', 'filename': fname})
            continue

        data_dict[fname] = df

        # 确定最终使用的数据类型：手动覆盖 > 自动识别
        if fname in type_overrides:
            dtype = type_overrides[fname]
            confidence = 1.0  # 手动指定 = 100% 置信
            auto_detected = detect_data_type(df, fname)[0]
        else:
            dtype, confidence = detect_data_type(df, fname)
            auto_detected = dtype

        # 执行对应分析
        try:
            analysis = run_single_analysis(df, dtype, grr_tolerance)
            analysis['filename'] = fname
            analysis['detection_confidence'] = confidence
            analysis['detected_type'] = auto_detected
            analysis['manual_override'] = fname in type_overrides
            analyses.append(analysis)

        except Exception as e:
            analyses.append({
                'type': 'error',
                'filename': fname,
                'error': f'分析失败: {str(e)}',
                'detected_type': auto_detected if 'auto_detected' in dir() else dtype,
                'detection_confidence': confidence if 'confidence' in dir() else 0,
            })

    # 生成报告
    valid_analyses = [a for a in analyses if a.get('type') != 'error']
    valid_filenames = [a.get('filename', '') for a in valid_analyses]
    report = generate_report(valid_analyses, valid_filenames) if valid_analyses else '无有效数据可分析'

    return data_dict, analyses, report


def build_files_data(uploaded_files: list, type_mapping: Dict[str, str]) -> list:
    """
    将上传的 CSV 原始数据打包为可存储格式。
    用于保存到数据库以便后续重新加载分析。

    参数:
        uploaded_files: Streamlit UploadedFile 对象列表
        type_mapping: {文件名: 数据类型代码}

    返回:
        [{filename, csv_data, data_type}, ...]
    """
    files_data = []
    for uf in uploaded_files:
        raw_bytes = uf.getvalue()
        csv_str = raw_bytes.decode('utf-8-sig') or raw_bytes.decode('utf-8') or raw_bytes.decode('gbk', errors='replace')
        files_data.append({
            'filename': uf.name,
            'csv_data': csv_str,
            'data_type': type_mapping.get(uf.name, 'unknown'),
        })
    return files_data


def build_analyses_summary(analyses: List[dict]) -> list:
    """
    从分析结果中提取摘要（排除不可序列化的图表对象），用于存储。

    返回:
        [{filename, type, summary, detected_type, manual_override}, ...]
    """
    summary_list = []
    for a in analyses:
        summary_list.append({
            'filename': a.get('filename', ''),
            'type': a.get('type', 'unknown'),
            'summary': a.get('summary', {}),
            'detected_type': a.get('detected_type', ''),
            'manual_override': a.get('manual_override', False),
        })
    return summary_list


def restore_analyses_from_files(files_data: list,
                                grr_tolerance: Optional[float] = None) -> Tuple[Dict[str, pd.DataFrame], List[dict]]:
    """
    从数据库加载的文件数据重新执行分析（恢复完整分析结果含图表）。

    参数:
        files_data: [{filename, csv_data, data_type}, ...]
        grr_tolerance: GRR公差

    返回:
        (数据字典, 分析结果列表)
    """
    data_dict = {}
    analyses = []

    for fd in files_data:
        fname = fd.get('filename', 'unknown.csv')
        csv_str = fd.get('csv_data', '')
        data_type = fd.get('data_type', 'continuous')

        if not csv_str:
            continue

        try:
            from io import StringIO
            df = pd.read_csv(StringIO(csv_str))
        except Exception:
            continue

        data_dict[fname] = df

        try:
            analysis = run_single_analysis(df, data_type, grr_tolerance)
            analysis['filename'] = fname
            analysis['detected_type'] = data_type
            analysis['manual_override'] = True  # 从数据库恢复的，类型来自存储
            analysis['detection_confidence'] = 1.0
            analyses.append(analysis)
        except Exception as e:
            analyses.append({
                'type': 'error',
                'filename': fname,
                'error': f'重新分析失败: {str(e)}',
            })

    return data_dict, analyses
