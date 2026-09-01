"""
Supabase 数据库操作辅助模块 (v2 — 支持用户隔离)
=================================================
提供数据集的 CRUD 操作，实现质量数据的持久化存储。

v2 关键变更（应对"增加账号密码"的影响）：
  1. 所有写操作自动注入 user_id，插入时携带当前用户身份
  2. 读取操作优先使用用户 JWT 客户端，Supabase RLS 策略自动过滤数据
  3. 兼容旧版 anon key 客户端（非认证模式，用于注册/登录阶段）

v3: 添加数据库冷启动重试机制（解决 Supabase 免费版暂停后唤醒慢的问题）
"""

import os
import time
import math
from datetime import datetime
from supabase import create_client, Client
import pandas as pd
import numpy as np
import json
import streamlit as st
from typing import Optional
from functools import wraps

# ==================== 重试机制 ====================

RETRY_MAX = 3           # 最大重试次数
RETRY_DELAY = 3         # 每次重试间隔（秒），Supabase 冷启动通常需要 2-5 秒
RETRY_DELAY_BACKOFF = 2  # 退避倍数（3s → 6s → 12s）


def _with_retry(func):
    """
    数据库操作重试装饰器
    当 Supabase 免费版项目处于暂停状态时，首次请求会触发冷启动，
    需要等待 2-5 秒后重试。此装饰器自动处理该场景。
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_error = None
        delay = RETRY_DELAY
        for attempt in range(RETRY_MAX):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                msg = str(e).lower()
                # 判断是否是需要重试的错误类型
                retryable = any(kw in msg for kw in [
                    "connection", "timeout", "timed out",
                    "failed to connect", "refused",
                    "server terminated", "unexpected",
                    "network", "reset", "broken pipe",
                    "could not connect", "sslerror",
                ])
                if not retryable or attempt >= RETRY_MAX - 1:
                    raise last_error
                st.warning(f"⏳ 数据库正在唤醒中，{delay}秒后重试...（第{attempt + 1}次）")
                time.sleep(delay)
                delay *= RETRY_DELAY_BACKOFF
        raise last_error
    return wrapper


# ==================== 客户端初始化 ====================

def _get_supabase_url() -> str:
    try:
        return st.secrets.get("SUPABASE_URL", "") or os.environ.get("SUPABASE_URL", "")
    except Exception:
        return os.environ.get("SUPABASE_URL", "")


def _get_supabase_key() -> str:
    try:
        return st.secrets.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
    except Exception:
        return os.environ.get("SUPABASE_ANON_KEY", "")


# 最近一次获取客户端失败的具体原因（供页面诊断显示）
_last_client_error: str = ""


def _get_user_id() -> Optional[str]:
    """从 session 获取当前用户 ID"""
    if st.session_state.get("authenticated") and st.session_state.get("user"):
        return st.session_state.user.id
    return None


def _get_client() -> Optional[Client]:
    """
    获取 Supabase 客户端（自动选择认证模式）

    优先级：
      1. 已登录 → 使用携带用户 JWT 的认证客户端，确保 RLS 策略能识别当前用户
      2. 未登录 → 使用 anon key（匿名访问，RLS 策略下只能读公开数据）

    关键点：
      - 认证客户端直接通过 auth.get_authenticated_client() 获取，它会在 token 即将过期时
        用 refresh_token 自动刷新，并返回携带有效 JWT 的客户端。
      - 已登录但无法获取认证客户端时（session 失效），返回 None，让上层提示重新登录，
        而不是静默降级为匿名客户端导致 RLS 过滤全部数据。

    Supabase RLS 通过 JWT 中的 auth.uid() 识别用户身份，确保数据隔离。
    """
    global _last_client_error
    url = _get_supabase_url()
    key = _get_supabase_key()
    if not url or not key:
        _last_client_error = "SUPABASE_URL / SUPABASE_ANON_KEY 未配置（检查 .env 或 Streamlit Secrets）"
        return None

    # 已登录：优先使用携带 JWT 的认证客户端
    if st.session_state.get("authenticated") and st.session_state.get("session"):
        try:
            from modules import auth
            client = auth.get_authenticated_client()
            if client is not None:
                _last_client_error = ""
                return client
            # 已登录但 session 失效，不能降级为匿名，否则 RLS 会隐藏所有数据
            auth_err = st.session_state.get("auth_error")
            if auth_err:
                _last_client_error = f"登录会话异常：{auth_err}"
            else:
                _last_client_error = "已登录但无法获取认证客户端（会话可能已失效，请退出重新登录）"
            return None
        except Exception as e:
            _last_client_error = f"获取认证客户端出错：{e}"
            return None

    # 未登录：使用 anon key 匿名客户端
    _last_client_error = ""
    return create_client(url, key)


def _check_client(client: Optional[Client]):
    """检查客户端是否可用，不可用时抛出异常（带具体失败原因）"""
    if client is None:
        raise ValueError(_last_client_error or "SUPABASE_URL 或 SUPABASE_ANON_KEY 未设置，请检查 .env 文件或 Streamlit Secrets")


def _sanitize_value(v):
    """把无法 JSON 序列化的值清洗为 Python 原生类型或 None"""
    if v is None:
        return None
    # numpy 标量 / NaN / Inf
    if isinstance(v, (np.floating, np.float64, np.float32)):
        if np.isnan(v) or np.isinf(v):
            return None
        return float(v)
    if isinstance(v, (np.integer, np.int64, np.int32)):
        return int(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    # Python float 的 NaN / Inf
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    # pandas Timestamp / NaT
    if isinstance(v, pd.Timestamp):
        if pd.isna(v):
            return None
        return v.isoformat()
    # datetime / date
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _sanitize_rows(rows: list, uid: str) -> list:
    """给数据注入 user_id 并清洗不可 JSON 序列化的值"""
    cleaned = []
    for row in rows:
        new_row = {'user_id': uid}
        for k, v in row.items():
            new_row[k] = _sanitize_value(v)
        cleaned.append(new_row)
    return cleaned


# ==================== 数据集 CRUD ====================

@_with_retry
def _do_save_dataset(client, data):
    result = client.table("datasets").insert(data).execute()
    return result.data[0] if result.data else None


def save_dataset(name: str, df: pd.DataFrame, columns_info: dict = None) -> Optional[dict]:
    """
    将 DataFrame 保存到 Supabase 数据集表

    v3 变更：
      - 添加重试机制，处理 Supabase 免费版冷启动
    """
    try:
        client = _get_client()
        _check_client(client)
        data = {
            "name": name,
            "data": json.loads(df.to_json(orient="records", force_ascii=False)),
            "columns_info": columns_info or list(df.columns),
            "row_count": len(df),
        }
        uid = _get_user_id()
        if uid:
            data["user_id"] = uid

        return _do_save_dataset(client, data)
    except Exception as e:
        st.error(f"保存数据集失败: {e}")
        return None


@_with_retry
def _do_load_dataset(client, dataset_id):
    return client.table("datasets").select("*").eq("id", dataset_id).execute()


def load_dataset(dataset_id: str) -> Optional[pd.DataFrame]:
    """
    从 Supabase 加载指定的数据集

    v3 变更：添加重试机制，处理 Supabase 免费版冷启动
    """
    try:
        client = _get_client()
        _check_client(client)
        result = _do_load_dataset(client, dataset_id)
        if result.data:
            record = result.data[0]
            rid = record.get("user_id")
            uid = _get_user_id()
            if uid and rid and rid != uid:
                st.error("无权访问此数据集")
                return None
            df = pd.DataFrame(record["data"])
            return df
        return None
    except Exception as e:
        st.error(f"加载数据集失败: {e}")
        return None


@_with_retry
def _do_list_datasets(client):
    return client.table("datasets").select("*").order("created_at", desc=True).execute()


def list_datasets() -> list:
    """
    列出当前用户的所有数据集

    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        result = _do_list_datasets(client)

        uid = _get_user_id()
        if uid and result.data:
            result.data = [r for r in result.data if r.get("user_id") == uid]

        return result.data
    except Exception as e:
        st.error(f"获取数据集列表失败: {e}")
        return []


@_with_retry
def _do_delete_dataset(client, dataset_id):
    return client.table("datasets").delete().eq("id", dataset_id).execute()


