"""
送检清单 vs 检验清单 对比模块
================================
功能：
  1. 解析送检清单 Excel（6 核心列：供应商 / 物料编码 / 规格型号 / 物料名称 / 收料日期 / 实收数量）
  2. 解析检验清单 Excel（6 核心列：供应商 / 物料编码 / 规格型号 / 物料名称 / 质检日期 / 检验数量）
  3. 送检记录幂等去重入库（去重键：供应商+物料编码+规格型号+物料名称+实收数量，日期不参与）
  4. 对比分类：✅ 已检验 / ⚠️ 未检验 / 📋 额外检验 / 🆔 名称不一致
  5. Excel 模板与结果导出

支持的检验清单字段（2026-08-21 用户提供）：
  是否加急检验、单据编号、数据状态、质检日期、供应商、采购员、批号、质检员、
  物料编码、物料名称、规格型号、单位、检验数量、合格数、不合格数、检验结果、
  异常描述、备注、类别、不良原因分析、解决措施、进度跟踪、不合格处理单号、
  检验工时、二次合格率、归属部件、归属产品、二次异常描述、检验方法、用途

设计要点：
  - 逐批次严格对比：每条送检记录需找到五字段完全一致的检验记录（质检日期 ≥ 收料日期）
  - 检验数量必须 = 实收数量才视为匹配
  - 检验清单不入库，仅用于本次临时对比；非核心列自动忽略
  - 名称不一致单独成类，人工判断
"""
import re
from datetime import date, datetime, timedelta
from io import BytesIO

import pandas as pd
import streamlit as st

from modules import supabase_helper

SUBMISSION_COLS = ['供应商', '物料编码', '规格型号', '物料名称', '收料日期', '实收数量']
INSPECTION_COLS = ['供应商', '物料编码', '规格型号', '物料名称', '质检日期', '检验数量']

COLUMN_ALIASES = {
    '供应商': ['供应商', 'supplier', '供应商名称', 'vendor'],
    '物料编码': ['物料编码', '编码', '料号', '物料代码', '物料号', 'material_code', 'code'],
    '规格型号': ['规格型号', '规格', '型号', 'spec', 'specification'],
    '物料名称': ['物料名称', '名称', '品名', '物料名', 'material', 'name'],
    '收料日期': ['收料日期', '收货日期', '收料时间', '到货日期', '来料日期'],
    '实收数量': ['实收数量', '实收数', '实收量', '实收', '数量'],
    '质检日期': ['质检日期', '检验日期', '检验时间', '质检时间', '检验完成日期', '质检完成日期'],
    '检验数量': ['检验数量', '检验数', '检验量', '检验'],
}

DATE_FORMATS = ['%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M',
                '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y%m%d', '%Y年%m月%d日']


# ==================== 基础清洗 ====================

def _clean_text(v):
    if v is None:
        return ''
    try:
        if pd.isna(v):
            return ''
    except Exception:
        pass
    return str(v).strip()


def _normalize_header(c):
    """
    列名规范化：去掉首尾空格、尾部星号、尾部的括号说明（如 "检验结果*"、"类别（下拉框）"）。
    用于兼容 ERP 导出里带星号或括号注释的表头。
    """
    s = _clean_text(c)
    s = re.sub(r'\*+\s*$', '', s)
    s = re.sub(r'[（(].*?[）)]\s*$', '', s).strip()
    return s


def _normalize_code(v):
    """物料编码规范化：数值去除 .0，字符串去空格"""
    if v is None:
        return ''
    try:
        if pd.isna(v):
            return ''
    except Exception:
        pass
    if isinstance(v, bool):
        return ''
    if isinstance(v, (int, float)):
        if float(v).is_integer():
            return str(int(v))
        return str(v)
    s = str(v).strip()
    if re.fullmatch(r'\d+\.0', s):
        return s[:-2]
    return s


def _parse_qty(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '').replace('，', '').replace(' ', '')
    m = re.sub(r'[^\d.+-]', '', s)
    if m in ('', '.', '-', '+'):
        return None
    try:
        return float(m)
    except Exception:
        return None


def _parse_date(v, default=None):
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, (int, float)):
        try:
            return (datetime(1899, 12, 30) + timedelta(days=float(v))).date()
        except Exception:
            return default
    s = str(v).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    s2 = re.split(r'[\sT]', s)[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s2, fmt).date()
        except Exception:
            continue
    return default


