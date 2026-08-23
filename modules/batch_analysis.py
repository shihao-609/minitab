"""
批量导入与自动分析报告模块
============================
支持：
  1. 自动识别 CSV 数据类型（缺陷/GRR/成分/性能/尺寸）
  2. 一键批量上传多个文件
  3. 生成综合质量分析报告
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional

# 导入现有分析模块
from modules import pareto_histogram, gage_rr, spc_charts, capability
from modules import stats_tools, quality_tools, spc_advanced, msa_advanced, advanced_analysis


# ============================================================
# 第一部分：数据类型自动识别
# ============================================================

def _get_type_label(analysis: dict) -> str:
    """从分析结果中提取可读的类型标签 — 优先使用已选模块名称"""
    # 连续数据批量选择了多个模块 → 列出模块名称
    modules = analysis.get('modules_selected', [])
    if modules:
        labels = [ALL_MODULES[m]['label'] for m in modules if m in ALL_MODULES]
        if labels:
            return ', '.join(labels)
    # 独立模块（pareto / grr / dimension / grr_attribute）
    mod = analysis.get('module', '')
    if mod and mod in ALL_MODULES:
        return ALL_MODULES[mod]['label']
    # 兜底
    dtype = analysis.get('data_type', analysis.get('type', ''))
    if dtype in ALL_MODULES:
        return ALL_MODULES[dtype]['label']
    return dtype

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

def analyze_pareto(df: pd.DataFrame, cat_col: str = None, cnt_col: str = None) -> dict:
    """帕累托分析（缺陷数据）"""
    cat_col = cat_col if cat_col else df.columns[0]
    cnt_col = cnt_col if cnt_col else df.columns[1]
    categories = df[cat_col].astype(str).tolist()
    counts = pd.to_numeric(df[cnt_col], errors='coerce').fillna(0).values
    total = int(sum(counts))
    result = pareto_histogram.pareto_chart(categories, counts)
    if total > 0:
        top1_pct = counts[0] / total * 100 if len(counts) > 0 else 0.0
        top3_sum = sum(sorted(counts, reverse=True)[:3])
        top3_pct = top3_sum / total * 100
    else:
        top1_pct = top3_pct = 0.0
    return {
        'type': 'pareto',
        'result': result,
        'summary': {
            '总缺陷数': total,
            '缺陷类别数': len(categories),
            'TOP1缺陷': f'{categories[0] if len(categories) > 0 else "无"} ({counts[0] if len(counts) > 0 else 0}件, {top1_pct:.1f}%)',
            'TOP3占比': f'{top3_pct:.1f}%',
        }
    }


def analyze_grr(df: pd.DataFrame, tolerance: Optional[float] = None,
                part_col: str = None, op_col: str = None, meas_col: str = None) -> dict:
    """GRR 测量系统分析"""
    part_col = part_col if part_col else df.columns[0]
    op_col = op_col if op_col else df.columns[1]
    meas_col = meas_col if meas_col else df.columns[2]
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


def analyze_dimension(df: pd.DataFrame, batch_col: str = None, meas_cols: list = None) -> dict:
    """
    型材尺寸数据分析：
    - 将多测量值列合并，做 SPC I-MR
    - 每个批次计算均值和极差
    """
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    # 测量列：优先使用用户指定，否则自动排除批次列
    if meas_cols:
        non_batch_cols = [c for c in meas_cols if c in numeric_cols]
    else:
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
    now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
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

        elif atype in ('component', 'mechanics', 'continuous'):
            data_info = f"{summary.get('数据行数', '?')}行×{summary.get('数值列数', '?')}变量"
            spc_ok = summary.get('SPC受控列数', 0)
            spc_bad = summary.get('SPC异常列数', 0)
            sig_corr = summary.get('显著相关对数', 0)
            key_findings.append(f'受控: {spc_ok}列 / 异常: {spc_bad}列')
            if sig_corr:
                key_findings.append(f'显著相关: {sig_corr}对')
            if spc_bad > 0:
                total_issues += 1

        elif atype == 'dimension':
            data_info = f"{summary.get('批次数', '?')}批×{summary.get('每批测量次数', '?')}次"
            key_findings.append(f"均值: {summary.get('整体均值', 'N/A')}")
            key_findings.append(f"标准差: {summary.get('整体标准差', 'N/A')}")

        elif atype == 'grr_attribute':
            data_info = f"{summary.get('操作员数', '?')}操作员×{summary.get('样本数', '?')}样本"
            key_findings.append(f"两两一致性: {summary.get('两两一致性', 'N/A')}")

        else:
            data_info = f"{summary.get('数据行数', '?')}行"

        lines.append(f'| {i} | {fname} | {_get_type_label(analysis)} | {data_info} | {"; ".join(key_findings)} |')

    lines.append(f'')
    lines.append(f'**⚠️ 需关注问题数**: {total_issues}')
    lines.append(f'')

    # === 逐项详细分析 ===
    for i, (analysis, fname) in enumerate(zip(all_analyses, filenames), 1):
        atype = analysis.get('type', 'unknown')
        lines.append(f'---')
        lines.append(f'')
        lines.append(f'## {i}. {fname} — {_get_type_label(analysis)}分析')
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
                lines.append(f'| 缺陷类型 | 数量 | 占比 (%) | 累积 (%) |')
                lines.append(f'|----------|------|----------|----------|')
                for _, row in data.iterrows():
                    vals = [row.iloc[i] for i in range(min(len(row), 4))]
                    lines.append(f'| {" | ".join(str(v) for v in vals)} |')
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
            spc_result = results.get('spc', {})
            spc_list = spc_result.get('summary', []) if isinstance(spc_result, dict) else spc_result
            if spc_list:
                lines.append(f'### SPC 控制图分析')
                lines.append(f'')
                lines.append(f'| 变量 | 图表类型 | 均值 | 标准差 | 超限点 | 状态 |')
                lines.append(f'|------|---------|------|--------|--------|------|')
                for s in spc_list:
                    chart_type = s.get('图表类型', 'I-MR')
                    mean_val = s.get('均值', 0)
                    std_val = s.get('标准差', 0)
                    # 兼容旧格式（超限点/状态）和新格式（超限点数/受控状态）
                    ooc_val = s.get('超限点数', s.get('超限点', 0))
                    status_val = s.get('受控状态', s.get('状态', 'N/A'))
                    lines.append(f'| {s["列名"]} | {chart_type} | {mean_val:.3f} | {std_val:.4f} | {ooc_val} | {status_val} |')
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

            # 正态性检验
            normality_list = results.get('normality', [])
            if normality_list:
                lines.append(f'### 正态性检验 (Shapiro-Wilk)')
                lines.append(f'')
                lines.append(f'| 变量 | 样本量 | p值 | 正态性 |')
                lines.append(f'|------|--------|-----|--------|')
                for n in normality_list:
                    lines.append(f'| {n["列名"]} | {n["样本量"]} | {n["Shapiro-Wilk p值"]} | {n["正态性"]} |')
                non_normal = [n for n in normality_list if '❌' in n.get('正态性', '')]
                if non_normal:
                    lines.append(f'')
                    lines.append(f'⚠️ {len(non_normal)} 个变量不服从正态分布: {", ".join([n["列名"] for n in non_normal])}')
                lines.append(f'')

            # 运行图
            runchart_list = results.get('run_chart', [])
            if runchart_list:
                lines.append(f'### 运行图分析')
                lines.append(f'')
                lines.append(f'| 变量 | 均值 | 中位数 | 游程检验 | 趋势检验 |')
                lines.append(f'|------|------|--------|----------|----------|')
                for r in runchart_list:
                    lines.append(f'| {r["列名"]} | {r["均值"]} | {r["中位数"]} | {r["游程检验"]} | {r["趋势检验"]} |')
                lines.append(f'')

            # 描述性统计
            stats_list = results.get('stats_summary', [])
            if stats_list:
                lines.append(f'### 描述性统计')
                lines.append(f'')
                lines.append(f'| 变量 | 样本量 | 均值 | 标准差 | 最小值 | 中位数 | 最大值 | 偏度 | 峰度 |')
                lines.append(f'|------|--------|------|--------|--------|--------|--------|------|------|')
                for s in stats_list:
                    lines.append(f'| {s["列名"]} | {s["样本量"]} | {s["均值"]} | {s["标准差"]} | {s["最小值"]} | {s["中位数"]} | {s["最大值"]} | {s["偏度"]} | {s["峰度"]} |')
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

        elif atype == 'grr_attribute':
            summary = analysis.get('summary', {})
            lines.append(f'### 计数型 GRR 评估')
            lines.append(f'- **参考列**: {summary.get("参考列", "N/A")}')
            lines.append(f'- **操作员数**: {summary.get("操作员数", "N/A")}')
            lines.append(f'- **样本数**: {summary.get("样本数", "N/A")}')
            lines.append(f'- **两两一致性**: {summary.get("两两一致性", "N/A")}')
            lines.append(f'')
            kappa = analysis.get('kappa_summary', [])
            if kappa:
                lines.append(f'### Kappa 一致性')
                lines.append(f'')
                lines.append(f'| 操作员 | Kappa | 评级 |')
                lines.append(f'|--------|-------|------|')
                for k in kappa:
                    lines.append(f'| {k.get("操作员", "N/A")} | {k.get("Kappa", "N/A")} | {k.get("评级", "N/A")} |')
                lines.append(f'')
            lines.append(f'### 💡 改进建议')
            agree_str = summary.get('两两一致性', '0%')
            try:
                agree_val = float(agree_str.replace('%', '')) / 100 if '%' in agree_str else float(agree_str)
            except (ValueError, AttributeError):
                agree_val = 0
            if agree_val >= 0.9:
                lines.append(f'- ✅ 操作员一致性好，测量系统可靠')
            elif agree_val >= 0.7:
                lines.append(f'- ⚠️ 操作员一致性可接受，建议加强培训和标准')
            else:
                lines.append(f'- 🔴 操作员一致性差，急需统一判定标准和培训')
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

        elif atype in ('component', 'mechanics', 'continuous', 'dimension'):
            results = analysis.get('results', {})
            spc_result = results.get('spc', {})
            spc_list = spc_result.get('summary', []) if isinstance(spc_result, dict) else spc_result
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
# 第四部分：便捷批量导入接口（纯手动版）
# ============================================================

# 连续数据类型可选择的分析模块
CONTINUOUS_MODULES = {
    'spc':        {'label': 'SPC 控制图 (I-MR)', 'default': True},
    'capability': {'label': '过程能力分析 (Cp/Cpk)', 'default': True},
    'correlation': {'label': '相关性矩阵', 'default': True},
    'regression': {'label': '回归分析', 'default': True},
}

# 全部可用的分析模块（用户直接选择）
ALL_MODULES = {
    # ---- 基础图形 ----
    'pareto':        {'label': '帕累托图',          'group': 'quality_graph', 'desc': '缺陷类别 + 数量'},
    'histogram':     {'label': '直方图 (含统计)',   'group': 'quality_graph', 'desc': '分布形态 + 正态拟合'},
    'boxplot':       {'label': '箱线图',             'group': 'quality_graph', 'desc': '分布特征 + 异常值'},
    'run_chart':     {'label': '运行图',             'group': 'quality_graph', 'desc': '时序趋势 + 游程检验'},
    # ---- SPC 控制 ----
    'spc':           {'label': 'SPC 控制图',         'group': 'spc_control',  'desc': '7种休哈特控制图 (多选子类型)'},
    'ewma':          {'label': 'EWMA 控制图',        'group': 'spc_control',  'desc': '指数加权移动平均'},
    'cusum':         {'label': 'CUSUM 控制图',       'group': 'spc_control',  'desc': '累积和 (灵敏检测小偏移)'},
    # ---- 能力分析 ----
    'capability':    {'label': 'Cp/Cpk 过程能力',    'group': 'capability',   'desc': '短期+长期能力指数'},
    'box_cox':       {'label': 'Box-Cox 变换能力',   'group': 'capability',   'desc': '非正态数据能力分析'},
    'cg_cgk':        {'label': 'Cg/Cgk 检具能力',    'group': 'capability',   'desc': 'MSA Type 1 检具评估'},
    # ---- 统计推断 ----
    'normality':     {'label': '正态性检验',         'group': 'statistics',   'desc': 'Shapiro-Wilk / AD / K²'},
    'correlation':   {'label': '相关性矩阵',         'group': 'statistics',   'desc': 'Pearson 相关系数热力图'},
    'regression':    {'label': '回归分析',           'group': 'statistics',   'desc': '一元/多元线性回归'},
    'stats_summary': {'label': '描述性统计',         'group': 'statistics',   'desc': '均值/标准差/偏度/峰度'},
    # ---- 测量系统 MSA ----
    'grr':           {'label': '计量型 Gage R&R',    'group': 'msa',          'desc': 'X-bar R + ANOVA 法'},
    'grr_attribute': {'label': '计数型 Gage R&R',    'group': 'msa',          'desc': '属性一致性 Kappa 法'},
    'uncertainty':   {'label': '测量不确定度',       'group': 'msa',          'desc': 'GUM 法评定'},
    # ---- 特殊分析 ----
    'dimension':     {'label': '型材尺寸分析',       'group': 'special',      'desc': '批次多测量值 SPC'},
    'weibull':       {'label': 'Weibull 可靠性',     'group': 'special',      'desc': '失效时间/寿命分析'},
}

# 休哈特控制图子类型（在勾选"SPC 控制图"后可多选）
SPC_SUB_MODES = {
    'imr':    {'label': 'I-MR (单值-移动极差)',   'func': 'imr',        'cat': 'continuous', 'desc': '单值+移动极差，n≥2'},
    'xbar_r': {'label': 'X-bar R (均值-极差)',    'func': 'xbar_r',     'cat': 'continuous', 'desc': '需子组≥2，n≥10'},
    'xbar_s': {'label': 'X-bar S (均值-标准差)',  'func': 'xbar_s',     'cat': 'continuous', 'desc': '需子组≥6，n≥10'},
    'p':      {'label': 'P 图 (不合格品率)',      'func': 'p',          'cat': 'attribute',  'desc': '需不良品数+样本量两列'},
    'np':     {'label': 'NP 图 (不合格品数)',     'func': 'np',         'cat': 'attribute',  'desc': '需不良品数+固定样本量'},
    'c':      {'label': 'C 图 (缺陷数)',          'func': 'c',          'cat': 'attribute',  'desc': '需缺陷数单列，n≥5'},
    'u':      {'label': 'U 图 (单位缺陷数)',      'func': 'u',          'cat': 'attribute',  'desc': '需缺陷数+样本量两列'},
}

# 模块分组（用于设置界面按组展示）
MODULE_GROUPS = [
    ('📊 基础图形',    'quality_graph'),
    ('📈 SPC 控制',    'spc_control'),
    ('🎯 能力分析',    'capability'),
    ('🔢 统计推断',    'statistics'),
    ('🔬 测量系统 MSA', 'msa'),
    ('📏 特殊分析',    'special'),
]

# 连续型模块的子分析函数映射
_CONTINUOUS_ANALYZERS = {
    'normality':     '_analyze_normality',
    'boxplot':       '_analyze_boxplot',
    'run_chart':     '_analyze_runchart',
    'stats_summary': '_analyze_stats_summary',
    'histogram':     '_analyze_histogram',
    'ewma':          '_analyze_ewma',
    'cusum':         '_analyze_cusum',
    'box_cox':       '_analyze_boxcox',
    'cg_cgk':        '_analyze_cgcgk',
    'uncertainty':   '_analyze_uncertainty',
    'weibull':       '_analyze_weibull',
}


def _analyze_spc_shewhart(df: pd.DataFrame, numeric_cols: list = None,
                           spc_sub_modes: list = None,
                           extra_params: dict = None) -> dict:
    """执行所有选中的休哈特控制图分析。

    参数:
        df: 数据 DataFrame
        numeric_cols: 要分析的数值列，None=自动检测
        spc_sub_modes: 子类型列表，如 ['imr','xbar_r','xbar_s','p','np','c','u']
        extra_params: 额外参数，如 subgroup_size, spc_target, spc_attr_cols

    返回:
        dict: {
            'summary': [{列名, 均值, 标准差, 超限点数, 受控状态, 图表类型}, ...],
            'imr': {'charts': {col: fig}, 'summary': [...]},  # 各子类型结果
            'xbar_r': ...,
        }
    """
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if spc_sub_modes is None:
        spc_sub_modes = ['imr']
    ep = extra_params or {}
    subgroup_size = ep.get('spc_subgroup_size', 5)
    spc_target = ep.get('spc_target')
    if spc_target is not None:
        try:
            spc_target = float(spc_target)
        except (ValueError, TypeError):
            spc_target = None

    # ---- 分离连续型子类型和计数型子类型 ----
    cont_modes = [m for m in spc_sub_modes if SPC_SUB_MODES.get(m, {}).get('cat') == 'continuous']
    attr_modes = [m for m in spc_sub_modes if SPC_SUB_MODES.get(m, {}).get('cat') == 'attribute']

    results = {}
    overall_summary = []  # 统一的汇总表

    # ======== 连续型 SPC (I-MR / X-bar R / X-bar S) ========
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) < 2:
            continue
        n = len(data)

        # --- I-MR ---
        if 'imr' in cont_modes:
            if 'imr' not in results:
                results['imr'] = {'charts': {}, 'summary': []}
            try:
                r = spc_charts.imr_chart(data, target=spc_target)
                ooc = sum(r.get('ooc_points', {}).values()) if isinstance(r.get('ooc_points'), dict) else 0
                results['imr']['charts'][col] = r.get('chart')
                results['imr']['summary'].append({
                    '列名': col, '均值': np.mean(data), '标准差': np.std(data, ddof=1),
                    '超限点': ooc, '状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                })
                overall_summary.append({
                    '列名': col, '图表类型': 'I-MR',
                    '均值': np.mean(data), '标准差': np.std(data, ddof=1),
                    '超限点数': ooc, '受控状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                })
            except Exception:
                pass

        # --- X-bar R ---
        if 'xbar_r' in cont_modes:
            if 'xbar_r' not in results:
                results['xbar_r'] = {'charts': {}, 'summary': []}
            if n >= subgroup_size * 2:  # 至少需要2个子组
                try:
                    r = spc_charts.xbar_r_chart(data, subgroup_size=subgroup_size, target=spc_target)
                    ooc = sum(r.get('ooc_points', {}).values()) if isinstance(r.get('ooc_points'), dict) else 0
                    stats = r.get('stats', {})
                    results['xbar_r']['charts'][col] = r.get('chart')
                    results['xbar_r']['summary'].append({
                        '列名': col, '子组大小': subgroup_size,
                        'X̄̄': f"{stats.get('X_bar_bar', 0):.4f}", 'R̄': f"{stats.get('R_bar', 0):.4f}",
                        '超限点': ooc, '状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                    })
                    overall_summary.append({
                        '列名': col, '图表类型': f'X-bar R(n={subgroup_size})',
                        '均值': stats.get('X_bar_bar', np.mean(data)),
                        '标准差': stats.get('sigma_estimate', np.std(data, ddof=1)),
                        '超限点数': ooc, '受控状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                    })
                except Exception:
                    pass

        # --- X-bar S ---
        if 'xbar_s' in cont_modes:
            if 'xbar_s' not in results:
                results['xbar_s'] = {'charts': {}, 'summary': []}
            if n >= subgroup_size * 2:
                try:
                    r = spc_charts.xbar_s_chart(data, subgroup_size=subgroup_size, target=spc_target)
                    ooc = sum(r.get('ooc_points', {}).values()) if isinstance(r.get('ooc_points'), dict) else 0
                    stats = r.get('stats', {})
                    results['xbar_s']['charts'][col] = r.get('chart')
                    results['xbar_s']['summary'].append({
                        '列名': col, '子组大小': subgroup_size,
                        'X̄̄': f"{stats.get('X_bar_bar', 0):.4f}", 'S̄': f"{stats.get('S_bar', 0):.4f}",
                        '超限点': ooc, '状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                    })
                    overall_summary.append({
                        '列名': col, '图表类型': f'X-bar S(n={subgroup_size})',
                        '均值': stats.get('X_bar_bar', np.mean(data)),
                        '标准差': stats.get('sigma_estimate', np.std(data, ddof=1)),
                        '超限点数': ooc, '受控状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                    })
                except Exception:
                    pass

    # ======== 计数型 SPC (P / NP / C / U) ========
    # 计数型需要特殊列匹配：尝试从 numeric_cols 中选择合适的列
    if attr_modes and len(numeric_cols) >= 1:
        # 检查 ep 中是否有指定的计数型列映射
        attr_cols = ep.get('spc_attr_cols', {})
        # 智能猜测：如果 numeric_cols 的第1列是小整数，可能是缺陷/不良数据
        first_col = numeric_cols[0] if numeric_cols else None
        second_col = numeric_cols[1] if len(numeric_cols) >= 2 else None
        first_data = df[first_col].dropna() if first_col else pd.Series()

        def _is_count_like(s: pd.Series) -> bool:
            """判断是否像计数数据 (非负整数，范围较小)"""
            if len(s) == 0:
                return False
            is_int_like = (s == s.round()).all() or s.dtype in (np.int32, np.int64)
            is_nonneg = (s >= 0).all()
            return is_int_like and is_nonneg

        def _analyze_attr_chart(mode: str, chart_func, col_defect: str,
                                 col_size: str = None, fixed_size: int = None):
            """通用计数型图表分析"""
            if mode not in results:
                results[mode] = {'charts': {}, 'summary': []}
            try:
                defects = df[col_defect].dropna().values
                if col_size:
                    sizes = df[col_size].dropna().values
                    ml = min(len(defects), len(sizes))
                    defects, sizes = defects[:ml], sizes[:ml]
                    if len(defects) < 3:
                        return
                    if mode == 'p':
                        r = chart_func(defects, sizes, target=spc_target)
                    else:  # u
                        r = chart_func(defects, sizes, target=spc_target)
                else:
                    defects = defects.astype(float)
                    if len(defects) < 5:
                        return
                    if mode == 'np':
                        r = chart_func(defects, fixed_size, target=spc_target)
                    else:  # c
                        r = chart_func(defects, target=spc_target)

                ooc = sum(r.get('ooc_points', {}).values()) if isinstance(r.get('ooc_points'), dict) else 0
                chart_label = col_defect if not col_size else f'{col_defect} / {col_size}'
                results[mode]['charts'][chart_label] = r.get('chart')
                results[mode]['summary'].append({
                    '列名': chart_label,
                    '超限点': ooc,
                    '状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                })
                overall_summary.append({
                    '列名': chart_label, '图表类型': SPC_SUB_MODES[mode]['label'],
                    '均值': np.mean(defects),
                    '标准差': np.std(defects, ddof=1) if len(defects) > 1 else 0,
                    '超限点数': ooc,
                    '受控状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                })
            except Exception:
                pass

        # P 图：需要缺陷数 + 样本量
        if 'p' in attr_modes:
            p_col = attr_cols.get('p_defect_col', first_col)
            p_size_col = attr_cols.get('p_size_col', second_col)
            if p_col and p_size_col and p_col in df.columns and p_size_col in df.columns:
                _analyze_attr_chart('p', spc_charts.p_chart, p_col, col_size=p_size_col)
            elif _is_count_like(first_data) and len(numeric_cols) >= 2:
                # 智能猜测
                _analyze_attr_chart('p', spc_charts.p_chart, first_col, col_size=second_col)

        # NP 图：需要缺陷数 + 固定样本量
        if 'np' in attr_modes:
            np_col = attr_cols.get('np_col', first_col)
            np_size = ep.get('spc_np_size', 100)
            if np_col and np_col in df.columns and first_data is not None and len(first_data) >= 5:
                if _is_count_like(df[np_col].dropna()):
                    _analyze_attr_chart('np', spc_charts.np_chart, np_col, fixed_size=np_size)

        # C 图：需要缺陷数单列
        if 'c' in attr_modes:
            c_col = attr_cols.get('c_col', first_col)
            if c_col and c_col in df.columns:
                c_data = df[c_col].dropna()
                if len(c_data) >= 5 and _is_count_like(c_data):
                    _analyze_attr_chart('c', spc_charts.c_chart, c_col)

        # U 图：需要缺陷数 + 样本量
        if 'u' in attr_modes:
            u_col = attr_cols.get('u_defect_col', first_col)
            u_size_col = attr_cols.get('u_size_col', second_col)
            if u_col and u_size_col and u_col in df.columns and u_size_col in df.columns:
                _analyze_attr_chart('u', spc_charts.u_chart, u_col, col_size=u_size_col)
            elif _is_count_like(first_data) and len(numeric_cols) >= 2:
                _analyze_attr_chart('u', spc_charts.u_chart, first_col, col_size=second_col)

    return {
        'summary': overall_summary,
        **results,  # imr, xbar_r, xbar_s, p, np, c, u
    }


# 保留旧函数的向后兼容包装
def _analyze_spc_only(df: pd.DataFrame, numeric_cols: list = None) -> list:
    """仅执行 SPC I-MR 分析（向后兼容）"""
    result = _analyze_spc_shewhart(df, numeric_cols, spc_sub_modes=['imr'])
    return result.get('summary', [])


def _analyze_capability_only(df: pd.DataFrame, numeric_cols: list = None,
                              usl: float = None, lsl: float = None,
                              target: float = None,
                              subgroup_size: int = 1,
                              within_method: str = 'Rbar') -> list:
    """执行过程能力分析，支持用户指定规格限。

    参数:
        usl: 规格上限，None=自动估计 (mean + 3σ)
        lsl: 规格下限，None=自动估计 (mean - 3σ)
        target: 目标值
        subgroup_size: 子组大小
        within_method: 组内标准差方法 ('Rbar' 或 'Sbar')
    """
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    cap_results = []
    skipped_cols = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5:
            try:
                col_usl = usl if usl else np.mean(data) + 3 * np.std(data, ddof=1)
                col_lsl = lsl if lsl else np.mean(data) - 3 * np.std(data, ddof=1)
                r = capability.process_capability(data, usl=col_usl, lsl=col_lsl,
                                                  target=target,
                                                  subgroup_size=subgroup_size,
                                                  within_method=within_method)
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
        else:
            skipped_cols.append(f'{col}(n={len(data)})')
    if skipped_cols:
        st.warning(f'⚠️ 能力分析：数据量不足(<5点)已跳过 {", ".join(skipped_cols)}')
    return cap_results


def _analyze_correlation_only(df: pd.DataFrame, numeric_cols: list = None) -> Optional[dict]:
    """仅执行相关性矩阵"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if len(numeric_cols) >= 2:
        try:
            return stats_tools.correlation_matrix(df[numeric_cols])
        except Exception:
            pass
    return None