def delete_dataset(dataset_id: str) -> bool:
    """
    删除数据集

    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        _do_delete_dataset(client, dataset_id)
        return True
    except Exception as e:
        st.error(f"删除数据集失败: {e}")
        return False


# ==================== 鱼骨图配置 CRUD ====================

@_with_retry
def _do_save_fishbone(client, data):
    result = client.table("fishbone_configs").insert(data).execute()
    return result.data[0] if result.data else None


def save_fishbone(name: str, problem: str, raw_input: str) -> Optional[dict]:
    """
    将鱼骨图配置保存到独立的 fishbone_configs 表

    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        data = {
            "name": name,
            "problem": problem,
            "raw_input": raw_input,
        }
        uid = _get_user_id()
        if uid:
            data["user_id"] = uid

        return _do_save_fishbone(client, data)
    except Exception as e:
        st.error(f"保存鱼骨图配置失败: {e}")
        return None


@_with_retry
def _do_list_fishbone(client):
    return client.table("fishbone_configs").select("*").order("created_at", desc=True).execute()


def list_fishbone_configs() -> list:
    """
    列出当前用户的所有鱼骨图配置

    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        result = _do_list_fishbone(client)

        uid = _get_user_id()
        if uid and result.data:
            result.data = [r for r in result.data if r.get("user_id") == uid]

        return result.data
    except Exception as e:
        st.error(f"获取鱼骨图配置列表失败: {e}")
        return []


@_with_retry
def _do_delete_fishbone(client, config_id):
    return client.table("fishbone_configs").delete().eq("id", config_id).execute()


def delete_fishbone_config(config_id: str) -> bool:
    """
    删除指定的鱼骨图配置
    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        _do_delete_fishbone(client, config_id)
        return True
    except Exception as e:
        st.error(f"删除鱼骨图配置失败: {e}")
        return False


# ==================== 分析报告 CRUD ====================

def ensure_reports_table() -> bool:
    """
    自动创建 analysis_reports 表（如果不存在）。
    使用 Supabase REST API 的 rpc 或直接尝试插入来判断。

    注意：Supabase REST API 不支持 DDL，此函数通过
    尝试查询来检测表是否存在，不存在时提示用户执行 SQL。
    """
    try:
        client = _get_client()
        _check_client(client)
        # 尝试查询一行，看表是否存在
        client.table("analysis_reports").select("id").limit(1).execute()
        return True
    except Exception:
        # 表不存在，尝试创建
        try:
            client = _get_client()
            _check_client(client)
            # 使用 REST API 无法直接创建表，返回 False 让调用方提示
            pass
        except Exception:
            pass
        return False


def get_create_reports_table_sql() -> str:
    """返回创建 analysis_reports 表的 SQL 语句（含 RLS 策略）"""
    return """
-- 1. 创建表
CREATE TABLE IF NOT EXISTS analysis_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id),
    name TEXT NOT NULL,
    report_md TEXT,
    analyses_summary JSONB DEFAULT '[]'::jsonb,
    files_data JSONB DEFAULT '[]'::jsonb,
    file_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. 启用 RLS
ALTER TABLE analysis_reports ENABLE ROW LEVEL SECURITY;

-- 3. 删除旧策略（避免重复创建报错）
DROP POLICY IF EXISTS "Users can view own reports" ON analysis_reports;
DROP POLICY IF EXISTS "Users can insert own reports" ON analysis_reports;
DROP POLICY IF EXISTS "Users can update own reports" ON analysis_reports;
DROP POLICY IF EXISTS "Users can delete own reports" ON analysis_reports;

-- 4. 创建 RLS 策略：用户只能访问自己的报告
CREATE POLICY "Users can view own reports"
    ON analysis_reports FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own reports"
    ON analysis_reports FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own reports"
    ON analysis_reports FOR UPDATE
    USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own reports"
    ON analysis_reports FOR DELETE
    USING (auth.uid() = user_id);

-- 5. 创建索引
CREATE INDEX IF NOT EXISTS idx_reports_user_id ON analysis_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_reports_created_at ON analysis_reports(created_at DESC);
"""


@_with_retry
def _do_save_report(client, data):
    result = client.table("analysis_reports").insert(data).execute()
    return result.data[0] if result.data else None


def save_report(name: str, report_md: str, analyses_summary: list,
                files_data: list, file_count: int = 0) -> Optional[dict]:
    """
    保存分析报告到 Supabase

    v3 变更：添加重试机制，处理冷启动
    """
    try:
        client = _get_client()
        _check_client(client)

        uid = _get_user_id()
        if not uid:
            st.error("保存报告失败: 未获取到用户 ID，请确保已登录。")
            return None

        data = {
            "name": name,
            "report_md": report_md,
            "analyses_summary": json.loads(json.dumps(analyses_summary, ensure_ascii=False, default=str)),
            "files_data": json.loads(json.dumps(files_data, ensure_ascii=False)),
            "file_count": file_count,
            "user_id": uid,
        }

        return _do_save_report(client, data)
    except Exception as e:
        err_msg = str(e)
        if "row-level security" in err_msg.lower() or "42501" in err_msg:
            st.error("保存报告失败: 数据库安全策略阻止了写入。请在 Supabase SQL Editor 中执行以下 SQL 修复：")
            with st.expander("📋 修复 SQL（点击展开）"):
                st.code(get_create_reports_table_sql(), language='sql')
        else:
            st.error(f"保存报告失败: {e}")
        return None


@_with_retry
def _do_list_reports(client):
    return client.table("analysis_reports").select(
        "id,name,file_count,analyses_summary,created_at,user_id"
    ).order("created_at", desc=True).execute()


def list_reports() -> list:
    """
    列出当前用户的所有分析报告（按时间倒序）

    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        result = _do_list_reports(client)

        uid = _get_user_id()
        if uid and result.data:
            result.data = [r for r in result.data if r.get("user_id") == uid]

        return result.data
    except Exception as e:
        st.error(f"获取报告列表失败: {e}")
        return []


@_with_retry
def _do_load_report(client, report_id):
    return client.table("analysis_reports").select("*").eq("id", report_id).execute()


@_with_retry
def _do_delete_report(client, report_id):
    return client.table("analysis_reports").delete().eq("id", report_id).execute()


def load_report(report_id: str) -> Optional[dict]:
    """
    加载完整的分析报告（包含 report_md 和 files_data）

    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        result = _do_load_report(client, report_id)
        if result.data:
            record = result.data[0]
            rid = record.get("user_id")
            uid = _get_user_id()
            if uid and rid and rid != uid:
                st.error("无权访问此报告")
                return None
            return record
        return None
    except Exception as e:
        st.error(f"加载报告失败: {e}")
        return None


def delete_report(report_id: str) -> bool:
    """
    删除指定的分析报告

    v3 变更：添加重试机制
    """
    try:
        client = _get_client()
        _check_client(client)
        _do_delete_report(client, report_id)
        return True
    except Exception as e:
        st.error(f"删除报告失败: {e}")
        return False


# ==================== 送检清单 CRUD ====================

# 记录最近一次数据库结构检测失败的原因，供页面展示
_last_db_check_error: str = ""


def get_last_db_check_error() -> str:
    """返回最近一次数据库结构检测失败的原因（用于页面诊断提示）"""
    return _last_db_check_error


def ensure_inspection_table() -> bool:
    """检测 inspection_submissions 表是否存在"""
    global _last_db_check_error
    try:
        client = _get_client()
        _check_client(client)
        client.table("inspection_submissions").select("id").limit(1).execute()
        _last_db_check_error = ""
        return True
    except Exception as e:
        _last_db_check_error = str(e)
        return False


