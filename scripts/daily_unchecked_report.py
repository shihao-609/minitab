# -*- coding: utf-8 -*-
"""
每日未检验清单自动邮件脚本（独立于 Streamlit 运行）

功能：
  1. 用 service_role 密钥读取全部送检记录 / 检验记录（绕过 RLS）
  2. 复用 inspection_match 比对逻辑，计算「今日未检验清单」
  3. 回算「昨日未检验清单」（送检入库时间 ≤ 昨日 且 质检日期 ≤ 昨日的检验记录），
     对比出新增 / 已解决 / 持续三类变动 —— 无需任何额外存储
  4. 无未检验记录时直接退出（不发邮件）
  5. 生成 Excel（未检验清单 + 变动摘要 两个 sheet），SMTP 发送给配置的收件人列表

运行环境变量：
  SUPABASE_URL                Supabase 项目地址
  SUPABASE_SERVICE_ROLE_KEY   服务端密钥（用于绕过 RLS 读全量数据）
  SMTP_HOST                   SMTP 服务器（默认 smtp.qq.com）
  SMTP_PORT                   SMTP 端口（默认 465 / SSL）
  SMTP_USER                   发件邮箱
  SMTP_PASS                   发件邮箱授权码（非登录密码）
  REPORT_RECIPIENTS           收件人邮箱，逗号分隔，可多个
  REPORT_FROM_NAME            发件人显示名（可选，默认"质量管理系统"）
"""
import os
import sys
import smtplib
from datetime import date, datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

import pandas as pd
from supabase import create_client

# 让脚本可以 import 项目根目录下的 modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import inspection_match  # noqa: E402


def _get_env(name: str, default: str = '') -> str:
    return os.environ.get(name, default) or default