def _fmt_date(v):
    d = _parse_date(v)
    return d.isoformat() if d else ''


def _fmt_qty(v):
    v = _parse_qty(v)
    if v is None:
        return ''
    if v == int(v):
        return str(int(v))
    return str(v)


# ==================== Excel 解析 ====================

def _find_header_row(df):
    """在前 15 行中寻找包含物料编码/物料名称等关键列名的表头行"""
    for idx in range(min(15, len(df))):
        row = df.iloc[idx].astype(str)
        hits = 0
        for col in row:
            c = _normalize_header(col).lower()
            if any(c == a.lower() for a in COLUMN_ALIASES['物料编码']):
                hits += 1
            elif any(c == a.lower() for a in COLUMN_ALIASES['物料名称']):
                hits += 1
            elif any(c == a.lower() for a in COLUMN_ALIASES['供应商']):
                hits += 1
        if hits >= 2:
            return idx
    return None


def _map_columns(df, kind):
    target = SUBMISSION_COLS if kind == 'submission' else INSPECTION_COLS
    remap = {}
    used = set()
    df_cols = [_normalize_header(c) for c in df.columns]
    for std in target:
        for i, col in enumerate(df_cols):
            if i in used:
                continue
            if any(a.lower() == col.lower() for a in COLUMN_ALIASES[std]):
                remap[df.columns[i]] = std
                used.add(i)
                break
    missing = [c for c in target if c not in remap.values()]
    return df.rename(columns=remap), missing


def parse_sheet(uploaded_file, kind='submission', default_date=None):
    """
    解析上传的 Excel，返回标准化后的 DataFrame（6 标准列）。

    收料日期为空时（kind='submission'）填充 default_date（调用方传当天日期）。
    """
    df = pd.read_excel(uploaded_file, header=None)
    header_row = _find_header_row(df)
    if header_row is None:
        raise ValueError('未找到表头行，请确保包含「物料编码」「物料名称」等列名')
    df.columns = df.iloc[header_row].astype(str).str.strip()
    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df = df.dropna(how='all')
    df, missing = _map_columns(df, kind)
    if missing:
        raise ValueError(f'缺少必要列: {", ".join(missing)}（请使用标准列名或下载模板）')

    df['供应商'] = df['供应商'].map(_clean_text)
    df['物料编码'] = df['物料编码'].map(_normalize_code)
    df['规格型号'] = df['规格型号'].map(_clean_text)
    df['物料名称'] = df['物料名称'].map(_clean_text)
    if kind == 'submission':
        df['收料日期'] = df['收料日期'].map(lambda v: _parse_date(v, default=default_date))
        df['实收数量'] = df['实收数量'].map(_parse_qty)
    else:
        df['质检日期'] = df['质检日期'].map(lambda v: _parse_date(v, default=None))
        df['检验数量'] = df['检验数量'].map(_parse_qty)

    df = df[df['物料编码'] != ''].reset_index(drop=True)
    return df[SUBMISSION_COLS if kind == 'submission' else INSPECTION_COLS]


# ==================== 幂等去重入库 ====================

def _dedup_key(row):
    return (row['供应商'], row['物料编码'], row['规格型号'], row['物料名称'], row['实收数量'])


def _existing_keys(records):
    keys = set()
    for r in records:
        keys.add((
            _clean_text(r.get('supplier')),
            _normalize_code(r.get('material_code')),
            _clean_text(r.get('spec')),
            _clean_text(r.get('material_name')),
            _parse_qty(r.get('received_qty')),
        ))
    return keys


def preview_import(df, records=None):
    """
    返回 (新增记录 df, 重复记录 df)。去重键为五元组，不含日期。

    records 为空时走轻量查询（只拉取五元组键列），避免全量 SELECT *。
    """
    if records is None:
        records = supabase_helper.fetch_submission_keys()
    keys = _existing_keys(records)
    new_rows, dup_rows = [], []
    for _, row in df.iterrows():
        k = _dedup_key(row)
        (new_rows if k not in keys else dup_rows).append(row)
    cols = list(df.columns)
    return pd.DataFrame(new_rows, columns=cols), pd.DataFrame(dup_rows, columns=cols)