def get_create_inspection_table_sql() -> str:
    """返回创建 inspection_submissions 表的 SQL（含 RLS + 通用 dedup_key 唯一索引）"""
    return """
-- 1. 创建表（送检清单持久化）
CREATE TABLE IF NOT EXISTS inspection_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id),
    supplier TEXT NOT NULL DEFAULT '',
    material_code TEXT NOT NULL,
    spec TEXT DEFAULT '',
    material_name TEXT DEFAULT '',
    received_date DATE,
    received_qty NUMERIC,
    purchaser TEXT DEFAULT '',
    inspect_type TEXT NOT NULL DEFAULT '来料检',
    dedup_key TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. 启用 RLS
ALTER TABLE inspection_submissions ENABLE ROW LEVEL SECURITY;

-- 3. 删除旧策略（避免重复执行报错；兼容升级前的账号隔离策略）
DROP POLICY IF EXISTS "Users can view own inspections" ON inspection_submissions;
DROP POLICY IF EXISTS "Users can insert own inspections" ON inspection_submissions;
DROP POLICY IF EXISTS "Users can delete own inspections" ON inspection_submissions;
DROP POLICY IF EXISTS "Users can view all inspections" ON inspection_submissions;
DROP POLICY IF EXISTS "Users can delete all inspections" ON inspection_submissions;

-- 4. 创建 RLS 策略：团队共享（多个检验员共享同一份送检/检验数据）
--    SELECT/DELETE：所有登录用户可见、可删除全部
--    INSERT：仍写入当前用户 user_id，仅允许插入自己的记录（保留上传人追溯）
CREATE POLICY "Users can view all inspections"
    ON inspection_submissions FOR SELECT
    USING (auth.uid() IS NOT NULL);

CREATE POLICY "Users can insert own inspections"
    ON inspection_submissions FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete all inspections"
    ON inspection_submissions FOR DELETE
    USING (auth.uid() IS NOT NULL);

-- 5. 唯一索引：跨账号全局去重（dedup_key 由应用按工序配置字段计算，
--    多人上传同一记录时自动跳过；部分索引排除空去重键的异常行）
CREATE UNIQUE INDEX IF NOT EXISTS idx_inspection_dedup
    ON inspection_submissions(inspect_type, dedup_key) WHERE dedup_key <> '';

-- 6. 常用查询索引
CREATE INDEX IF NOT EXISTS idx_inspection_user_date
    ON inspection_submissions(user_id, received_date);

-- 7. 批量入库函数（RPC）：服务端 ON CONFLICT DO NOTHING 原子去重，
--    单次请求完成全部写入，重复记录自动跳过，无需客户端逐条重试。
CREATE OR REPLACE FUNCTION bulk_insert_inspections(p_rows jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    inserted integer := 0;
    r jsonb;
BEGIN
    -- 老库自愈：确保 inspect_type / dedup_key / purchaser 列存在（无动态 SQL，安全）
    ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS inspect_type TEXT NOT NULL DEFAULT '来料检';
    ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS dedup_key TEXT NOT NULL DEFAULT '';
    ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS purchaser TEXT DEFAULT '';
    -- 老库自愈：确保去重索引为跨账号全局（账号隔离索引存在则重建；索引缺失则补建）
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_submissions'
          AND indexname = 'idx_inspection_dedup'
          AND indexdef LIKE '%(user_id, inspect_type, dedup_key)%'
    ) THEN
        -- 账号隔离索引存在：先清理跨账号重复（保留最早一条），再重建为全局
        DELETE FROM inspection_submissions
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY inspect_type, dedup_key ORDER BY created_at, id
                ) AS rn
                FROM inspection_submissions WHERE dedup_key <> ''
            ) t WHERE rn > 1
        );
        DROP INDEX idx_inspection_dedup;
        CREATE UNIQUE INDEX idx_inspection_dedup
            ON inspection_submissions(inspect_type, dedup_key) WHERE dedup_key <> '';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_submissions'
          AND indexname = 'idx_inspection_dedup'
          AND indexdef LIKE '%ON inspection_submissions(inspect_type, dedup_key)%'
    ) THEN
        -- 索引缺失（异常旧库）：直接补建全局去重索引，防止去重失效
        CREATE UNIQUE INDEX IF NOT EXISTS idx_inspection_dedup
            ON inspection_submissions(inspect_type, dedup_key) WHERE dedup_key <> '';
    END IF;

    FOR r IN SELECT * FROM jsonb_array_elements(p_rows) LOOP
        INSERT INTO inspection_submissions
            (user_id, supplier, material_code, spec, material_name, received_date, received_qty, purchaser, inspect_type, dedup_key)
        VALUES
            (auth.uid(),
             COALESCE(r->>'supplier', ''),
             COALESCE(r->>'material_code', ''),
             COALESCE(r->>'spec', ''),
             COALESCE(r->>'material_name', ''),
             NULLIF(r->>'received_date', '')::date,
             (r->>'received_qty')::numeric,
             COALESCE(r->>'purchaser', ''),
             COALESCE(NULLIF(r->>'inspect_type', ''), '来料检'),
             COALESCE(NULLIF(r->>'dedup_key', ''), md5(
                 COALESCE(r->>'supplier','')||'|'||COALESCE(r->>'material_code','')||'|'||COALESCE(r->>'spec','')||'|'||
                 COALESCE(r->>'material_name','')||'|'||
                 CASE WHEN r->>'received_qty' IS NULL OR r->>'received_qty' = '' THEN ''
                      WHEN (r->>'received_qty')::numeric = floor((r->>'received_qty')::numeric)
                           THEN to_char((r->>'received_qty')::numeric, 'FM99999999999999999999')
                      ELSE (r->>'received_qty')::numeric::text END)))
        ON CONFLICT DO NOTHING;
        IF FOUND THEN
            inserted := inserted + 1;
        END IF;
    END LOOP;
    RETURN inserted;
END;
$$;

-- 8. 仅允许已登录用户调用
REVOKE ALL ON FUNCTION bulk_insert_inspections(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bulk_insert_inspections(jsonb) TO authenticated;
"""


@_with_retry
def _do_list_inspections(client, limit=None, inspect_type=None):
    q = client.table("inspection_submissions").select("*").order(
        "received_date", desc=True).order("created_at", desc=True)
    if inspect_type:
        q = q.eq("inspect_type", inspect_type)
    if limit:
        q = q.limit(limit)
    return q.execute()


def list_inspection_submissions(limit: int = None, inspect_type: str = None) -> list:
    """
    列出团队共享的送检记录（按收料日期倒序），用于管理页展示。
    limit 用于控制单次传输量（例如仅展示最近 2000 条）。
    inspect_type 传入工序名则只返回该工序的记录（None 返回全部，兼容旧调用）。
    """
    try:
        client = _get_client()
        _check_client(client)
        result = _do_list_inspections(client, limit, inspect_type)

        return result.data
    except Exception as e:
        st.error(f"获取送检记录失败: {e}")
        return []


@_with_retry
def _do_fetch_submission_keys(client):
    """只拉取去重所需的列（含 dedup_key），用于导入预览去重"""
    return client.table("inspection_submissions").select(
        "inspect_type,supplier,material_code,spec,material_name,received_qty,dedup_key"
    ).execute()


def fetch_submission_keys() -> list:
    """轻量查询：拉取全部送检记录的去重键（含 dedup_key），避免 SELECT * 大 payload。"""
    try:
        client = _get_client()
        _check_client(client)
        result = _do_fetch_submission_keys(client)
        return result.data
    except Exception as e:
        st.error(f"获取送检去重键失败: {e}")
        return []


@_with_retry
def _do_fetch_submission_records(client):
    """只拉取对比所需的业务列（不含 id / created_at）"""
    return client.table("inspection_submissions").select(
        "inspect_type,supplier,material_code,spec,material_name,received_date,received_qty,purchaser"
    ).order("received_date", desc=True).order("created_at", desc=True).execute()


def fetch_submission_records() -> list:
    """轻量查询：全部累计送检记录（6 业务列 + 采购员），用于检验对比。"""
    try:
        client = _get_client()
        _check_client(client)
        try:
            result = _do_fetch_submission_records(client)
        except Exception:
            # 老库尚无 purchaser 列 → 回退不带该列查询（采购员显示为空，迁移后自动恢复）
            result = client.table("inspection_submissions").select(
                "inspect_type,supplier,material_code,spec,material_name,received_date,received_qty"
            ).order("received_date", desc=True).order("created_at", desc=True).execute()
        return result.data
    except Exception as e:
        st.error(f"获取送检记录失败: {e}")
        return []


@_with_retry
def _do_count_inspections(client, inspect_type=None):
    q = client.table("inspection_submissions").select("id", count="exact")
    if inspect_type:
        q = q.eq("inspect_type", inspect_type)
    return q.execute()


