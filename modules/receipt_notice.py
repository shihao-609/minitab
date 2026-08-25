# -*- coding: utf-8 -*-
"""
ERP 收料通知单清洗模块
========================
ERP 导出的收料通知单中「单据编号 / 收料日期 / 单据状态 / 供应商 / 整单关闭状态」
可能仅首行有值（合并单元格导出），后续物料行同列为空。
本模块按单据号分组统一填充，保证同单数据一致。

严谨模式：
  1. 排除「合计 / 总计」汇总行（保持原样，不参与填充）；
  2. 「单据编号」向下填充(ffill)确立组归属；
  3. 填充列（收料日期/单据状态/供应商/整单关闭状态）按组校验：
     - 组内非空值去重后 > 1 个 → 冲突拦截，不填充该组，记入 notes['conflicts']；
     - 恰好 1 个 → 整组统一填充；
     - 0 个 → 保持空白，记入 notes['no_value']。

该模块不依赖 streamlit / supabase，脚本与 Web 端均可复用。
"""
import numpy as np
import pandas as pd

GROUP_COL = '单据编号'
FILL_COLS = ['收料日期', '单据状态', '供应商', '整单关闭状态']
SUM_MARKERS = ('合计', '总计')


def _is_summary_row(data):
    """识别「合计/总计」汇总行"""
    products = data.get('物料编码', pd.Series(index=data.index))
    suppliers = data.get('供应商', pd.Series(index=data.index))
    return (
        products.astype(str).str.strip().isin(SUM_MARKERS) |
        suppliers.astype(str).str.strip().isin(SUM_MARKERS)
    )


def fill_receipt_order(df):
    """
    按单据号统一填充「收料日期 / 单据状态 / 供应商 / 整单关闭状态」。

    参数
    ----
    df : DataFrame，应包含「单据编号」及 FILL_COLS 中的 ERP 导出列。

    返回
    ----
    (清洗后 DataFrame, notes)
    notes = {
        'conflicts': [(单号, 列, [不同值...]), ...],   # 同单多值，已拦截未填充
        'no_value':  [(单号, 列), ...],                # 整单无值，保持空白
        'filled_rows': {列名: 填充行数, ...},
    }
    """
    data = df.copy()
    notes = {'conflicts': [], 'no_value': [], 'filled_rows': {c: 0 for c in FILL_COLS}}

    if GROUP_COL not in data.columns:
        # 非收料通知单格式，原样返回
        return data, notes

    sum_mask = _is_summary_row(data)

    # 1. 单据编号向下填充确立组归属；合计行不参与，保持原样
    data[GROUP_COL] = data[GROUP_COL].ffill()
    data.loc[sum_mask, GROUP_COL] = np.nan
    work = data[~sum_mask]  # 仅数据行参与分组填充
    grp = work[GROUP_COL].fillna('__无编号__')

    for col in FILL_COLS:
        if col not in data.columns:
            notes['filled_rows'][col] = 0
            continue
        out = pd.Series(np.nan, index=data.index, dtype=object)
        filled = 0
        for g, sub in work.groupby(grp, sort=False):
            idx = sub.index
            vals = work.loc[idx, col].dropna().unique()
            if len(vals) > 1:
                notes['conflicts'].append((g, col, [str(v) for v in vals]))
            elif len(vals) == 1:
                was_na = work.loc[idx, col].isna().sum()
                out.loc[idx] = vals[0]
                filled += int(was_na)
            else:
                notes['no_value'].append((g, col))
        # 合计行保持原值，不参与填充
        out.loc[sum_mask] = data.loc[sum_mask, col]
        data[col] = out
        notes['filled_rows'][col] = filled

    return data, notes