def import_submissions(df):
    """
    幂等入库：收料日期为空→当天；五元组重复的跳过。
    返回 (插入数, 重复数, 新增行数)
    """
    df = df.copy()
    df['收料日期'] = df['收料日期'].map(lambda v: v or date.today())
    new_df, dup_df = preview_import(df)
    rows = []
    for _, row in new_df.iterrows():
        d = row['收料日期']
        rows.append({
            'supplier': row['供应商'],
            'material_code': row['物料编码'],
            'spec': row['规格型号'],
            'material_name': row['物料名称'],
            'received_date': d.isoformat() if isinstance(d, (date, datetime)) else None,
            'received_qty': row['实收数量'],
        })
    inserted = supabase_helper.insert_inspection_submissions(rows) if rows else 0
    return inserted, len(dup_df), len(new_df)


def submissions_to_df(records):
    """数据库记录 → 展示用 DataFrame（6 标准列 + 入库时间 + id）"""
    rows = [{
        '供应商': _clean_text(r.get('supplier')),
        '物料编码': _normalize_code(r.get('material_code')),
        '规格型号': _clean_text(r.get('spec')),
        '物料名称': _clean_text(r.get('material_name')),
        '收料日期': _fmt_date(r.get('received_date')),
        '实收数量': _parse_qty(r.get('received_qty')),
        '入库时间': str(r.get('created_at', ''))[:19],
        'id': r.get('id'),
    } for r in records]
    return pd.DataFrame(rows, columns=['供应商', '物料编码', '规格型号', '物料名称',
                                       '收料日期', '实收数量', '入库时间', 'id'])


# ==================== 对比 ====================

def _qty_eq(a, b):
    a, b = _parse_qty(a), _parse_qty(b)
    if a is None or b is None:
        return False
    return abs(a - b) < 1e-6


def _date_ge(ins_date, sub_date):
    """质检日期 >= 收料日期；任一为空视为通过"""
    if ins_date is None or sub_date is None:
        return True
    d1 = _parse_date(ins_date)
    d2 = _parse_date(sub_date)
    if d1 is None or d2 is None:
        return True
    return d1 >= d2


def _sub_dict(row):
    return {c: row[c] for c in SUBMISSION_COLS}


def _ins_dict(row):
    return {c: row[c] for c in INSPECTION_COLS}


def compare(sub_df, ins_df):
    """
    核心对比。逐批次严格匹配，返回四分类结果 dict：
      checked           ✅ 已检验（附匹配的检验记录摘要）
      unchecked         ⚠️ 未检验
      extra             📋 额外检验（送检清单中不存在的检验记录）
      name_mismatch     🆔 名称不一致（同编码但物料名称不同，需人工判断）
      summary           各分类计数
    """
    sub_df = sub_df.reset_index(drop=True)
    ins_df = ins_df.reset_index(drop=True)

    ins_by_code = {}
    for i, row in ins_df.iterrows():
        ins_by_code.setdefault(row['物料编码'], []).append((i, row))

    checked, unchecked = [], []
    name_mismatch_sub, name_mismatch_ins = [], []
    extra = []
    matched_ins = set()  # 已被送检匹配 / 归入名称不一致的检验行索引

    for _, srow in sub_df.iterrows():
        cands = ins_by_code.get(srow['物料编码'], [])
        if not cands:
            unchecked.append(_sub_dict(srow))
            continue

        # 名称一致的候选
        same_name = [(i, c) for i, c in cands
                     if _clean_text(c['物料名称']) == srow['物料名称']]
        if not same_name:
            # 同编码候选名称全部不一致 → 第四类，人工判断
            name_mismatch_sub.append(_sub_dict(srow))
            for i, c in cands:
                name_mismatch_ins.append({**_ins_dict(c), '对应送检编码': srow['物料编码']})
                matched_ins.add(i)
            continue

        # 逐字段完全匹配
        matched = None
        for i, c in same_name:
            if (c['供应商'] == srow['供应商']
                    and c['规格型号'] == srow['规格型号']
                    and _qty_eq(c['检验数量'], srow['实收数量'])
                    and _date_ge(c['质检日期'], srow['收料日期'])):
                matched = (i, c)
                break
        if matched:
            i, c = matched
            d = _sub_dict(srow)
            d['匹配检验记录'] = (f"{c['供应商']} · {c['规格型号']} · "
                              f"检验数量 {_fmt_qty(c['检验数量'])} · "
                              f"质检日期 {_fmt_date(c['质检日期'])}")
            checked.append(d)
            matched_ins.add(i)
        else:
            unchecked.append(_sub_dict(srow))

    # 额外检验：未被任何送检匹配的检验记录
    sub_codes = set(sub_df['物料编码'])
    for i, irow in ins_df.iterrows():
        if i in matched_ins:
            continue
        note = ''
        if irow['物料编码'] in sub_codes:
            note = '该编码存在于送检清单，但未匹配成功（请核对名称/数量/日期/供应商）'
        extra.append({**_ins_dict(irow), '备注': note})

    checked_df = pd.DataFrame(checked, columns=SUBMISSION_COLS + ['匹配检验记录'])
    unchecked_df = pd.DataFrame(unchecked, columns=SUBMISSION_COLS)
    extra_df = pd.DataFrame(extra, columns=INSPECTION_COLS + ['备注'])
    mismatch_sub_df = pd.DataFrame(name_mismatch_sub, columns=SUBMISSION_COLS)
    mismatch_ins_df = pd.DataFrame(name_mismatch_ins, columns=INSPECTION_COLS + ['对应送检编码'])

    summary = {
        'total_sub': len(sub_df),
        'checked': len(checked),
        'unchecked': len(unchecked),
        'name_mismatch': len(name_mismatch_sub),
        'extra': len(extra),
    }
    return {
        'checked': checked_df,
        'unchecked': unchecked_df,
        'extra': extra_df,
        'name_mismatch': {'sub': mismatch_sub_df, 'ins': mismatch_ins_df},
        'summary': summary,
    }