def count_inspection_submissions(inspect_type: str = None) -> int:
    """获取当前用户送检记录总数（inspect_type 传入工序名则只统计该工序）"""
    try:
        client = _get_client()
        _check_client(client)
        result = _do_count_inspections(client, inspect_type)
        return int(result.count or 0)
    except Exception:
        return 0


def ensure_inspection_rpc() -> bool:
    """检测 bulk_insert_inspections 函数是否已创建（空数组调用无副作用）"""
    global _last_db_check_error
    try:
        client = _get_client()
        _check_client(client)
        client.rpc("bulk_insert_inspections", {"p_rows": []}).execute()
        _last_db_check_error = ""
        return True
    except Exception as e:
        _last_db_check_error = str(e)
        return False


@_with_retry
def _do_insert_inspections(client, rows):
    return client.table("inspection_submissions").insert(rows).execute()


def insert_inspection_submissions(rows: list) -> int:
    """
    批量插入送检记录（幂等），返回实际插入条数。

    优先使用 RPC 原子入库（bulk_insert_inspections，单请求 ON CONFLICT DO NOTHING）；
    函数未创建时自动回退为普通批量插入 + 逐条跳过冲突。
    """
    if not rows:
        return 0
    try:
        client = _get_client()
        _check_client(client)
        uid = _get_user_id()
        if not uid:
            st.error("插入送检记录失败: 未获取到用户 ID，请重新登录。")
            return 0

        payload = _sanitize_rows(rows, uid)

        # 优先 RPC：单次请求完成全部写入（含去重），性能最优
        try:
            data = client.rpc("bulk_insert_inspections", {"p_rows": payload}).execute().data
            try:
                return int(data)
            except (TypeError, ValueError):
                return len(payload)
        except Exception as e:
            msg = str(e).lower()
            # RPC 函数未创建 → 回退普通插入
            if any(k in msg for k in (
                    "could not find the function", "does not exist",
                    "pgrst202", "bulk_insert_inspections")):
                pass
            else:
                raise

        # 回退：普通批量插入，撞唯一索引则逐条跳过冲突
        try:
            _do_insert_inspections(client, payload)
            return len(payload)
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("duplicate", "unique", "23505", "conflict")):
                inserted = 0
                for r in payload:
                    try:
                        client.table("inspection_submissions").insert(r).execute()
                        inserted += 1
                    except Exception:
                        pass
                return inserted
            raise
    except Exception as e:
        st.error(f"插入送检记录失败: {e}")
        return 0


@_with_retry
def _do_clear_inspections(client, inspect_type=None):
    q = client.table("inspection_submissions").delete()
    if inspect_type:
        q = q.eq("inspect_type", inspect_type)
    else:
        q = q.neq("inspect_type", "")  # PostgREST 要求 DELETE 必须带过滤条件
    return q.execute()


def clear_inspection_submissions(inspect_type: str = None) -> bool:
    """清空团队共享的送检记录（inspect_type 传入工序名则只清空该工序）"""
    try:
        client = _get_client()
        _check_client(client)
        _do_clear_inspections(client, inspect_type)
        return True
    except Exception as e:
        st.error(f"清空送检记录失败: {e}")
        return False


# ==================== 检验记录 CRUD（持久化入库） ====================

def get_create_inspection_records_table_sql() -> str:
    """返回创建检验记录表 + 供应商别名表 + 批量入库 RPC 的 SQL"""
    return """
-- ============ 检验记录表（持久化，跨窗口自动对账） ============
CREATE TABLE IF NOT EXISTS inspection_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id),
    doc_no TEXT DEFAULT '',
    supplier TEXT NOT NULL DEFAULT '',
    material_code TEXT NOT NULL,
    spec TEXT DEFAULT '',
    material_name TEXT DEFAULT '',
    inspect_date DATE,
    inspect_qty NUMERIC,
    qualified_qty NUMERIC,
    unqualified_qty NUMERIC,
    result TEXT DEFAULT '',
    inspector TEXT DEFAULT '',
    batch_no TEXT DEFAULT '',
    category TEXT DEFAULT '',
    purchaser TEXT DEFAULT '',
    inspect_type TEXT NOT NULL DEFAULT '来料检',
    dedup_key TEXT NOT NULL DEFAULT '',
    source_file TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE inspection_records ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own ins_records" ON inspection_records;
DROP POLICY IF EXISTS "Users can insert own ins_records" ON inspection_records;
DROP POLICY IF EXISTS "Users can delete own ins_records" ON inspection_records;
DROP POLICY IF EXISTS "Users can view all ins_records" ON inspection_records;
DROP POLICY IF EXISTS "Users can delete all ins_records" ON inspection_records;

-- 团队共享：SELECT/DELETE 对所有登录用户开放；INSERT 仍记录上传人
CREATE POLICY "Users can view all ins_records"
    ON inspection_records FOR SELECT
    USING (auth.uid() IS NOT NULL);

CREATE POLICY "Users can insert own ins_records"
    ON inspection_records FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete all ins_records"
    ON inspection_records FOR DELETE
    USING (auth.uid() IS NOT NULL);

-- 去重索引：跨账号全局去重（dedup_key 由应用按工序配置字段计算，
--    多人上传同一记录时自动跳过；部分索引排除空去重键的异常行）
CREATE UNIQUE INDEX IF NOT EXISTS idx_ins_records_dedup
    ON inspection_records(inspect_type, dedup_key) WHERE dedup_key <> '';

CREATE INDEX IF NOT EXISTS idx_ins_records_user_date
    ON inspection_records(user_id, inspect_date);

-- 批量入库函数（RPC）：服务端原子去重
CREATE OR REPLACE FUNCTION bulk_insert_inspection_records(p_rows jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    inserted integer := 0;
    r jsonb;
BEGIN
    -- 老库自愈：确保 inspect_type / dedup_key / purchaser 列存在（无动态 SQL，安全）
    ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS inspect_type TEXT NOT NULL DEFAULT '来料检';
    ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS dedup_key TEXT NOT NULL DEFAULT '';
    ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS purchaser TEXT DEFAULT '';
    -- 老库自愈：确保去重索引为跨账号全局（账号隔离索引存在则重建；索引缺失则补建）
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_records'
          AND indexname = 'idx_ins_records_dedup'
          AND indexdef LIKE '%(user_id, inspect_type, dedup_key)%'
    ) THEN
        -- 账号隔离索引存在：先清理跨账号重复（保留最早一条），再重建为全局
        DELETE FROM inspection_records
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY inspect_type, dedup_key ORDER BY created_at, id
                ) AS rn
                FROM inspection_records WHERE dedup_key <> ''
            ) t WHERE rn > 1
        );
        DROP INDEX idx_ins_records_dedup;
        CREATE UNIQUE INDEX idx_ins_records_dedup
            ON inspection_records(inspect_type, dedup_key) WHERE dedup_key <> '';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_records'
          AND indexname = 'idx_ins_records_dedup'
          AND indexdef LIKE '%ON inspection_records(inspect_type, dedup_key)%'
    ) THEN
        -- 索引缺失（异常旧库）：直接补建全局去重索引，防止去重失效
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ins_records_dedup
            ON inspection_records(inspect_type, dedup_key) WHERE dedup_key <> '';
    END IF;

    FOR r IN SELECT * FROM jsonb_array_elements(p_rows) LOOP
        INSERT INTO inspection_records
            (user_id, doc_no, supplier, material_code, spec, material_name,
             inspect_date, inspect_qty, qualified_qty, unqualified_qty,
             result, inspector, batch_no, category, purchaser, inspect_type, source_file, dedup_key)
        VALUES
            (auth.uid(),
             COALESCE(r->>'doc_no', ''),
             COALESCE(r->>'supplier', ''),
             COALESCE(r->>'material_code', ''),
             COALESCE(r->>'spec', ''),
             COALESCE(r->>'material_name', ''),
             NULLIF(r->>'inspect_date', '')::date,
             NULLIF(r->>'inspect_qty', '')::numeric,
             NULLIF(r->>'qualified_qty', '')::numeric,
             NULLIF(r->>'unqualified_qty', '')::numeric,
             COALESCE(r->>'result', ''),
             COALESCE(r->>'inspector', ''),
             COALESCE(r->>'batch_no', ''),
             COALESCE(r->>'category', ''),
             COALESCE(r->>'purchaser', ''),
             COALESCE(NULLIF(r->>'inspect_type', ''), '来料检'),
             COALESCE(r->>'source_file', ''),
             COALESCE(NULLIF(r->>'dedup_key', ''), md5(
                 COALESCE(r->>'supplier','')||'|'||COALESCE(r->>'material_code','')||'|'||COALESCE(r->>'spec','')||'|'||
                 COALESCE(r->>'material_name','')||'|'||
                 CASE WHEN r->>'inspect_qty' IS NULL OR r->>'inspect_qty' = '' THEN ''
                      WHEN (r->>'inspect_qty')::numeric = floor((r->>'inspect_qty')::numeric)
                           THEN to_char((r->>'inspect_qty')::numeric, 'FM99999999999999999999')
                      ELSE (r->>'inspect_qty')::numeric::text END||'|'||
                 COALESCE(r->>'doc_no','')||'|'||COALESCE(NULLIF(r->>'inspect_date',''),''))))
        ON CONFLICT DO NOTHING;
        IF FOUND THEN
            inserted := inserted + 1;
        END IF;
    END LOOP;
    RETURN inserted;
END;
$$;

REVOKE ALL ON FUNCTION bulk_insert_inspection_records(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bulk_insert_inspection_records(jsonb) TO authenticated;

-- ============ 团队共享模式检测函数（应用通过 RPC 判断 RLS 是否已共享化） ============
CREATE OR REPLACE FUNCTION inspect_rls_mode()
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'inspection_submissions'
          AND policyname = 'Users can view all inspections'
    );
$$;

-- ============ 供应商别名表（团队共享的归一化配置） ============
CREATE TABLE IF NOT EXISTS supplier_aliases (
    alias TEXT PRIMARY KEY,
    canonical TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE supplier_aliases ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "All auth can view supplier_aliases" ON supplier_aliases;
DROP POLICY IF EXISTS "All auth can manage supplier_aliases" ON supplier_aliases;

CREATE POLICY "All auth can view supplier_aliases"
    ON supplier_aliases FOR SELECT TO authenticated USING (true);

CREATE POLICY "All auth can manage supplier_aliases"
    ON supplier_aliases FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- ============ 未检验清单邮件收件人表（团队共享，页面可维护） ============
-- recipient_type: 'to' 收件人 / 'cc' 抄送人
-- inspect_type: 适用的检验工序，'全部' = 所有工序都发；同一邮箱可为不同工序分别配置
CREATE TABLE IF NOT EXISTS report_recipients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    recipient_type TEXT NOT NULL DEFAULT 'to',
    inspect_type TEXT NOT NULL DEFAULT '全部',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (email, inspect_type)
);

ALTER TABLE report_recipients ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "All auth can view report_recipients" ON report_recipients;
DROP POLICY IF EXISTS "All auth can manage report_recipients" ON report_recipients;

CREATE POLICY "All auth can view report_recipients"
    ON report_recipients FOR SELECT TO authenticated USING (true);

CREATE POLICY "All auth can manage report_recipients"
    ON report_recipients FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- ============ 未检验清单快照表（持久化：每次比对结果覆盖上一次） ============
CREATE TABLE IF NOT EXISTS unchecked_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspect_type TEXT NOT NULL DEFAULT '来料检',
    supplier TEXT DEFAULT '',
    material_code TEXT DEFAULT '',
    spec TEXT DEFAULT '',
    material_name TEXT DEFAULT '',
    received_date DATE,
    received_qty NUMERIC,
    purchaser TEXT DEFAULT '',
    unmatched_reason TEXT DEFAULT '',
    saved_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE unchecked_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "All auth can view unchecked_snapshots" ON unchecked_snapshots;
DROP POLICY IF EXISTS "All auth can manage unchecked_snapshots" ON unchecked_snapshots;

CREATE POLICY "All auth can view unchecked_snapshots"
    ON unchecked_snapshots FOR SELECT TO authenticated USING (true);

CREATE POLICY "All auth can manage unchecked_snapshots"
    ON unchecked_snapshots FOR ALL TO authenticated USING (true) WITH CHECK (true);

-- ============ 邮件发送排程表（前端设置周几/几点发送） ============
-- send_days: 周几发送，1=周一 ... 5=周五，逗号分隔（如 '1,3,5'）
-- send_time: 发送时间（北京时间 HH:MM），如 '08:00'
-- last_sent_date: 最近一次成功发送的日期（北京时间 YYYY-MM-DD），用于防止重复发送
CREATE TABLE IF NOT EXISTS report_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    send_days TEXT NOT NULL DEFAULT '1,2,3,4,5',
    send_time TEXT NOT NULL DEFAULT '08:00',
    last_sent_date TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE report_schedule ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "All auth can view report_schedule" ON report_schedule;
DROP POLICY IF EXISTS "All auth can manage report_schedule" ON report_schedule;

CREATE POLICY "All auth can view report_schedule"
    ON report_schedule FOR SELECT TO authenticated USING (true);

CREATE POLICY "All auth can manage report_schedule"
    ON report_schedule FOR ALL TO authenticated USING (true) WITH CHECK (true);
"""