def _analyze_regression_only(df: pd.DataFrame, numeric_cols: list = None) -> list:
    """仅执行回归分析"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
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
        reg_results = sorted(reg_results, key=lambda x: abs(float(x['Pearson r'])), reverse=True)[:10]
    return reg_results


def _analyze_normality(df: pd.DataFrame, numeric_cols: list = None) -> list:
    """正态性检验"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 8:
            try:
                r = pareto_histogram.normality_test(data)
                # 取最常用的 Shapiro-Wilk 结果
                sw = r.get('Shapiro-Wilk', {})
                results.append({
                    '列名': col,
                    '样本量': len(data),
                    'Shapiro-Wilk p值': f"{sw.get('p_value', 0):.4f}",
                    '正态性': '✅ 正态' if sw.get('normal', True) else '❌ 非正态',
                })
            except Exception:
                pass
    return results


def _analyze_boxplot(df: pd.DataFrame, numeric_cols: list = None) -> dict:
    """箱线图"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5:
            try:
                r = pareto_histogram.box_plot(data, title=col)
                if 'chart' in r:
                    charts[col] = r['chart']
            except Exception:
                pass
    return {'charts': charts, 'columns': numeric_cols}


def _analyze_runchart(df: pd.DataFrame, numeric_cols: list = None) -> list:
    """运行图"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5:
            try:
                r = quality_tools.run_chart(data)
                stats = r.get('stats', {})
                results.append({
                    '列名': col,
                    '均值': f"{stats.get('均值', 0):.4f}",
                    '中位数': f"{stats.get('中位数', 0):.4f}",
                    '游程检验': stats.get('游程检验结论', 'N/A'),
                    '趋势检验': stats.get('趋势检验结论', 'N/A'),
                })
            except Exception:
                pass
    return results