def _load_all_rows(table: str):
    """用 service_role 读取指定表全部数据（绕过 RLS，含所有用户）"""
    url = _get_env('SUPABASE_URL')
    key = _get_env('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key:
        raise RuntimeError('缺少 SUPABASE_URL 或 SUPABASE_SERVICE_ROLE_KEY 环境变量')
    client = create_client(url, key)
    res = client.table(table).select('*').limit(100000).execute()
    return res.data or []


def _parse_dt(s):
    """解析 ISO 格式时间（如 '2026-08-21T09:00:00+00:00'），失败返回 None"""
    if not s:
        return None
    try:
        return pd.to_datetime(s)
    except Exception:
        return None


def _today_yesterday():
    today = date.today()
    return today, today - timedelta(days=1)


def _identity_keys(rows):
    """按五元组生成每条记录的身份键（与入库唯一索引一致），支持 DataFrame 或空列表"""
    if rows is None or len(rows) == 0:
        return []
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    return (df['供应商'].astype(str) + '|' + df['物料编码'].astype(str) + '|'
            + df['规格型号'].astype(str) + '|' + df['物料名称'].astype(str) + '|'
            + df['实收数量'].astype(str)).tolist()


def build_report():
    today, yesterday = _today_yesterday()
    print(f'[任务] 运行日期: {today}，回算基准日: {yesterday}')

    # 1. 拉取全量数据
    sub_records = _load_all_rows('inspection_submissions')
    ins_records = _load_all_rows('inspection_records')
    print(f'[数据] 送检记录 {len(sub_records)} 条，检验记录 {len(ins_records)} 条')
    if not sub_records:
        print('[跳过] 数据库中无送检记录，不发邮件')
        return None

    sub_df = inspection_match.submissions_to_df(sub_records)
    ins_df = inspection_match.inspection_records_to_df(ins_records)

    # 2. 今日未检验清单（当前全量数据）
    today_res = inspection_match.compare(sub_df, ins_df)
    today_unchecked = today_res['unchecked']
    print(f'[比对] 今日未检验 {len(today_unchecked)} 条，已检验 {len(today_res["checked"])} 条，'
          f'额外检验 {len(today_res["extra"])} 条，名称不一致 {len(today_res["name_mismatch"]["sub"])} 条')

    # 3. 回算昨日未检验清单
    sub_df['_created_dt'] = sub_df['入库时间'].map(_parse_dt)
    ins_df['_inspect_dt'] = pd.to_datetime(ins_df['质检日期'], errors='coerce').dt.date
    yesterday_end = datetime.combine(yesterday, datetime.max.time())

    sub_yesterday = sub_df[sub_df['_created_dt'] <= yesterday_end].drop(columns=['_created_dt'])
    ins_yesterday = ins_df[ins_df['_inspect_dt'] <= yesterday].drop(columns=['_inspect_dt'])
    print(f'[回算] 昨日截止送检 {len(sub_yesterday)} 条，昨日截止检验 {len(ins_yesterday)} 条')

    if len(sub_yesterday) > 0:
        yesterday_res = inspection_match.compare(sub_yesterday, ins_yesterday)
        yesterday_unchecked = yesterday_res['unchecked']
    else:
        yesterday_unchecked = []
    print(f'[回算] 昨日未检验 {len(yesterday_unchecked)} 条')

    # 4. 对比变动
    today_keys = set(_identity_keys(today_unchecked))
    yesterday_keys = set(_identity_keys(yesterday_unchecked))
    added = today_keys - yesterday_keys
    solved = yesterday_keys - today_keys
    ongoing = today_keys & yesterday_keys

    unchecked_out = today_unchecked.copy()
    if len(unchecked_out) > 0:
        keys = _identity_keys(unchecked_out)
        unchecked_out.insert(0, '状态', [
            '🆕 新增' if k in added else '持续未检验' for k in keys
        ])

    summary_rows = [
        ('回算基准日', str(yesterday)),
        ('昨日未检验', len(yesterday_keys)),
        ('昨日未检验 → 今日已解决（说明昨日检验已完成）', len(solved)),
        ('持续未检验（昨日至今仍未检验）', len(ongoing)),
        ('今日新增未检验', len(added)),
        ('今日未检验合计', len(today_keys)),
    ]

    if len(today_keys) == 0:
        print('[跳过] 今日无未检验记录，不发邮件')
        return None

    # 5. 生成 Excel
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        unchecked_out.to_excel(writer, index=False, sheet_name='未检验清单')
        pd.DataFrame(summary_rows, columns=['指标', '数值']).to_excel(
            writer, index=False, sheet_name='变动摘要')

        # 表头加粗 + 红底高亮未检验清单
        from openpyxl.styles import Font, PatternFill
        wb = writer.book
        for ws_name, n_rows in [('未检验清单', len(unchecked_out))]:
            ws = wb[ws_name]
            for cell in ws[1]:
                cell.font = Font(bold=True)
            if n_rows > 0:
                red = PatternFill(start_color='FFC7CE', end_color='FFC7CE', fill_type='solid')
                for row in ws.iter_rows(min_row=2, max_row=n_rows + 1, min_col=1,
                                        max_col=ws.max_column):
                    for cell in row:
                        cell.fill = red

    filename = f'未检验清单_{today.isoformat()}.xlsx'
    print(f'[输出] 生成 {filename}，未检验 {len(today_keys)} 条')

    # 6. 发送邮件
    recipients = [x.strip() for x in _get_env('REPORT_RECIPIENTS').split(',') if x.strip()]
    if not recipients:
        raise RuntimeError('未配置 REPORT_RECIPIENTS 收件人列表')
    smtp_user = _get_env('SMTP_USER')
    smtp_pass = _get_env('SMTP_PASS')
    smtp_host = _get_env('SMTP_HOST', 'smtp.qq.com')
    smtp_port = int(_get_env('SMTP_PORT', '465'))
    from_name = _get_env('REPORT_FROM_NAME', '质量管理系统')

    msg = MIMEMultipart()
    msg['From'] = f'{from_name} <{smtp_user}>'
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = f'【未检验清单】{today.isoformat()} 共 {len(today_keys)} 条'
    msg.attach(MIMEText('详见附件。', 'plain', 'utf-8'))

    part = MIMEApplication(buf.getvalue(), _subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    part.add_header('Content-Disposition', 'attachment', filename=('utf-8', '', filename))
    msg.attach(part)

    print(f'[邮件] 连接 {smtp_host}:{smtp_port} ...')
    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())
    print(f'[邮件] 已发送给 {len(recipients)} 个收件人: {", ".join(recipients)}')

    return {'filename': filename, 'unchecked_count': len(today_keys)}


def main():
    try:
        result = build_report()
        if result:
            print(f'[完成] {result["filename"]} 已发送')
        else:
            print('[完成] 无需发送（未检验清单为空或没有送检记录）')
    except Exception as e:
        print(f'[失败] {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