def get_inspect_type_migration_sql() -> str:
    """老表迁移 SQL：补充 inspect_type / dedup_key / purchaser 字段，回填去重键，
    重建通用去重索引并重建入库 RPC（幂等，可重复执行）"""
    return """
-- ============ 检验类型 + 通用去重键 + 采购员迁移（来料检/过程检/出货检…） ============
-- 1. 两张表补充 inspect_type 列（老数据默认「来料检」）
ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS inspect_type TEXT NOT NULL DEFAULT '来料检';
ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS inspect_type TEXT NOT NULL DEFAULT '来料检';

-- 2. 补充通用去重键 dedup_key 列（以后新增工序字段可不同，去重不再绑定固定列）
ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS dedup_key TEXT NOT NULL DEFAULT '';
ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS dedup_key TEXT NOT NULL DEFAULT '';

-- 2b. 补充采购员 purchaser 列（未检验清单展示用，不参与匹配/去重）
ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS purchaser TEXT DEFAULT '';
ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS purchaser TEXT DEFAULT '';

-- 3. 回填已有数据的去重键（算法与 Python 端 _fmt_qty 一致：整数去小数尾，非整数保留原样）
UPDATE inspection_submissions SET dedup_key = md5(
    COALESCE(supplier,'')||'|'||COALESCE(material_code,'')||'|'||COALESCE(spec,'')||'|'||
    COALESCE(material_name,'')||'|'||
    CASE WHEN received_qty IS NULL THEN ''
         WHEN received_qty = floor(received_qty) THEN to_char(received_qty, 'FM99999999999999999999')
         ELSE received_qty::text END)
WHERE dedup_key = '';
UPDATE inspection_records SET dedup_key = md5(
    COALESCE(supplier,'')||'|'||COALESCE(material_code,'')||'|'||COALESCE(spec,'')||'|'||
    COALESCE(material_name,'')||'|'||
    CASE WHEN inspect_qty IS NULL THEN ''
         WHEN inspect_qty = floor(inspect_qty) THEN to_char(inspect_qty, 'FM99999999999999999999')
         ELSE inspect_qty::text END||'|'||
    COALESCE(doc_no,'')||'|'||COALESCE(inspect_date::text,''))
WHERE dedup_key = '';

-- 4. 重建去重索引为跨账号全局去重（团队共享：多人上传同一记录自动跳过）
DROP INDEX IF EXISTS idx_inspection_dedup;
DROP INDEX IF EXISTS idx_ins_records_dedup;
CREATE UNIQUE INDEX IF NOT EXISTS idx_inspection_dedup
    ON inspection_submissions(inspect_type, dedup_key) WHERE dedup_key <> '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ins_records_dedup
    ON inspection_records(inspect_type, dedup_key) WHERE dedup_key <> '';

-- 5. 重建批量入库 RPC（携带 purchaser 字段；与完整建表 SQL 一致，幂等）
CREATE OR REPLACE FUNCTION bulk_insert_inspections(p_rows jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    inserted integer := 0;
    r jsonb;
BEGIN
    ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS inspect_type TEXT NOT NULL DEFAULT '来料检';
    ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS dedup_key TEXT NOT NULL DEFAULT '';
    ALTER TABLE inspection_submissions ADD COLUMN IF NOT EXISTS purchaser TEXT DEFAULT '';
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_submissions'
          AND indexname = 'idx_inspection_dedup'
          AND indexdef LIKE '%(user_id, inspect_type, dedup_key)%'
    ) THEN
        DELETE FROM inspection_submissions
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY inspect_type, dedup_key ORDER BY created_at, id
                ) AS rn
                FROM inspection_submissions WHERE dedup_key <> ''
            ) t WHERE rn > 1
        );
        DROP INDEX idx_inspection_dedup;
        CREATE UNIQUE INDEX idx_inspection_dedup
            ON inspection_submissions(inspect_type, dedup_key) WHERE dedup_key <> '';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_submissions'
          AND indexname = 'idx_inspection_dedup'
          AND indexdef LIKE '%ON inspection_submissions(inspect_type, dedup_key)%'
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS idx_inspection_dedup
            ON inspection_submissions(inspect_type, dedup_key) WHERE dedup_key <> '';
    END IF;

    FOR r IN SELECT * FROM jsonb_array_elements(p_rows) LOOP
        INSERT INTO inspection_submissions
            (user_id, supplier, material_code, spec, material_name, received_date, received_qty, purchaser, inspect_type, dedup_key)
        VALUES
            (auth.uid(),
             COALESCE(r->>'supplier', ''),
             COALESCE(r->>'material_code', ''),
             COALESCE(r->>'spec', ''),
             COALESCE(r->>'material_name', ''),
             NULLIF(r->>'received_date', '')::date,
             (r->>'received_qty')::numeric,
             COALESCE(r->>'purchaser', ''),
             COALESCE(NULLIF(r->>'inspect_type', ''), '来料检'),
             COALESCE(NULLIF(r->>'dedup_key', ''), md5(
                 COALESCE(r->>'supplier','')||'|'||COALESCE(r->>'material_code','')||'|'||COALESCE(r->>'spec','')||'|'||
                 COALESCE(r->>'material_name','')||'|'||
                 CASE WHEN r->>'received_qty' IS NULL OR r->>'received_qty' = '' THEN ''
                      WHEN (r->>'received_qty')::numeric = floor((r->>'received_qty')::numeric)
                           THEN to_char((r->>'received_qty')::numeric, 'FM99999999999999999999')
                      ELSE (r->>'received_qty')::numeric::text END)))
        ON CONFLICT DO NOTHING;
        IF FOUND THEN
            inserted := inserted + 1;
        END IF;
    END LOOP;
    RETURN inserted;
END;
$$;

CREATE OR REPLACE FUNCTION bulk_insert_inspection_records(p_rows jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    inserted integer := 0;
    r jsonb;
BEGIN
    ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS inspect_type TEXT NOT NULL DEFAULT '来料检';
    ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS dedup_key TEXT NOT NULL DEFAULT '';
    ALTER TABLE inspection_records ADD COLUMN IF NOT EXISTS purchaser TEXT DEFAULT '';
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_records'
          AND indexname = 'idx_ins_records_dedup'
          AND indexdef LIKE '%(user_id, inspect_type, dedup_key)%'
    ) THEN
        DELETE FROM inspection_records
        WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY inspect_type, dedup_key ORDER BY created_at, id
                ) AS rn
                FROM inspection_records WHERE dedup_key <> ''
            ) t WHERE rn > 1
        );
        DROP INDEX idx_ins_records_dedup;
        CREATE UNIQUE INDEX idx_ins_records_dedup
            ON inspection_records(inspect_type, dedup_key) WHERE dedup_key <> '';
    ELSIF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'inspection_records'
          AND indexname = 'idx_ins_records_dedup'
          AND indexdef LIKE '%ON inspection_records(inspect_type, dedup_key)%'
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ins_records_dedup
            ON inspection_records(inspect_type, dedup_key) WHERE dedup_key <> '';
    END IF;

    FOR r IN SELECT * FROM jsonb_array_elements(p_rows) LOOP
        INSERT INTO inspection_records
            (user_id, doc_no, supplier, material_code, spec, material_name,
             inspect_date, inspect_qty, qualified_qty, unqualified_qty,
             result, inspector, batch_no, category, purchaser, inspect_type, source_file, dedup_key)
        VALUES
            (auth.uid(),
             COALESCE(r->>'doc_no', ''),
             COALESCE(r->>'supplier', ''),
             COALESCE(r->>'material_code', ''),
             COALESCE(r->>'spec', ''),
             COALESCE(r->>'material_name', ''),
             NULLIF(r->>'inspect_date', '')::date,
             NULLIF(r->>'inspect_qty', '')::numeric,
             NULLIF(r->>'qualified_qty', '')::numeric,
             NULLIF(r->>'unqualified_qty', '')::numeric,
             COALESCE(r->>'result', ''),
             COALESCE(r->>'inspector', ''),
             COALESCE(r->>'batch_no', ''),
             COALESCE(r->>'category', ''),
             COALESCE(r->>'purchaser', ''),
             COALESCE(NULLIF(r->>'inspect_type', ''), '来料检'),
             COALESCE(r->>'source_file', ''),
             COALESCE(NULLIF(r->>'dedup_key', ''), md5(
                 COALESCE(r->>'supplier','')||'|'||COALESCE(r->>'material_code','')||'|'||COALESCE(r->>'spec','')||'|'||
                 COALESCE(r->>'material_name','')||'|'||
                 CASE WHEN r->>'inspect_qty' IS NULL OR r->>'inspect_qty' = '' THEN ''
                      WHEN (r->>'inspect_qty')::numeric = floor((r->>'inspect_qty')::numeric)
                           THEN to_char((r->>'inspect_qty')::numeric, 'FM99999999999999999999')
                      ELSE (r->>'inspect_qty')::numeric::text END||'|'||
                 COALESCE(r->>'doc_no','')||'|'||COALESCE(NULLIF(r->>'inspect_date',''),''))))
        ON CONFLICT DO NOTHING;
        IF FOUND THEN
            inserted := inserted + 1;
        END IF;
    END LOOP;
    RETURN inserted;
END;
$$;

REVOKE ALL ON FUNCTION bulk_insert_inspections(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bulk_insert_inspections(jsonb) TO authenticated;
REVOKE ALL ON FUNCTION bulk_insert_inspection_records(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bulk_insert_inspection_records(jsonb) TO authenticated;
"""


