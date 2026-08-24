# -*- coding: utf-8 -*-
"""
收料通知单清洗：ERP 导出的单据中「单据编号 / 收料日期 / 单据状态」仅首行有值，
后续物料行同列为空。本脚本按单据号分组统一填充这三列，保证同单数据一致。

规则（严谨模式）：
1. 排除「合计」汇总行；
2. 「单据编号」向下填充(ffill)确立组归属（后续空行归属到上方最近单据）；
3. 「收料日期」「单据状态」按组校验：
   - 组内非空值去重后 > 1 个 → 冲突拦截，不填充该组，输出冲突清单；
   - 恰好 1 个 → 整组统一填充；
   - 0 个 → 保持空白并输出「无值单据」清单；
4. 原文件只读，结果输出到新文件（原名 + _已填充.xlsx）。

用法:
    python fill_receipt_dates.py [输入.xlsx] [-o 输出.xlsx]
不带参数时自动在 %TEMP%/codebuddy-dropped-files 下查找「收料通知单*.xlsx」。
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

GROUP_COL = '单据编号'
FILL_COLS = ['收料日期', '单据状态']
SUM_MARKERS = ('合计', '总计')


def find_input_file(path=None):
    if path and os.path.exists(path):
        return path
    if path:
        print(f'[警告] 输入文件不存在: {path}')
    pattern = os.path.join(os.environ.get('TEMP', r'C:\Users\Administrator\AppData\Local\Temp'),
                           'codebuddy-dropped-files', '*', '收料通知单*.xlsx')
    files = glob.glob(pattern)
    if not files:
        sys.exit('未找到收料通知单文件。请通过参数指定输入文件路径。')
    return files[0]


def is_summary_row(series_products, series_suppliers):
    """识别「合计」汇总行"""
    return (
        series_products.astype(str).str.strip().isin(SUM_MARKERS) |
        series_suppliers.astype(str).str.strip().isin(SUM_MARKERS)
    )


def fill_by_order(df):
    """
    按单据号统一填充。返回 (结果DataFrame, 提示信息dict)。
    提示信息: {'conflicts': [(单号, 列, 日期列表)...],
              'no_value': [(单号, 列)...],
              'filled_rows': {列名: 填充行数}}
    """
    data = df.copy()
    sum_mask = is_summary_row(data.get('物料编码', pd.Series(index=data.index)),
                              data.get('供应商', pd.Series(index=data.index)))

    # 1. 单据编号向下填充确立组归属；合计行不参与，保持原样
    data[GROUP_COL] = data[GROUP_COL].ffill()
    data.loc[sum_mask, GROUP_COL] = np.nan
    work = data[~sum_mask]  # 仅数据行参与分组填充
    grp = work[GROUP_COL].fillna('__无编号__')

    notes = {'conflicts': [], 'no_value': [], 'filled_rows': {}}
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


def main():
    ap = argparse.ArgumentParser(description='收料通知单清洗：按单据号统一填充收料日期/单据状态')
    ap.add_argument('input', nargs='?', default=None, help='输入 xlsx 路径（缺省自动查找）')
    ap.add_argument('-o', '--output', default=None, help='输出 xlsx 路径（缺省: 原名_已填充.xlsx）')
    args = ap.parse_args()

    in_path = find_input_file(args.input)
    df = pd.read_excel(in_path)
    print(f'读取: {in_path}  共 {len(df)} 行')

    out_df, notes = fill_by_order(df)
    out_path = args.output or (os.path.splitext(in_path)[0] + '_已填充.xlsx')
    out_df.to_excel(out_path, index=False)
    print(f'写出: {out_path}  共 {len(out_df)} 行')

    print('\n===== 处理汇总 =====')
    for col, n in notes['filled_rows'].items():
        print(f'{col}: 填充 {n} 行')
    if notes['conflicts']:
        print(f'\n[冲突] 同单存在多个不同值，未填充（需人工处理）:')
        for g, col, vals in notes['conflicts']:
            print(f'  单号 {g} | {col}: {vals}')
    else:
        print('\n[冲突] 无（同单值唯一，逻辑严谨性校验通过）')
    if notes['no_value']:
        print(f'\n[提示] 整单无{"/".join(c for _, c, _ in [])}值的单:')
        seen = set()
        for g, col in notes['no_value']:
            key = (g, col)
            if key in seen:
                continue
            seen.add(key)
            print(f'  单号 {g} | {col} 无值')


if __name__ == '__main__':
    main()