def _analyze_stats_summary(df: pd.DataFrame, numeric_cols: list = None) -> list:
    """描述性统计"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    results = []
    skipped_cols = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 2:
            from scipy import stats as sp_stats
            results.append({
                '列名': col,
                '样本量': len(data),
                '均值': f'{np.mean(data):.4f}',
                '标准差': f'{np.std(data, ddof=1):.4f}',
                '最小值': f'{np.min(data):.4f}',
                '中位数': f'{np.median(data):.4f}',
                '最大值': f'{np.max(data):.4f}',
                '偏度': f'{sp_stats.skew(data):.4f}',
                '峰度': f'{sp_stats.kurtosis(data):.4f}',
            })
        else:
            skipped_cols.append(f'{col}(n={len(data)})')
    if skipped_cols:
        st.warning(f'⚠️ 描述性统计：数据量不足(<2点)已跳过 {", ".join(skipped_cols)}')
    return results


def _analyze_histogram(df: pd.DataFrame, numeric_cols: list = None) -> dict:
    """直方图（含统计）"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    stats_list = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 3:
            try:
                r = pareto_histogram.histogram_with_stats(data, title=col)
                if 'chart' in r:
                    charts[col] = r['chart']
                if 'stats' in r:
                    stats_list.append({'列名': col, **r['stats']})
            except Exception:
                pass
    return {'charts': charts, 'stats': stats_list, 'columns': numeric_cols}


