# -*- coding: utf-8 -*-
"""
收料通知单清洗命令行工具（复用 modules.receipt_notice 逻辑）
============================================================
ERP 导出的单据中「单据编号 / 收料日期 / 单据状态」仅首行有值，后续物料行同列为空。
本脚本按单据号分组统一填充这三列，保证同单数据一致；原文件只读，输出新文件。

规则（严谨模式，详见 modules.receipt_notice）：
  1. 排除「合计/总计」汇总行；
  2. 「单据编号」向下填充(ffill)确立组归属；
  3. 「收料日期」「单据状态」按组校验：组内多值→拦截并输出冲突清单；
     单值→整组统一填充；无值→保持空白并提示；
  4. 原文件只读，结果输出到新文件（原名 + _已填充.xlsx）。

用法:
    python fill_receipt_dates.py [输入.xlsx] [-o 输出.xlsx]
不带参数时自动在 %TEMP%/codebuddy-dropped-files 下查找「收料通知单*.xlsx」。
"""
import argparse
import glob
import os
import sys

import pandas as pd

# 使 scripts 目录下的脚本能导入项目根下的 modules 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modules.receipt_notice import fill_receipt_order  # noqa: E402


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


def main():
    ap = argparse.ArgumentParser(description='收料通知单清洗：按单据号统一填充收料日期/单据状态')
    ap.add_argument('input', nargs='?', default=None, help='输入 xlsx 路径（缺省自动查找）')
    ap.add_argument('-o', '--output', default=None, help='输出 xlsx 路径（缺省: 原名_已填充.xlsx）')
    args = ap.parse_args()

    in_path = find_input_file(args.input)
    df = pd.read_excel(in_path)
    print(f'读取: {in_path}  共 {len(df)} 行')

    out_df, notes = fill_receipt_order(df)
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
        print('\n[提示] 整单无值的单:')
        for g, col in notes['no_value']:
            print(f'  单号 {g} | {col} 无值')


if __name__ == '__main__':
    main()
