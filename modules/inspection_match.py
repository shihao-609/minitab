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
from modules.supplier_normalize import normalize_supplier, same_supplier

SUBMISSION_COLS = ['供应商', '物料编码', '规格型号', '物料名称', '收料日期', '实收数量']
INSPECTION_COLS = ['供应商', '物料编码', '规格型号', '物料名称', '质检日期', '检验数量']

# 检验清单入库时保留的扩展字段（非必填，缺失时置空）
EXTRA_INS_COLS = {
    '单据编号': ['单据编号', '检验单号', '单号', '检验报告编号'],
    '合格数': ['合格数', '合格数量', '合格'],
    '不合格数': ['不合格数', '不合格数量', '不合格', '不良数'],
    '检验结果': ['检验结果', '检验结论', '判定', '结果'],
    '质检员': ['质检员', '检验员', '检验人', '质检人'],
    '批号': ['批号', '批次', '批号/序号'],
    '类别': ['类别', '检验类别', '检验方式', '分类'],
}

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

    收料日期（kind='submission'）按 Excel 原样保存：为空则保持为空，不再自动填充。
    参数 default_date 仅作向后兼容保留，已不使用。
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
    # 过滤 Excel 底部的「合计」汇总行（供应商列含"合计"，如 ERP 导出常见的合计行）
    df = df[~df['供应商'].str.contains('合计', na=False)].reset_index(drop=True)
    df['物料编码'] = df['物料编码'].map(_normalize_code)
    df['规格型号'] = df['规格型号'].map(_clean_text)
    df['物料名称'] = df['物料名称'].map(_clean_text)
    if kind == 'submission':
        df['收料日期'] = df['收料日期'].map(lambda v: _parse_date(v, default=None))
        df['实收数量'] = df['实收数量'].map(_parse_qty)
    else:
        df['质检日期'] = df['质检日期'].map(lambda v: _parse_date(v, default=None))
        df['检验数量'] = df['检验数量'].map(_parse_qty)

    df = df[df['物料编码'] != ''].reset_index(drop=True)
    return df[SUBMISSION_COLS if kind == 'submission' else INSPECTION_COLS]


# ==================== 幂等去重入库 ====================

def _dedup_key(row):
    return (row.get('检验类型', '来料检'), row['供应商'], row['物料编码'],
            row['规格型号'], row['物料名称'], row['实收数量'])


def _existing_keys(records):
    keys = set()
    for r in records:
        keys.add((
            _clean_text(r.get('inspect_type')) or '来料检',
            _clean_text(r.get('supplier')),
            _normalize_code(r.get('material_code')),
            _clean_text(r.get('spec')),
            _clean_text(r.get('material_name')),
            _parse_qty(r.get('received_qty')),
        ))
    return keys


def preview_import(df, records=None, inspect_type='来料检'):
    """
    返回 (新增记录 df, 重复记录 df)。去重键为 检验类型+五元组，不含日期。

    records 为空时走轻量查询（只拉取键列），避免全量 SELECT *。
    """
    if records is None:
        records = supabase_helper.fetch_submission_keys()
    keys = _existing_keys(records)

    df = df.copy()
    df['检验类型'] = inspect_type

    key_series = df.apply(_dedup_key, axis=1)
    is_new = ~key_series.isin(keys)
    return df[is_new].reset_index(drop=True), df[~is_new].reset_index(drop=True)


def import_submissions(df, progress=None, batch_size=1000, inspect_type='来料检'):
    """
    幂等入库：收料日期保持 Excel 原样（为空则入库为空）；类型+五元组重复的跳过。
    返回 (插入数, 重复数, 新增行数)

    progress: 可选回调 progress(done, total)
    """
    df = df.copy()

    # 直接插入全部数据，由数据库 unique index 完成去重，避免再次查 keys
    total = len(df)
    rows = []
    for _, row in df.iterrows():
        d = row['收料日期']
        rows.append({
            'supplier': row['供应商'],
            'material_code': row['物料编码'],
            'spec': row['规格型号'],
            'material_name': row['物料名称'],
            'received_date': d.isoformat() if isinstance(d, (date, datetime)) else None,
            'received_qty': row['实收数量'],
            'inspect_type': inspect_type,
        })

    inserted = 0
    if rows:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            inserted += supabase_helper.insert_inspection_submissions(batch)
            if progress:
                progress(min(i + len(batch), total), total)

    skipped = total - inserted
    return inserted, skipped, total