def get_shared_mode_migration_sql() -> str:
    """老库团队共享迁移 SQL（幂等，可重复执行）

    升级后：
      1. 送检/检验数据对所有登录用户共享（可见/可删），多个检验员协作
      2. 去重改为跨账号全局去重（多人上传同一记录自动跳过）
      3. 安装 inspect_rls_mode 检测函数，供应用判断是否已迁移
    """
    return """
-- =============================================================
-- 团队共享迁移 SQL（老库专用，幂等可重复执行）
-- =============================================================

-- 1. 送检记录表：SELECT/DELETE 放开为所有登录用户（INSERT 仍记录上传人）
DROP POLICY IF EXISTS "Users can view own inspections" ON inspection_submissions;
DROP POLICY IF EXISTS "Users can delete own inspections" ON inspection_submissions;
DROP POLICY IF EXISTS "Users can view all inspections" ON inspection_submissions;
DROP POLICY IF EXISTS "Users can delete all inspections" ON inspection_submissions;

CREATE POLICY "Users can view all inspections"
    ON inspection_submissions FOR SELECT
    USING (auth.uid() IS NOT NULL);

CREATE POLICY "Users can delete all inspections"
    ON inspection_submissions FOR DELETE
    USING (auth.uid() IS NOT NULL);

-- 2. 检验记录表：SELECT/DELETE 放开为所有登录用户
DROP POLICY IF EXISTS "Users can view own ins_records" ON inspection_records;
DROP POLICY IF EXISTS "Users can delete own ins_records" ON inspection_records;
DROP POLICY IF EXISTS "Users can view all ins_records" ON inspection_records;
DROP POLICY IF EXISTS "Users can delete all ins_records" ON inspection_records;

CREATE POLICY "Users can view all ins_records"
    ON inspection_records FOR SELECT
    USING (auth.uid() IS NOT NULL);

CREATE POLICY "Users can delete all ins_records"
    ON inspection_records FOR DELETE
    USING (auth.uid() IS NOT NULL);

-- 3. 清理跨账号重复记录（保留最早一条），否则无法创建全局唯一索引
DELETE FROM inspection_submissions
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY inspect_type, dedup_key ORDER BY created_at, id
        ) AS rn
        FROM inspection_submissions WHERE dedup_key <> ''
    ) t WHERE rn > 1
);

DELETE FROM inspection_records
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY inspect_type, dedup_key ORDER BY created_at, id
        ) AS rn
        FROM inspection_records WHERE dedup_key <> ''
    ) t WHERE rn > 1
);

-- 4. 重建为跨账号全局去重索引（部分索引：排除空去重键的异常行）
DROP INDEX IF EXISTS idx_inspection_dedup;
DROP INDEX IF EXISTS idx_ins_records_dedup;
CREATE UNIQUE INDEX idx_inspection_dedup
    ON inspection_submissions(inspect_type, dedup_key) WHERE dedup_key <> '';
CREATE UNIQUE INDEX idx_ins_records_dedup
    ON inspection_records(inspect_type, dedup_key) WHERE dedup_key <> '';

-- 5. 安装共享模式检测函数（应用通过 RPC 判断是否已迁移）
CREATE OR REPLACE FUNCTION inspect_rls_mode()
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'inspection_submissions'
          AND policyname = 'Users can view all inspections'
    );
$$;
"""