def _analyze_ewma(df: pd.DataFrame, numeric_cols: list = None,
                   lam: float = 0.2, L: float = 2.7,
                   target: float = None) -> dict:
    """EWMA 控制图（每个数值列）"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 2:
            try:
                r = spc_advanced.ewma_chart(data, lam=lam, L=L, target=target)
                if 'chart' in r:
                    charts[col] = r['chart']
                ooc = sum(v for v in r.get('ooc_points', {}).values()) if isinstance(r.get('ooc_points'), dict) else 0
                results.append({
                    '列名': col,
                    '均值': f'{np.mean(data):.4f}',
                    '标准差': f'{np.std(data, ddof=1):.4f}',
                    '超限点': ooc,
                    '状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                })
            except Exception:
                pass
    return {'charts': charts, 'summary': results, 'columns': numeric_cols}


def _analyze_cusum(df: pd.DataFrame, numeric_cols: list = None,
                    k: float = 1.0, h: float = 4.0,
                    target: float = None) -> dict:
    """CUSUM 控制图"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 2:
            try:
                r = spc_advanced.cusum_chart(data, target=target, k=k, h=h)
                if 'chart' in r:
                    charts[col] = r['chart']
                ooc = sum(v for v in r.get('ooc_points', {}).values()) if isinstance(r.get('ooc_points'), dict) else 0
                results.append({
                    '列名': col,
                    '均值': f'{np.mean(data):.4f}',
                    '标准差': f'{np.std(data, ddof=1):.4f}',
                    '超限点': ooc,
                    '状态': '✅ 受控' if ooc == 0 else f'⚠️ {ooc}个超限点',
                })
            except Exception:
                pass
    return {'charts': charts, 'summary': results, 'columns': numeric_cols}