# ==================== 导出 ====================

def _to_xlsx_bytes(df, sheet_name='结果', red_rows=None):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        if red_rows:
            from openpyxl.styles import PatternFill
            fill = PatternFill(start_color='FFF4C4C4', end_color='FFF4C4C4', fill_type='solid')
            ws = writer.sheets[sheet_name]
            for r in red_rows:
                for c in range(1, len(df.columns) + 1):
                    ws.cell(row=r + 2, column=c).fill = fill
    return buf.getvalue()


def download_template(kind='submission'):
    """下载填表模板（含一行示例）"""
    cols = SUBMISSION_COLS if kind == 'submission' else INSPECTION_COLS
    example = {
        'submission': ['供应商A', 'M1001', '10x20x30', '示例物料', '2026-08-21', 1000],
        'inspection': ['供应商A', 'M1001', '10x20x30', '示例物料', '2026-08-21', 1000],
    }
    rows = [dict(zip(cols, example[kind])), {c: '' for c in cols}]
    return _to_xlsx_bytes(pd.DataFrame(rows, columns=cols), '模板')


def export_submissions(records):
    """导出当前用户的全部送检记录"""
    df = submissions_to_df(records).drop(columns=['id'])
    return _to_xlsx_bytes(df, '送检记录')


def export_unchecked(result):
    """导出未检验清单（整表红底高亮）"""
    df = result['unchecked']
    return _to_xlsx_bytes(df, '未检验', red_rows=list(range(len(df))))


def export_all(result):
    """导出全部对比结果（多 sheet，未检验 sheet 红底高亮）"""
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        result['checked'].to_excel(writer, index=False, sheet_name='已检验')
        result['unchecked'].to_excel(writer, index=False, sheet_name='未检验')
        result['extra'].to_excel(writer, index=False, sheet_name='额外检验')
        result['name_mismatch']['sub'].to_excel(writer, index=False, sheet_name='名称不一致-送检')
        result['name_mismatch']['ins'].to_excel(writer, index=False, sheet_name='名称不一致-检验')
        from openpyxl.styles import PatternFill
        fill = PatternFill(start_color='FFF4C4C4', end_color='FFF4C4C4', fill_type='solid')
        ws = writer.sheets['未检验']
        for r in range(2, len(result['unchecked']) + 2):
            for c in range(1, len(result['unchecked'].columns) + 1):
                ws.cell(row=r, column=c).fill = fill
    return buf.getvalue()