def is_shared_mode() -> bool:
    """检测当前是否为团队共享模式（老库需先执行 get_shared_mode_migration_sql）"""
    global _last_db_check_error
    try:
        client = _get_client()
        _check_client(client)
        data = client.rpc("inspect_rls_mode").execute()
        _last_db_check_error = ""
        return bool(data.data)
    except Exception as e:
        _last_db_check_error = str(e)
        return False


def ensure_inspect_type_columns() -> bool:
    """检测两张表是否已有 inspect_type / dedup_key / purchaser 列（老库需先执行迁移 SQL）"""
    global _last_db_check_error
    try:
        client = _get_client()
        _check_client(client)
        client.table("inspection_submissions").select("inspect_type,dedup_key,purchaser").limit(1).execute()
        client.table("inspection_records").select("inspect_type,dedup_key,purchaser").limit(1).execute()
        _last_db_check_error = ""
        return True
    except Exception as e:
        _last_db_check_error = str(e)
        return False


def ensure_inspection_records_table() -> bool:
    """检测 inspection_records 表是否存在"""
    global _last_db_check_error
    try:
        client = _get_client()
        _check_client(client)
        client.table("inspection_records").select("id").limit(1).execute()
        _last_db_check_error = ""
        return True
    except Exception as e:
        _last_db_check_error = str(e)
        return False


def ensure_supplier_aliases_table() -> bool:
    """检测 supplier_aliases 表是否存在"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("supplier_aliases").select("alias").limit(1).execute()
        return True
    except Exception:
        return False


@_with_retry
def _do_list_inspection_records(client, limit=None, inspect_type=None):
    q = client.table("inspection_records").select("*").order(
        "inspect_date", desc=True).order("created_at", desc=True)
    if inspect_type:
        q = q.eq("inspect_type", inspect_type)
    if limit:
        q = q.limit(limit)
    return q.execute()


def list_inspection_records(limit: int = None, inspect_type: str = None) -> list:
    """列出团队共享的检验记录（按质检日期倒序）
    inspect_type 传入工序名则只返回该工序的记录（None 返回全部，兼容旧调用）。"""
    try:
        client = _get_client()
        _check_client(client)
        result = _do_list_inspection_records(client, limit, inspect_type)

        return result.data
    except Exception as e:
        st.error(f"获取检验记录失败: {e}")
        return []


@_with_retry
def _do_fetch_inspection_records(client):
    """只拉取对账所需的业务列（不含 id / created_at）"""
    return client.table("inspection_records").select(
        "doc_no,supplier,material_code,spec,material_name,inspect_date,"
        "inspect_qty,qualified_qty,unqualified_qty,result,inspector,batch_no,category,purchaser,inspect_type"
    ).order("inspect_date", desc=True).order("created_at", desc=True).execute()


def fetch_inspection_records() -> list:
    """轻量查询：全部累计检验记录（对账用）"""
    try:
        client = _get_client()
        _check_client(client)
        try:
            result = _do_fetch_inspection_records(client)
        except Exception:
            # 老库尚无 purchaser 列 → 回退不带该列查询
            result = client.table("inspection_records").select(
                "doc_no,supplier,material_code,spec,material_name,inspect_date,"
                "inspect_qty,qualified_qty,unqualified_qty,result,inspector,batch_no,category,inspect_type"
            ).order("inspect_date", desc=True).order("created_at", desc=True).execute()
        return result.data
    except Exception as e:
        st.error(f"获取检验记录失败: {e}")
        return []


@_with_retry
def _do_count_inspection_records(client, inspect_type=None):
    q = client.table("inspection_records").select("id", count="exact")
    if inspect_type:
        q = q.eq("inspect_type", inspect_type)
    return q.execute()


def count_inspection_records(inspect_type: str = None) -> int:
    """获取当前用户检验记录总数（inspect_type 传入工序名则只统计该工序）"""
    try:
        client = _get_client()
        _check_client(client)
        result = _do_count_inspection_records(client, inspect_type)
        return int(result.count or 0)
    except Exception:
        return 0


@_with_retry
def _do_insert_inspection_records(client, rows):
    return client.table("inspection_records").insert(rows).execute()


def insert_inspection_records(rows: list) -> int:
    """
    批量插入检验记录（幂等），返回实际插入条数。
    优先 RPC 原子入库；函数未创建时回退普通批量插入 + 逐条跳过冲突。
    """
    if not rows:
        return 0
    try:
        client = _get_client()
        _check_client(client)
        uid = _get_user_id()
        if not uid:
            st.error("插入检验记录失败: 未获取到用户 ID，请重新登录。")
            return 0

        payload = _sanitize_rows(rows, uid)

        try:
            data = client.rpc("bulk_insert_inspection_records", {"p_rows": payload}).execute().data
            try:
                return int(data)
            except (TypeError, ValueError):
                return len(payload)
        except Exception as e:
            msg = str(e).lower()
            if any(k in msg for k in (
                    "could not find the function", "does not exist",
                    "pgrst202", "bulk_insert_inspection_records")):
                pass
            else:
                raise

        try:
            _do_insert_inspection_records(client, payload)
            return len(payload)
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("duplicate", "unique", "23505", "conflict")):
                inserted = 0
                for r in payload:
                    try:
                        client.table("inspection_records").insert(r).execute()
                        inserted += 1
                    except Exception:
                        pass
                return inserted
            raise
    except Exception as e:
        st.error(f"插入检验记录失败: {e}")
        return 0


@_with_retry
def _do_clear_inspection_records(client, inspect_type=None):
    q = client.table("inspection_records").delete()
    if inspect_type:
        q = q.eq("inspect_type", inspect_type)
    else:
        q = q.neq("inspect_type", "")  # PostgREST 要求 DELETE 必须带过滤条件
    return q.execute()


def clear_inspection_records(inspect_type: str = None) -> bool:
    """清空团队共享的检验记录（inspect_type 传入工序名则只清空该工序）"""
    try:
        client = _get_client()
        _check_client(client)
        _do_clear_inspection_records(client, inspect_type)
        return True
    except Exception as e:
        st.error(f"清空检验记录失败: {e}")
        return False


# ==================== 供应商别名 CRUD ====================

@_with_retry
def _do_list_supplier_aliases(client):
    return client.table("supplier_aliases").select("*").order("alias", desc=False).execute()


def list_supplier_aliases() -> list:
    """列出全部供应商别名映射（团队共享）"""
    try:
        client = _get_client()
        _check_client(client)
        result = _do_list_supplier_aliases(client)
        return result.data
    except Exception as e:
        st.error(f"获取供应商别名失败: {e}")
        return []


def add_supplier_alias(alias: str, canonical: str) -> bool:
    """新增供应商别名映射（alias → canonical）"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("supplier_aliases").insert({
            "alias": alias.strip(),
            "canonical": canonical.strip(),
        }).execute()
        return True
    except Exception as e:
        st.error(f"新增供应商别名失败: {e}")
        return False