def _analyze_boxcox(df: pd.DataFrame, numeric_cols: list = None,
                     usl: float = None, lsl: float = None,
                     target: float = None,
                     subgroup_size: int = 1, within_method: str = 'Rbar') -> dict:
    """Box-Cox 变换过程能力"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5:
            try:
                r = capability.process_capability_boxcox(data, usl, lsl,
                                                          subgroup_size=subgroup_size,
                                                          within_method=within_method)
                if 'chart' in r:
                    charts[col] = r['chart']
                trans = r.get('transformation', {})
                results.append({
                    '列名': col,
                    'λ': str(trans.get('lambda', 'N/A')),
                    'Cp': f'{r.get("Cp", 0):.2f}' if r.get('Cp') else 'N/A',
                    'Cpk': f'{r.get("Cpk", 0):.2f}' if r.get('Cpk') else 'N/A',
                    'Pp': f'{r.get("Pp", 0):.2f}' if r.get('Pp') else 'N/A',
                    'Ppk': f'{r.get("Ppk", 0):.2f}' if r.get('Ppk') else 'N/A',
                    '评级': r.get('cpk_level', 'N/A'),
                })
            except Exception:
                pass
    return {'charts': charts, 'summary': results, 'columns': numeric_cols}


def _analyze_cgcgk(df: pd.DataFrame, numeric_cols: list = None,
                    tolerance: float = None, ref_value: float = None,
                    percent_tol: float = 20) -> dict:
    """Cg/Cgk 检具能力"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5 and tolerance:
            try:
                r = msa_advanced.cg_cgk(data, tolerance, ref_value, percent_tol=percent_tol)
                if 'chart' in r:
                    charts[col] = r['chart']
                results.append({
                    '列名': col,
                    'Cg': r.get('stats', {}).get('Cg', 'N/A'),
                    'Cgk': r.get('stats', {}).get('Cgk', 'N/A'),
                    'Cg评级': r.get('stats', {}).get('Cg 评级', 'N/A'),
                    'Cgk评级': r.get('stats', {}).get('Cgk 评级', 'N/A'),
                })
            except Exception:
                pass
    return {'charts': charts, 'summary': results, 'columns': numeric_cols}