def submissions_to_df(records):
    """数据库记录 → 展示用 DataFrame（检验类型 + 6 标准列 + 入库时间 + id）"""
    rows = [{
        '检验类型': _clean_text(r.get('inspect_type')) or '来料检',
        '供应商': _clean_text(r.get('supplier')),
        '物料编码': _normalize_code(r.get('material_code')),
        '规格型号': _clean_text(r.get('spec')),
        '物料名称': _clean_text(r.get('material_name')),
        '收料日期': _fmt_date(r.get('received_date')),
        '实收数量': _parse_qty(r.get('received_qty')),
        '入库时间': str(r.get('created_at', ''))[:19],
        'id': r.get('id'),
    } for r in records]
    return pd.DataFrame(rows, columns=['检验类型', '供应商', '物料编码', '规格型号', '物料名称',
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
    d = {c: row[c] for c in SUBMISSION_COLS}
    d['检验类型'] = row.get('检验类型', '来料检')
    return d


def _ins_dict(row):
    d = {c: row[c] for c in INSPECTION_COLS}
    d['检验类型'] = row.get('检验类型', '来料检')
    return d


def _diff_fields(sub, ins, use_norm_supplier=True):
    """严格核对送检记录与检验记录的字段，返回差异描述（空串表示完全一致）。

    重点核对：供应商 / 规格型号 / 物料名称 / 实收数量 vs 检验数量。
    use_norm_supplier=True 时供应商用归一化比较（自动识别同一公司的不同写法，
    如"常州塑邦" vs "塑邦模型"）；False 时按原始名称精确比较。
    """
    diffs = []
    if use_norm_supplier:
        if not same_supplier(sub['供应商'], ins['供应商']):
            diffs.append(f"供应商「{sub['供应商'] or '空'}」≠「{ins['供应商'] or '空'}」")
    else:
        if sub['供应商'] != ins['供应商']:
            diffs.append(f"供应商「{sub['供应商'] or '空'}」≠「{ins['供应商'] or '空'}」")
    if sub['规格型号'] != ins['规格型号']:
        diffs.append(f"规格「{sub['规格型号'] or '空'}」≠「{ins['规格型号'] or '空'}」")
    if sub['物料名称'] != ins['物料名称']:
        diffs.append(f"名称「{sub['物料名称'] or '空'}」≠「{ins['物料名称'] or '空'}」")
    if not _qty_eq(sub['实收数量'], ins['检验数量']):
        diffs.append(f"实收数量 {_fmt_qty(sub['实收数量']) or '空'} ≠ 检验数量 {_fmt_qty(ins['检验数量']) or '空'}")
    return '；'.join(diffs)


def compare(sub_df, ins_df, progress=None, normalize_suppliers=True, inspect_type=None):
    """
    核心对比。逐批次严格匹配，返回四分类结果 dict：
      checked           ✅ 已检验（附匹配的检验记录摘要）
      unchecked         ⚠️ 未检验
      extra             📋 额外检验（送检清单中不存在的检验记录）
      name_mismatch     🆔 名称不一致（同编码但物料名称不同，需人工判断）
      summary           各分类计数

    normalize_suppliers: 开启后自动识别同一供应商的不同写法（默认开启）。
    inspect_type: 指定检验类型（如 '来料检'）时只比对该类型的送检/检验记录，
                  避免跨工序互相匹配；None 表示全量比对。
    progress: 可选回调 progress(done, total)
    """
    sub_df = sub_df.reset_index(drop=True)
    ins_df = ins_df.reset_index(drop=True)

    if inspect_type:
        if '检验类型' in sub_df.columns:
            sub_df = sub_df[sub_df['检验类型'] == inspect_type].reset_index(drop=True)
        if '检验类型' in ins_df.columns:
            ins_df = ins_df[ins_df['检验类型'] == inspect_type].reset_index(drop=True)

    def _norm_sup(name):
        return normalize_supplier(name) if normalize_suppliers else _clean_text(name)

    # 预处理：to_dict 批量转换（比 iterrows 快约 3 倍），日期/数量统一解析
    subs = []
    for r in sub_df.to_dict('records'):
        sup = _clean_text(r['供应商'])
        subs.append({
            '检验类型': _clean_text(r.get('检验类型', '来料检')) or '来料检',
            '供应商': sup,
            '_sup_norm': _norm_sup(sup),
            '物料编码': _normalize_code(r['物料编码']),
            '规格型号': _clean_text(r['规格型号']),
            '物料名称': _clean_text(r['物料名称']),
            '收料日期': _parse_date(r['收料日期']),
            '实收数量': _parse_qty(r['实收数量']),
        })

    ins_rows = []
    for r in ins_df.to_dict('records'):
        sup = _clean_text(r['供应商'])
        ins_rows.append({
            '检验类型': _clean_text(r.get('检验类型', '来料检')) or '来料检',
            '供应商': sup,
            '_sup_norm': _norm_sup(sup),
            '物料编码': _normalize_code(r['物料编码']),
            '规格型号': _clean_text(r['规格型号']),
            '物料名称': _clean_text(r['物料名称']),
            '质检日期': _parse_date(r['质检日期']),
            '检验数量': _parse_qty(r['检验数量']),
        })

    # 建立多级索引（供应商键使用归一化名）
    ins_by_code = {}
    ins_by_full_key = {}
    for idx, c in enumerate(ins_rows):
        code = c['物料编码']
        ins_by_code.setdefault(code, []).append((idx, c))
        key = (c['_sup_norm'], code, c['规格型号'], c['物料名称'], c['检验数量'])
        ins_by_full_key.setdefault(key, []).append((idx, c))

    checked, unchecked = [], []
    name_mismatch_sub, name_mismatch_ins = [], []
    matched_ins = set()

    total_sub = len(subs)
    report_every = max(1, total_sub // 20)  # 每 5% 进度报告一次，避免频繁重绘

    for s_idx, srow in enumerate(subs):
        code = srow['物料编码']
        cands_code = ins_by_code.get(code, [])

        if not cands_code:
            unchecked.append(_sub_dict(srow))
            continue

        # 先尝试全键命中（O(1)）
        full_key = (srow['_sup_norm'], code, srow['规格型号'], srow['物料名称'], srow['实收数量'])
        candidates = ins_by_full_key.get(full_key, [])
        matched_idx = None
        matched_ins_row = None
        for idx, c in candidates:
            if idx in matched_ins:
                continue
            if _date_ge(c['质检日期'], srow['收料日期']):
                matched_idx = idx
                matched_ins_row = c
                break

        if matched_idx is not None:
            d = _sub_dict(srow)
            _mr = matched_ins_row or {}
            d['匹配检验记录'] = (f"{_mr['供应商']} · {_mr['规格型号']} · "
                              f"检验数量 {_fmt_qty(_mr['检验数量'])} · "
                              f"质检日期 {_fmt_date(_mr['质检日期'])}")
            checked.append(d)
            matched_ins.add(matched_idx)
        else:
            # 是否有同编码且名称一致的候选？
            same_name = any(c['物料名称'] == srow['物料名称']
                            for _, c in cands_code)
            if not same_name:
                # 同编码但物料名称完全对不上 → 第四类
                name_mismatch_sub.append(_sub_dict(srow))
                for idx, c in cands_code:
                    if idx not in matched_ins:
                        name_mismatch_ins.append({**_ins_dict(c), '对应送检编码': code})
                        matched_ins.add(idx)
            else:
                # 同编码同名称但全键未命中 → 严格核对：
                # 1) 优先寻找归一化后完全一致的候选（供应商不同写法也算一致）→ 转为已检验
                # 2) 否则记录差异原因（重点：数量 / 供应商）
                d = _sub_dict(srow)
                reasons = []
                matched_idx = None
                matched_ins_row = None
                for idx, c in cands_code:
                    if idx in matched_ins:
                        continue
                    diff = _diff_fields(srow, c, use_norm_supplier=normalize_suppliers)
                    if not diff and _date_ge(c['质检日期'], srow['收料日期']):
                        matched_idx = idx
                        matched_ins_row = c
                        break
                    if diff:
                        reasons.append(diff)
                    else:
                        reasons.append(f"质检日期 {_fmt_date(c['质检日期']) or '空'} 早于收料日期 {_fmt_date(srow['收料日期']) or '空'}")
                if matched_idx is not None:
                    _mr = matched_ins_row or {}
                    d['匹配检验记录'] = (f"{_mr['供应商']} · {_mr['规格型号']} · "
                                      f"检验数量 {_fmt_qty(_mr['检验数量'])} · "
                                      f"质检日期 {_fmt_date(_mr['质检日期'])}")
                    checked.append(d)
                    matched_ins.add(matched_idx)
                else:
                    if reasons:
                        d['未匹配原因'] = '；'.join(reasons[:2])
                    unchecked.append(d)

        if progress and (s_idx + 1) % report_every == 0:
            progress(s_idx + 1, total_sub)

    if progress:
        progress(total_sub, total_sub)

    # 额外检验：未被任何送检匹配的检验记录
    sub_codes = {s['物料编码'] for s in subs}
    # 统计检验记录的关键字段出现次数，用于识别疑似重复检验单（供应商键使用归一化名）
    dup_counts = {}
    for c in ins_rows:
        _key = (c['_sup_norm'], c['物料编码'], c['规格型号'], c['物料名称'], c['质检日期'], c['检验数量'])
        dup_counts[_key] = dup_counts.get(_key, 0) + 1
    extra = []
    for idx, irow in enumerate(ins_rows):
        if idx in matched_ins:
            continue
        note = ''
        if irow['物料编码'] in sub_codes:
            # 找出同编码的送检记录，尽量给出具体差异（重点：数量）
            diffs = [_diff_fields(s, irow, use_norm_supplier=normalize_suppliers)
                     for s in subs if s['物料编码'] == irow['物料编码']]
            diffs = [d for d in diffs if d]
            if diffs:
                note = f"送检清单同编码记录存在差异：{'；'.join(diffs[:2])}"
            else:
                note = '该编码存在于送检清单，但未匹配成功（请核对名称/数量/日期/供应商）'
        # 疑似重复检验单提示（不去重，仅提示人工确认）
        dup_key = (irow['_sup_norm'], irow['物料编码'], irow['规格型号'], irow['物料名称'], irow['质检日期'], irow['检验数量'])
        if dup_counts.get(dup_key, 0) > 1:
            dup_tip = ('该记录与另一条检验记录完全相同（同供应商/编码/规格型号/名称/质检日期/数量），'
                       '疑似重复检验单，请人工确认')
            note = f"{note}；{dup_tip}" if note else dup_tip
        extra.append({**_ins_dict(irow), '备注': note})

    checked_df = pd.DataFrame(checked, columns=SUBMISSION_COLS + ['检验类型', '匹配检验记录'])
    unchecked_df = pd.DataFrame(unchecked, columns=SUBMISSION_COLS + ['检验类型', '未匹配原因'])
    extra_df = pd.DataFrame(extra, columns=INSPECTION_COLS + ['检验类型', '备注'])
    mismatch_sub_df = pd.DataFrame(name_mismatch_sub, columns=SUBMISSION_COLS + ['检验类型'])
    mismatch_ins_df = pd.DataFrame(name_mismatch_ins, columns=INSPECTION_COLS + ['检验类型', '对应送检编码'])

    summary = {
        'total_sub': total_sub,
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


# ==================== 检验记录入库（方案四：持久化 + 跨窗口对账） ====================

def parse_inspection_full(uploaded_file):
    """
    解析检验清单 Excel，保留入库所需完整字段。
    返回 DataFrame（6 核心列 + 扩展列：单据编号/合格数/不合格数/检验结果/质检员/批号/类别）。
    """
    df = pd.read_excel(uploaded_file, header=None)
    header_row = _find_header_row(df)
    if header_row is None:
        raise ValueError('未找到表头行，请确保包含「物料编码」「物料名称」等列名')
    df.columns = df.iloc[header_row].astype(str).str.strip()
    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df = df.dropna(how='all')

    df, missing = _map_columns(df, 'inspection')
    if missing:
        raise ValueError(f'缺少必要列: {", ".join(missing)}（请使用标准列名或下载模板）')

    df['供应商'] = df['供应商'].map(_clean_text)
    # 过滤 Excel 底部的「合计」汇总行
    df = df[~df['供应商'].str.contains('合计', na=False)].reset_index(drop=True)
    df['物料编码'] = df['物料编码'].map(_normalize_code)
    df['规格型号'] = df['规格型号'].map(_clean_text)
    df['物料名称'] = df['物料名称'].map(_clean_text)
    df['质检日期'] = df['质检日期'].map(lambda v: _parse_date(v, default=None))
    df['检验数量'] = df['检验数量'].map(_parse_qty)
    df = df[df['物料编码'] != ''].reset_index(drop=True)

    # 提取扩展列（按别名匹配，缺失时置空）
    df_cols = [_normalize_header(c) for c in df.columns]
    for std, aliases in EXTRA_INS_COLS.items():
        if std in df.columns:
            continue
        for i, col in enumerate(df_cols):
            if any(a.lower() == col.lower() for a in aliases):
                df[std] = df[df.columns[i]]
                break
        else:
            df[std] = ''

    for std in ('合格数', '不合格数'):
        if std in df.columns:
            df[std] = df[std].map(_parse_qty)
    for std in ('检验结果', '质检员', '批号', '类别', '单据编号'):
        if std in df.columns:
            df[std] = df[std].map(_clean_text)

    return df


def import_inspection_records(df, progress=None, batch_size=1000, source_file='',
                              inspect_type='来料检'):
    """
    幂等入库：检验记录持久化（类型+单据编号+供应商+编码+规格+名称+质检日期+数量 去重）。
    返回 (插入数, 跳过数, 总行数)
    """
    df = df.copy()
    total = len(df)
    rows = []
    for _, row in df.iterrows():
        d = row['质检日期']
        rows.append({
            'doc_no': row.get('单据编号', '') or '',
            'supplier': row['供应商'],
            'material_code': row['物料编码'],
            'spec': row['规格型号'],
            'material_name': row['物料名称'],
            'inspect_date': d.isoformat() if isinstance(d, (date, datetime)) else None,
            'inspect_qty': row['检验数量'],
            'qualified_qty': row.get('合格数'),
            'unqualified_qty': row.get('不合格数'),
            'result': row.get('检验结果', '') or '',
            'inspector': row.get('质检员', '') or '',
            'batch_no': row.get('批号', '') or '',
            'category': row.get('类别', '') or '',
            'inspect_type': inspect_type,
            'source_file': source_file,
        })

    inserted = 0
    if rows:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            inserted += supabase_helper.insert_inspection_records(batch)
            if progress:
                progress(min(i + len(batch), total), total)

    skipped = total - inserted
    return inserted, skipped, total


def inspection_records_to_df(records):
    """数据库检验记录 → 展示/比对用 DataFrame（检验类型 + 6 标准列 + 扩展列 + 入库时间 + id）"""
    rows = [{
        '检验类型': _clean_text(r.get('inspect_type')) or '来料检',
        '供应商': _clean_text(r.get('supplier')),
        '物料编码': _normalize_code(r.get('material_code')),
        '规格型号': _clean_text(r.get('spec')),
        '物料名称': _clean_text(r.get('material_name')),
        '质检日期': _fmt_date(r.get('inspect_date')),
        '检验数量': _parse_qty(r.get('inspect_qty')),
        '单据编号': _clean_text(r.get('doc_no')),
        '合格数': _parse_qty(r.get('qualified_qty')),
        '不合格数': _parse_qty(r.get('unqualified_qty')),
        '检验结果': _clean_text(r.get('result')),
        '质检员': _clean_text(r.get('inspector')),
        '批号': _clean_text(r.get('batch_no')),
        '类别': _clean_text(r.get('category')),
        '入库时间': str(r.get('created_at', ''))[:19],
        'id': r.get('id'),
    } for r in records]
    cols = ['检验类型', '供应商', '物料编码', '规格型号', '物料名称', '质检日期', '检验数量',
            '单据编号', '合格数', '不合格数', '检验结果', '质检员', '批号', '类别', '入库时间', 'id']
    return pd.DataFrame(rows, columns=cols)