def delete_supplier_alias(alias: str) -> bool:
    """删除供应商别名映射"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("supplier_aliases").delete().eq("alias", alias).execute()
        return True
    except Exception as e:
        st.error(f"删除供应商别名失败: {e}")
        return False


# ==================== 邮件收件人 CRUD ====================

def ensure_report_recipients_table() -> bool:
    """检测 report_recipients 表是否存在"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("report_recipients").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def ensure_report_recipients_columns() -> bool:
    """检测 report_recipients 表是否有 recipient_type / inspect_type 列（老表需执行迁移 SQL）"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("report_recipients").select("recipient_type, inspect_type").limit(1).execute()
        return True
    except Exception:
        return False


def get_report_recipients_migration_sql() -> str:
    """老表迁移 SQL：补充 recipient_type / inspect_type 列，并把唯一约束升级为 (email, inspect_type)。
    幂等，可重复执行。"""
    return """
-- 老表迁移：新增 recipient_type 列（'to' 收件人 / 'cc' 抄送人）
ALTER TABLE report_recipients ADD COLUMN IF NOT EXISTS recipient_type TEXT NOT NULL DEFAULT 'to';
-- 老表迁移：新增 inspect_type 列（适用工序，'全部' = 所有工序都发）
ALTER TABLE report_recipients ADD COLUMN IF NOT EXISTS inspect_type TEXT NOT NULL DEFAULT '全部';
-- 唯一约束从 email 升级为 (email, inspect_type)：同一邮箱可为不同工序分别配置角色
ALTER TABLE report_recipients DROP CONSTRAINT IF EXISTS report_recipients_email_key;
ALTER TABLE report_recipients DROP CONSTRAINT IF EXISTS report_recipients_email_inspect_type_key;
ALTER TABLE report_recipients ADD CONSTRAINT report_recipients_email_inspect_type_key UNIQUE (email, inspect_type);
"""


def list_report_recipients() -> list:
    """列出全部邮件收件人（团队共享）"""
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("report_recipients").select("*").order("created_at", desc=True).execute()
        return result.data
    except Exception as e:
        st.error(f"获取收件人列表失败: {e}")
        return []


def add_report_recipient(email: str, recipient_type: str = 'to', inspect_type: str = '全部') -> bool:
    """新增收件人邮箱
    recipient_type: 'to' 收件人 / 'cc' 抄送人
    inspect_type: 适用的检验工序，'全部' = 所有工序都发
    同一邮箱可为不同工序分别配置角色，去重由唯一约束 (email, inspect_type) 兜底"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("report_recipients").insert({
            "email": email.strip(),
            "recipient_type": 'cc' if recipient_type == 'cc' else 'to',
            "inspect_type": inspect_type if inspect_type else '全部',
        }).execute()
        return True
    except Exception as e:
        st.error(f"新增收件人失败: {e}")
        return False


def delete_report_recipient(rid: str) -> bool:
    """按 id 删除收件人"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("report_recipients").delete().eq("id", rid).execute()
        return True
    except Exception as e:
        st.error(f"删除收件人失败: {e}")
        return False


# ==================== 未检验清单快照（持久化：每次比对结果覆盖上一次） ====================

def get_create_unchecked_snapshots_sql() -> str:
    """未检验清单快照表建表 SQL（幂等，可重复执行）"""
    return """
CREATE TABLE IF NOT EXISTS unchecked_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspect_type TEXT NOT NULL DEFAULT '来料检',
    supplier TEXT DEFAULT '',
    material_code TEXT DEFAULT '',
    spec TEXT DEFAULT '',
    material_name TEXT DEFAULT '',
    received_date DATE,
    received_qty NUMERIC,
    purchaser TEXT DEFAULT '',
    unmatched_reason TEXT DEFAULT '',
    saved_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE unchecked_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "All auth can view unchecked_snapshots" ON unchecked_snapshots;
DROP POLICY IF EXISTS "All auth can manage unchecked_snapshots" ON unchecked_snapshots;

CREATE POLICY "All auth can view unchecked_snapshots"
    ON unchecked_snapshots FOR SELECT TO authenticated USING (true);

CREATE POLICY "All auth can manage unchecked_snapshots"
    ON unchecked_snapshots FOR ALL TO authenticated USING (true) WITH CHECK (true);
"""


def ensure_unchecked_snapshots_table() -> bool:
    """检测 unchecked_snapshots 表是否存在"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("unchecked_snapshots").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def save_unchecked_snapshot(inspect_type: str, rows: list) -> bool:
    """持久化未检验清单：先删除该工序旧快照，再写入最新比对结果（覆盖语义，保存最新的）"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("unchecked_snapshots").delete().eq("inspect_type", inspect_type).execute()
        if rows:
            client.table("unchecked_snapshots").insert(rows).execute()
        return True
    except Exception as e:
        if "unchecked_snapshots" in str(e):
            st.error("保存未检验清单失败：`unchecked_snapshots` 表尚未创建。"
                     "请先到「📮 邮件收件人」页或 Supabase SQL Editor 执行建表 SQL：")
            st.code(get_create_unchecked_snapshots_sql(), language='sql')
        else:
            st.error(f"保存未检验清单失败: {e}")
        return False


def load_unchecked_snapshot(inspect_type: str) -> dict:
    """读取该工序最近一次持久化的未检验清单（覆盖保存，故仅一份最新数据）"""
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("unchecked_snapshots").select(
            "*"
        ).eq("inspect_type", inspect_type).order("saved_at", desc=True).execute()
        data = result.data or []
        return {"rows": data, "saved_at": data[0].get("saved_at") if data else None}
    except Exception as e:
        st.error(f"读取未检验清单失败: {e}")
        return {"rows": [], "saved_at": None}


def delete_unchecked_snapshots(inspect_type: str) -> bool:
    """删除该工序全部未检验清单快照"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("unchecked_snapshots").delete().eq("inspect_type", inspect_type).execute()
        return True
    except Exception as e:
        st.error(f"删除未检验清单失败: {e}")
        return False


# ==================== 邮件发送排程（前端设置周几/几点发送） ====================

def get_create_report_schedule_sql() -> str:
    """邮件发送排程表建表 SQL（幂等，可重复执行）"""
    return """
CREATE TABLE IF NOT EXISTS report_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    send_days TEXT NOT NULL DEFAULT '1,2,3,4,5',
    send_time TEXT NOT NULL DEFAULT '08:00',
    last_sent_date TEXT DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE report_schedule ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "All auth can view report_schedule" ON report_schedule;
DROP POLICY IF EXISTS "All auth can manage report_schedule" ON report_schedule;

CREATE POLICY "All auth can view report_schedule"
    ON report_schedule FOR SELECT TO authenticated USING (true);

CREATE POLICY "All auth can manage report_schedule"
    ON report_schedule FOR ALL TO authenticated USING (true) WITH CHECK (true);
"""


def ensure_report_schedule_table() -> bool:
    """检测 report_schedule 表是否存在"""
    try:
        client = _get_client()
        _check_client(client)
        client.table("report_schedule").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def get_report_schedule() -> dict:
    """读取邮件发送排程（默认：周一至周五 08:00，北京时间）"""
    default = {"send_days": "1,2,3,4,5", "send_time": "08:00", "last_sent_date": ""}
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("report_schedule").select("send_days,send_time,last_sent_date").limit(1).execute()
        data = result.data
        if data:
            r = data[0]
            return {
                "send_days": str(r.get("send_days") or "1,2,3,4,5"),
                "send_time": str(r.get("send_time") or "08:00"),
                "last_sent_date": str(r.get("last_sent_date") or ""),
            }
        return default
    except Exception as e:
        st.error(f"读取发送排程失败: {e}")
        return default


def save_report_schedule(send_days: list, send_time: str) -> bool:
    """保存邮件发送排程（单行 upsert：有行则更新，无行则插入）"""
    try:
        client = _get_client()
        _check_client(client)
        days = ",".join(str(int(d)) for d in send_days)
        payload = {"send_days": days, "send_time": send_time}
        rows = client.table("report_schedule").select("id").limit(1).execute().data
        if rows:
            client.table("report_schedule").update(payload).eq("id", rows[0]["id"]).execute()
        else:
            client.table("report_schedule").insert(payload).execute()
        return True
    except Exception as e:
        st.error(f"保存发送排程失败: {e}")
        return False