def _analyze_uncertainty(df: pd.DataFrame, numeric_cols: list = None,
                          resolution: float = 0.001, cal_unc: float = 0.0,
                          temp_range: float = 0.0, temp_coeff: float = 0.0) -> dict:
    """测量不确定度"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5:
            try:
                r = msa_advanced.measurement_uncertainty(data, resolution, cal_unc,
                                                          temp_range, temp_coeff)
                if 'chart' in r:
                    charts[col] = r['chart']
                results.append({
                    '列名': col,
                    '合成标准不确定度 uc': r.get('result', {}).get('合成标准不确定度 uc', 'N/A'),
                    '扩展不确定度 U (k=2)': r.get('result', {}).get('扩展不确定度 U (k=2)', 'N/A'),
                })
            except Exception:
                pass
    return {'charts': charts, 'summary': results, 'columns': numeric_cols}


def _analyze_weibull(df: pd.DataFrame, numeric_cols: list = None) -> dict:
    """Weibull 可靠性分析"""
    if numeric_cols is None:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    charts = {}
    results = []
    for col in numeric_cols:
        data = df[col].dropna().values
        if len(data) >= 5:
            try:
                r = advanced_analysis.weibull_analysis(data)
                if 'chart' in r:
                    charts[col] = r['chart']
                results.append({
                    '列名': col,
                    '形状参数 β': r.get('params', {}).get('形状参数 β', 'N/A'),
                    '尺度参数 η': r.get('params', {}).get('尺度参数 η', 'N/A'),
                })
            except Exception:
                pass
    return {'charts': charts, 'summary': results, 'columns': numeric_cols}


def analyze_process_selective(df: pd.DataFrame, data_label: str = '',
                               modules: Optional[List[str]] = None,
                               cols: Optional[List[str]] = None,
                               extra_params: Optional[dict] = None) -> dict:
    """
    通用连续型数据 — 按模块选择性分析。
    支持所有 continuous group 模块:
        spc, capability, correlation, regression,
        normality, boxplot, run_chart, stats_summary,
        histogram, ewma, cusum, box_cox, cg_cgk,
        uncertainty, weibull
    cols: 指定要分析的数值列，None=全部数值列
    extra_params: 额外参数字典，如 {'ewma_lam':0.3, 'cusum_k':0.5, 'tolerance':0.1, ...}
    """
    if modules is None:
        modules = list(CONTINUOUS_MODULES.keys())

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if cols is not None:
        # 用户明确指定了分析列（可能为空列表），只保留有效列
        numeric_cols = [c for c in cols if c in numeric_cols]
    # 注意：不再 fallback 到全部数值列，尊重用户选择

    results = {}
    ep = extra_params or {}

    # 基础图形 — SPC 休哈特控制图（支持子类型多选）
    if 'spc' in modules:
        spc_sub = ep.get('spc_sub_modes', ['imr'])
        results['spc'] = _analyze_spc_shewhart(df, numeric_cols,
                                                spc_sub_modes=spc_sub,
                                                extra_params=ep)
    if 'histogram' in modules:
        results['histogram'] = _analyze_histogram(df, numeric_cols)
    if 'boxplot' in modules:
        results['boxplot'] = _analyze_boxplot(df, numeric_cols)
    if 'run_chart' in modules:
        results['run_chart'] = _analyze_runchart(df, numeric_cols)

    # SPC 高级
    if 'ewma' in modules:
        results['ewma'] = _analyze_ewma(df, numeric_cols,
                                         lam=ep.get('ewma_lam', 0.2),
                                         L=ep.get('ewma_L', 2.7),
                                         target=ep.get('spc_target'))
    if 'cusum' in modules:
        results['cusum'] = _analyze_cusum(df, numeric_cols,
                                           k=ep.get('cusum_k', 1.0),
                                           h=ep.get('cusum_h', 4.0),
                                           target=ep.get('spc_target'))

    # 能力分析
    if 'capability' in modules:
        results['capability'] = _analyze_capability_only(
            df, numeric_cols,
            usl=ep.get('usl'),
            lsl=ep.get('lsl'),
            target=ep.get('spc_target'),
            subgroup_size=ep.get('bc_subgroup', 1),
            within_method=ep.get('bc_method', 'Rbar'),
        )
    if 'box_cox' in modules:
        results['box_cox'] = _analyze_boxcox(df, numeric_cols,
                                              usl=ep.get('usl'),
                                              lsl=ep.get('lsl'),
                                              target=ep.get('spc_target'),
                                              subgroup_size=ep.get('bc_subgroup', 1),
                                              within_method=ep.get('bc_method', 'Rbar'))
    if 'cg_cgk' in modules:
        results['cg_cgk'] = _analyze_cgcgk(df, numeric_cols,
                                            tolerance=ep.get('cg_tolerance'),
                                            ref_value=ep.get('cg_ref'),
                                            percent_tol=ep.get('cg_pct', 20))

    # 统计推断
    if 'normality' in modules:
        results['normality'] = _analyze_normality(df, numeric_cols)
    if 'correlation' in modules:
        results['correlation'] = _analyze_correlation_only(df, numeric_cols)
    if 'regression' in modules:
        results['regression'] = _analyze_regression_only(df, numeric_cols)
    if 'stats_summary' in modules:
        results['stats_summary'] = _analyze_stats_summary(df, numeric_cols)

    # 测量系统
    if 'uncertainty' in modules:
        results['uncertainty'] = _analyze_uncertainty(df, numeric_cols,
                                                       resolution=ep.get('unc_res', 0.001),
                                                       cal_unc=ep.get('unc_cal', 0.0),
                                                       temp_range=ep.get('unc_tr', 5.0),
                                                       temp_coeff=ep.get('unc_tc', 0.0))

    # 特殊分析
    if 'weibull' in modules:
        results['weibull'] = _analyze_weibull(df, numeric_cols)

    spc_result = results.get('spc', {})
    spc_results = spc_result.get('summary', []) if isinstance(spc_result, dict) else spc_result
    reg_results = results.get('regression', [])
    normality_results = results.get('normality', [])

    return {
        'type': 'continuous',
        'label': data_label,
        'numeric_cols': numeric_cols,
        'results': results,
        'modules_selected': modules,
        'summary': {
            '数据行数': len(df),
            '数值列数': len(numeric_cols),
            'SPC受控列数': sum(1 for s in spc_results if '受控' in s.get('受控状态', '')),
            'SPC异常列数': sum(1 for s in spc_results if '超限' in s.get('受控状态', '')),
            '显著相关对数': len(reg_results),
            '正态列数': sum(1 for n in normality_results if '✅' in n.get('正态性', '')),
        }
    }


def run_single_analysis(df: pd.DataFrame, data_type: str,
                        grr_tolerance: Optional[float] = None,
                        modules: Optional[List[str]] = None,
                        params: Optional[dict] = None) -> dict:
    """
    对单个 DataFrame 执行指定类型的分析（纯手动）。

    参数:
        df: 数据 DataFrame
        data_type: 类型代码
        grr_tolerance: GRR公差
        modules: 连续数据类型的分析模块列表 (可选，默认全部)
        params: 额外参数字典，如 {'cat_col':'不良类型','cnt_col':'数量',
               'part_col':'Part','op_col':'Operator','meas_col':'Measurement',
               'cols':['Si','Mg'],'batch_col':'批次','meas_cols':['测量值1','测量值2']}

    返回:
        分析结果 dict
    """
    params = params or {}
    # 优先使用 params 中的 tolerance，支持 per-file 设置
    if params.get('tolerance') is not None and params.get('tolerance') != '':
        try:
            grr_tolerance = float(params['tolerance'])
        except (ValueError, TypeError):
            grr_tolerance = grr_tolerance  # fallback to argument
    if data_type == 'pareto':
        return analyze_pareto(df,
                              cat_col=params.get('cat_col'),
                              cnt_col=params.get('cnt_col'))
    elif data_type == 'grr':
        return analyze_grr(df, grr_tolerance,
                           part_col=params.get('part_col'),
                           op_col=params.get('op_col'),
                           meas_col=params.get('meas_col'))
    elif data_type == 'component':
        r = analyze_process_selective(df, '化学成分', modules, cols=params.get('cols'))
        r['type'] = 'component'
        return r
    elif data_type == 'mechanics':
        r = analyze_process_selective(df, '力学性能', modules, cols=params.get('cols'))
        r['type'] = 'mechanics'
        return r
    elif data_type == 'dimension':
        return analyze_dimension(df,
                                 batch_col=params.get('batch_col'),
                                 meas_cols=params.get('meas_cols'))
    else:
        r = analyze_process_selective(df, '通用', modules, cols=params.get('cols'))
        r['type'] = 'continuous'
        return r


def batch_import_and_analyze(
    uploaded_files: list,
    grr_tolerance: Optional[float] = None,
    type_mapping: Optional[Dict[str, str]] = None,
    module_selections: Optional[Dict[str, List[str]]] = None,
    params_map: Optional[Dict[str, dict]] = None,
) -> Tuple[Dict[str, pd.DataFrame], List[dict], str]:
    """
    批量导入多个CSV文件并按选中的模块分析。

    参数:
        uploaded_files: Streamlit UploadedFile 对象列表
        grr_tolerance: GRR分析的公差值
        type_mapping: （向后兼容，新模式下可省略）{文件名: 类型代码}
        module_selections: {文件名: [模块key列表]}  — 每个文件选中的模块
        params_map: {文件名: {参数字典}}  — 各文件的分析参数

    返回:
        (数据字典, 分析结果列表, 报告字符串)
    """
    if module_selections is None:
        module_selections = {}
    if params_map is None:
        params_map = {}

    data_dict = {}
    analyses = []

    for uploaded_file in uploaded_files:
        fname = uploaded_file.name

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

        # 获取选中的模块
        modules = module_selections.get(fname, [])
        # 向后兼容：如果提供了 type_mapping 但没有 module_selections
        if not modules and type_mapping and fname in type_mapping:
            dtype = type_mapping[fname]
            if dtype in ('component', 'mechanics', 'continuous'):
                modules = list(CONTINUOUS_MODULES.keys())
            else:
                modules = [dtype]

        if not modules:
            analyses.append({
                'type': 'error',
                'filename': fname,
                'error': f'未选择任何分析模块，请为「{fname}」勾选模块',
            })
            continue

        params = params_map.get(fname, {})

        # 分组：standalone（需特定列格式） vs continuous（适用任何数值列）
        STANDALONE_MODULE_KEYS = {'pareto', 'grr', 'grr_attribute', 'dimension'}
        standalone_mods = [m for m in modules if m in STANDALONE_MODULE_KEYS]
        continuous_mods = [m for m in modules if m not in STANDALONE_MODULE_KEYS]

        # 提取连续型模块额外参数
        extra_params = {k: v for k, v in params.items() if k not in (
            'cat_col', 'cnt_col', 'part_col', 'op_col', 'meas_col',
            'batch_col', 'meas_cols', 'cols', 'tolerance',
            'ref_col', 'op_cols', 'attr_ops'
        )}
        if 'tolerance' in params:
            extra_params['cg_tolerance'] = params['tolerance']

        # ---- standalone 模块各自分析 ----
        for mod in standalone_mods:
            try:
                if mod == 'pareto':
                    analysis = analyze_pareto(df,
                        cat_col=params.get('cat_col'),
                        cnt_col=params.get('cnt_col'))
                elif mod == 'grr':
                    # 优先使用 params 中的 per-file tolerance
                    file_tol = grr_tolerance
                    if 'tolerance' in params and params['tolerance'] is not None and params['tolerance'] != '':
                        try:
                            file_tol = float(params['tolerance'])
                        except (ValueError, TypeError):
                            pass
                    analysis = analyze_grr(df, file_tol,
                        part_col=params.get('part_col'),
                        op_col=params.get('op_col'),
                        meas_col=params.get('meas_col'))
                elif mod == 'grr_attribute':
                    # 计数型 GRR: 参考列 + 操作员判定列
                    ref_col = params.get('ref_col', df.columns[0])
                    op_cols = params.get('op_cols', params.get('attr_ops', []))
                    if not op_cols:
                        op_cols = [c for c in df.columns if c != ref_col][:5]
                    ref = df[ref_col].values
                    appraisers = {c: df[c].values for c in op_cols if c in df.columns}
                    r_attr = msa_advanced.attribute_gage_rr(ref, appraisers)
                    analysis = {
                        'type': 'grr_attribute',
                        'chart': r_attr.get('chart'),
                        'kappa_summary': r_attr.get('kappa_summary', []),
                        'between_operators_agreement': r_attr.get('between_operators_agreement', 0),
                        'summary': {
                            '参考列': ref_col,
                            '操作员数': len(op_cols),
                            '样本数': len(ref),
                            '两两一致性': f"{r_attr.get('between_operators_agreement', 0):.1%}",
                        }
                    }
                elif mod == 'dimension':
                    analysis = analyze_dimension(df,
                        batch_col=params.get('batch_col'),
                        meas_cols=params.get('meas_cols'))
                else:
                    continue
                analysis['filename'] = fname
                analysis['module'] = mod
                analysis['data_type'] = mod
                analyses.append(analysis)
            except Exception as e:
                analyses.append({
                    'type': 'error',
                    'filename': fname,
                    'error': f'分析模块 {ALL_MODULES.get(mod,{}).get("label",mod)} 失败: {str(e)}',
                })

        # ---- continuous 模块合并分析 ----
        if continuous_mods:
            try:
                analysis = analyze_process_selective(df, '数值分析', continuous_mods,
                                                      cols=params.get('cols'),
                                                      extra_params=extra_params)
                analysis['filename'] = fname
                analysis['module'] = 'continuous'
                analysis['data_type'] = 'continuous'
                analyses.append(analysis)
            except Exception as e:
                analyses.append({
                    'type': 'error',
                    'filename': fname,
                    'error': f'连续数据分析失败: {str(e)}',
                })

    # 生成报告
    valid_analyses = [a for a in analyses if a.get('type') != 'error']
    valid_filenames = [a.get('filename', '') for a in valid_analyses]
    report = generate_report(valid_analyses, valid_filenames) if valid_analyses else '无有效数据可分析'

    return data_dict, analyses, report


def build_files_data(uploaded_files: list, type_mapping: Optional[Dict[str, str]] = None,
                     module_selections: Optional[Dict[str, List[str]]] = None,
                     params_map: Optional[Dict[str, dict]] = None) -> list:
    """
    将上传的 CSV 原始数据打包为可存储格式（含模块和参数信息）。
    """
    if type_mapping is None:
        type_mapping = {}
    if module_selections is None:
        module_selections = {}
    if params_map is None:
        params_map = {}
    files_data = []
    for uf in uploaded_files:
        raw_bytes = uf.getvalue()
        try:
            csv_str = raw_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                csv_str = raw_bytes.decode('utf-8')
            except UnicodeDecodeError:
                csv_str = raw_bytes.decode('gbk', errors='replace')
        files_data.append({
            'filename': uf.name,
            'csv_data': csv_str,
            'data_type': type_mapping.get(uf.name, ''),
            'modules': module_selections.get(uf.name, []),
            'params': params_map.get(uf.name, {}),
        })
    return files_data


def build_analyses_summary(analyses: List[dict]) -> list:
    """
    从分析结果中提取摘要（排除不可序列化的图表对象），用于存储。
    """
    summary_list = []
    for a in analyses:
        summary_list.append({
            'filename': a.get('filename', ''),
            'type': a.get('type', 'unknown'),
            'summary': a.get('summary', {}),
            'data_type': a.get('data_type', ''),
            'modules_selected': a.get('modules_selected', []),
        })
    return summary_list


def restore_analyses_from_files(files_data: list,
                                grr_tolerance: Optional[float] = None) -> Tuple[Dict[str, pd.DataFrame], List[dict]]:
    """
    从数据库加载的文件数据重新执行分析（恢复完整分析结果含图表）。
    支持新的模块化分析模式。
    """
    data_dict = {}
    analyses = []

    for fd in files_data:
        fname = fd.get('filename', 'unknown.csv')
        csv_str = fd.get('csv_data', '')
        modules = fd.get('modules', [])
        params = fd.get('params', {})
        data_type = fd.get('data_type', '')

        if not csv_str:
            continue

        try:
            from io import StringIO
            df = pd.read_csv(StringIO(csv_str))
        except Exception:
            continue

        data_dict[fname] = df

        # 向后兼容：如果 modules 为空但有 data_type，推导模块
        if not modules and data_type:
            if data_type in ('component', 'mechanics', 'continuous'):
                modules = list(CONTINUOUS_MODULES.keys())
            else:
                modules = [data_type]
            # 也把 data_type 当作模块来处理（适用于 pareto/grr/dimension）
            if data_type in ('component', 'mechanics'):
                # 用 continuous 模块替代
                modules = list(CONTINUOUS_MODULES.keys())

        if not modules:
            analyses.append({
                'type': 'error',
                'filename': fname,
                'error': '没有可恢复的分析模块',
            })
            continue

        # 分组（与 batch_import_and_analyze 保持一致）
        STANDALONE_MODULE_KEYS = {'pareto', 'grr', 'grr_attribute', 'dimension'}
        standalone_mods = [m for m in modules if m in STANDALONE_MODULE_KEYS]
        continuous_mods = [m for m in modules if m not in STANDALONE_MODULE_KEYS]

        extra_params = {k: v for k, v in params.items() if k not in (
            'cat_col', 'cnt_col', 'part_col', 'op_col', 'meas_col',
            'batch_col', 'meas_cols', 'cols', 'tolerance',
            'ref_col', 'op_cols', 'attr_ops'
        )}

        for mod in standalone_mods:
            try:
                if mod == 'pareto':
                    analysis = analyze_pareto(df,
                        cat_col=params.get('cat_col'),
                        cnt_col=params.get('cnt_col'))
                elif mod == 'grr':
                    # 优先使用 params 中的 per-file tolerance
                    file_tol = grr_tolerance
                    if 'tolerance' in params and params['tolerance'] is not None and params['tolerance'] != '':
                        try:
                            file_tol = float(params['tolerance'])
                        except (ValueError, TypeError):
                            pass
                    analysis = analyze_grr(df, file_tol,
                        part_col=params.get('part_col'),
                        op_col=params.get('op_col'),
                        meas_col=params.get('meas_col'))
                elif mod == 'grr_attribute':
                    ref_col = params.get('ref_col', df.columns[0])
                    op_cols = params.get('op_cols', params.get('attr_ops', []))
                    if not op_cols:
                        op_cols = [c for c in df.columns if c != ref_col][:5]
                    ref = df[ref_col].values
                    appraisers = {c: df[c].values for c in op_cols if c in df.columns}
                    r_attr = msa_advanced.attribute_gage_rr(ref, appraisers)
                    analysis = {
                        'type': 'grr_attribute',
                        'chart': r_attr.get('chart'),
                        'kappa_summary': r_attr.get('kappa_summary', []),
                        'between_operators_agreement': r_attr.get('between_operators_agreement', 0),
                        'summary': {
                            '参考列': ref_col,
                            '操作员数': len(op_cols),
                            '样本数': len(ref),
                            '两两一致性': f"{r_attr.get('between_operators_agreement', 0):.1%}",
                        }
                    }
                elif mod == 'dimension':
                    analysis = analyze_dimension(df,
                        batch_col=params.get('batch_col'),
                        meas_cols=params.get('meas_cols'))
                else:
                    continue
                analysis['filename'] = fname
                analysis['module'] = mod
                analysis['data_type'] = mod
                analyses.append(analysis)
            except Exception as e:
                analyses.append({
                    'type': 'error',
                    'filename': fname,
                    'error': f'重新分析失败 ({mod}): {str(e)}',
                })

        if continuous_mods:
            try:
                analysis = analyze_process_selective(df, '数值分析', continuous_mods,
                                                      cols=params.get('cols'),
                                                      extra_params=extra_params)
                analysis['filename'] = fname
                analysis['module'] = 'continuous'
                analysis['data_type'] = 'continuous'
                analyses.append(analysis)
            except Exception as e:
                analyses.append({
                    'type': 'error',
                    'filename': fname,
                    'error': f'重新分析失败 (连续数据): {str(e)}',
                })

    return data_dict, analyses


# ============================================================
# 第五部分：可复用的模块选择弹窗组件
# ============================================================

def render_module_selector(
    current_modules: list,
    session_key_prefix: str = 'module_sel',
    columns: int = 3,
    show_confirm_button: bool = True,
) -> list:
    """
    可复用的分析模块选择 UI 组件。
    按分组展示所有模块，每组有「全选」「清空」按钮，返回选中的模块列表。

    参数:
        current_modules: 当前已选中的模块 key 列表
        session_key_prefix: session_state key 前缀（避免冲突）
        columns: 分组列数（默认 3 列）
        show_confirm_button: 是否显示底部确认/取消按钮

    返回:
        list: 选中的模块 key 列表

    使用方式（两种）:

        方式 A — 直接内嵌在页面中:
            selected = batch_analysis.render_module_selector(
                st.session_state.get('my_selected_modules', []),
                session_key_prefix='mypage'
            )

        方式 B — 在 @st.dialog 弹窗中使用:
            @st.dialog('选择分析模块')
            def my_dialog():
                selected = batch_analysis.render_module_selector(...)
                if st.button('确认', type='primary'):
                    st.session_state.my_selected_modules = selected
                    st.rerun()
    """
    # 用 session_state 存储临时选择状态
    temp_key = f'{session_key_prefix}_temp'
    if temp_key not in st.session_state:
        st.session_state[temp_key] = list(current_modules)

    new_modules = []
    group_cols = st.columns(columns)

    for gi, (gname, gkey) in enumerate(MODULE_GROUPS):
        col_idx = gi % columns
        with group_cols[col_idx]:
            # ---- 组标题 + 全选/清空 ----
            st.caption(f'**{gname}**')
            group_mods = [(k, v) for k, v in ALL_MODULES.items()
                         if v['group'] == gkey]
            all_selected = all(k in st.session_state[temp_key] for k, _ in group_mods)

            cm1, cm2 = st.columns([1, 1])
            with cm1:
                if st.button('全选' if not all_selected else '已全选',
                             key=f'{session_key_prefix}_selall_{gkey}',
                             use_container_width=True,
                             disabled=all_selected):
                    for k, _ in group_mods:
                        if k not in st.session_state[temp_key]:
                            st.session_state[temp_key].append(k)
                        # 清除 checkbox 自身的 session_state key，使下次渲染时 value 参数生效
                        ck = f'{session_key_prefix}_mod_{k}'
                        if ck in st.session_state:
                            del st.session_state[ck]
                    st.rerun()
            with cm2:
                has_any = any(k in st.session_state[temp_key] for k, _ in group_mods)
                if st.button('清空', key=f'{session_key_prefix}_clear_{gkey}',
                             use_container_width=True,
                             disabled=not has_any):
                    st.session_state[temp_key] = [
                        m for m in st.session_state[temp_key]
                        if m not in {k for k, _ in group_mods}
                    ]
                    for k, _ in group_mods:
                        ck = f'{session_key_prefix}_mod_{k}'
                        if ck in st.session_state:
                            del st.session_state[ck]
                    st.rerun()

            # ---- 各模块 checkbox ----
            for mod_key, mod_info in group_mods:
                checked = st.checkbox(
                    mod_info['label'],
                    value=mod_key in st.session_state[temp_key],
                    key=f'{session_key_prefix}_mod_{mod_key}',
                    help=mod_info.get('desc', ''),
                )
                if checked and mod_key not in new_modules:
                    new_modules.append(mod_key)
                elif not checked and mod_key in new_modules:
                    new_modules.remove(mod_key)

    # 同步回 session_state
    st.session_state[temp_key] = new_modules

    # ---- 底部统计 + 确认按钮 ----
    if show_confirm_button:
        st.divider()
        sel_count = len(new_modules)
        if sel_count > 0:
            labels = [ALL_MODULES[m]['label'] for m in new_modules if m in ALL_MODULES]
            c_info, c_btn = st.columns([3, 1])
            with c_info:
                st.caption(f'已选择 **{sel_count}** 个模块：' + ' · '.join(labels))
            with c_btn:
                confirmed = st.button('✅ 确认选择', type='primary',
                                      use_container_width=True,
                                      key=f'{session_key_prefix}_confirm',
                                      disabled=sel_count == 0)
                if confirmed:
                    result = list(new_modules)
                    st.session_state[f'{session_key_prefix}_result'] = result
                    st.session_state.pop(temp_key, None)
                    return result
        else:
            st.caption('⚠️ 请至少选择一个分析模块')
            st.button('✅ 确认选择', type='primary', use_container_width=True,
                     key=f'{session_key_prefix}_confirm', disabled=True)

    return new_modules


# ============================================================
# 第六部分：按钮导航式模块选择（跳转式）
# ============================================================

# 模块导航定义 — 与侧边栏菜单一一对应
MODULE_NAV_ITEMS = [
    {'key': 'spc',            'icon': '📈', 'label': 'SPC 控制图',
     'desc': '休哈特七图 / EWMA / CUSUM / 多变量T²'},
    {'key': 'capability',      'icon': '🎯', 'label': '过程能力分析',
     'desc': 'Cp/Cpk/Pp/Ppk / Box-Cox / Cg/Cgk'},
    {'key': 'quality_tools',   'icon': '📊', 'label': '质量图形工具',
     'desc': '帕累托 / 直方图 / 箱线图 / 运行图 / 鱼骨图'},
    {'key': 'msa',             'icon': '🔬', 'label': '测量系统分析 MSA',
     'desc': '计量型GRR / 计数型GRR / 不确定度'},
    {'key': 'stats',           'icon': '🔢', 'label': '统计推断',
     'desc': '正态性检验 / 假设检验 / 回归 / 相关性'},
    {'key': 'advanced',        'icon': '🧪', 'label': '高级分析',
     'desc': 'DOE / Weibull / 抽样方案 / FMEA'},
]


def render_module_nav(session_key_prefix: str = 'module_nav') -> str:
    """
    按钮列表式的模块导航选择器。
    每个模块一行，带图标和描述，点击后返回对应的模块 key。

    参数:
        session_key_prefix: session_state key 前缀

    返回:
        str: 被选中的模块 key（如 'spc', 'capability' 等），未选中返回 ''

    使用方式:

        @st.dialog('选择分析模块')
        def nav_dialog():
            selected = batch_analysis.render_module_nav('batch_page')
            if selected:
                st.session_state.menu = f'对应菜单项'
                st.rerun()
    """
    selected_key = ''
    result_key = f'{session_key_prefix}_result'

    # 检查是否已有结果（上一次点击）
    if result_key in st.session_state:
        return st.session_state.pop(result_key)

    for item in MODULE_NAV_ITEMS:
        col_icon, col_text, col_go = st.columns([0.5, 4, 0.8])
        with col_icon:
            st.markdown(f'### {item["icon"]}')
        with col_text:
            st.markdown(f'**{item["label"]}**')
            st.caption(item['desc'])
        with col_go:
            # 竖向居中对齐
            st.markdown('<br>', unsafe_allow_html=True)
            if st.button('进入 →', key=f'{session_key_prefix}_go_{item["key"]}',
                         use_container_width=True, type='primary'):
                selected_key = item['key']
                break

    if selected_key:
        st.session_state[result_key] = selected_key
        st.rerun()

    return selected_key
