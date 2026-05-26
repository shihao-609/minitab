"""
Supabase 数据库操作辅助模块 (v2 — 支持用户隔离)
=================================================
提供数据集的 CRUD 操作，实现质量数据的持久化存储。

v2 关键变更（应对"增加账号密码"的影响）：
  1. 所有写操作自动注入 user_id，插入时携带当前用户身份
  2. 读取操作优先使用用户 JWT 客户端，Supabase RLS 策略自动过滤数据
  3. 兼容旧版 anon key 客户端（非认证模式，用于注册/登录阶段）
"""

import os
from datetime import datetime, timezone
from supabase import create_client, Client
import pandas as pd
import json
import streamlit as st
from typing import Optional, List


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


def _get_user_id() -> Optional[str]:
    """从 session 获取当前用户 ID"""
    if st.session_state.get("authenticated") and st.session_state.get("user"):
        return st.session_state.user.id
    return None


def _get_client() -> Optional[Client]:
    """
    获取 Supabase 客户端（自动选择认证模式）

    优先级：
      1. 已登录 → 用 anon key 创建后注入 session，SDK 自动将 JWT 附加到请求头
      2. 未登录 → 使用 anon key（匿名访问，RLS 策略下只能读公开数据）

    这是解决"额外注意项②"的关键设计：
      - 不能用 `create_client(url, raw_jwt)` —— 原始 JWT 方式不会自动刷新
      - 必须用 `client.auth.set_session(access_token, refresh_token)` 注入 session
      - 注入后 supabase-py SDK 会自动在每次请求附加 Authorization: Bearer <jwt>
      - JWT 默认有效期 1 小时，SDK 通过 refresh_token 自动续期

    Supabase RLS 通过 JWT 中的 auth.uid() 识别用户身份，确保数据隔离。
    """
    url = _get_supabase_url()
    key = _get_supabase_key()
    if not url or not key:
        return None

    client = create_client(url, key)

    # 已登录：注入用户 session（SDK 自动附加 JWT + 自动刷新）
    if st.session_state.get("authenticated") and st.session_state.get("session"):
        session = st.session_state.session
        try:
            client.auth.set_session(
                session.access_token,
                session.refresh_token
            )
        except Exception:
            pass  # session 可能已过期，降级为匿名访问

    return client


def _check_client(client: Optional[Client]):
    """检查客户端是否可用，不可用时抛出异常"""
    if client is None:
        raise ValueError("SUPABASE_URL 或 SUPABASE_ANON_KEY 未设置，请检查 .env 文件或 Streamlit Secrets")


# ==================== 数据集 CRUD ====================

def save_dataset(name: str, df: pd.DataFrame, columns_info: dict = None) -> Optional[dict]:
    """
    将 DataFrame 保存到 Supabase 数据集表

    v2 变更：
      - 自动注入 user_id（登录状态下）
      - 使用 JWT 客户端调用（RLS 策略确保数据隔离）

    Args:
        name: 数据集名称
        df: 要保存的 DataFrame
        columns_info: 可选的列信息

    Returns:
        保存的记录，或 None（失败时）
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
        # 注入用户 ID
        uid = _get_user_id()
        if uid:
            data["user_id"] = uid

        result = client.table("datasets").insert(data).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"保存数据集失败: {e}")
        return None


def load_dataset(dataset_id: str) -> Optional[pd.DataFrame]:
    """
    从 Supabase 加载指定的数据集

    v2 变更：
      - 使用 JWT 客户端调用，RLS 自动限制只能读取自己的数据
      - 即使 RLS 未配置，也会回退到手动 user_id 过滤

    Args:
        dataset_id: 数据集 UUID

    Returns:
        DataFrame，或 None（失败时）
    """
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("datasets").select("*").eq("id", dataset_id).execute()
        if result.data:
            record = result.data[0]
            # RLS 回退：如果记录有 user_id 且与当前用户不匹配，拒绝访问
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


def list_datasets() -> list:
    """
    列出当前用户的所有数据集

    v2 变更：
      - RLS 策略自动按 user_id 过滤
      - 如果未配置 RLS，客户端 fallback 手动过滤

    Returns:
        数据集列表（按创建时间倒序）
    """
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("datasets").select("*").order("created_at", desc=True).execute()

        uid = _get_user_id()
        if uid and result.data:
            # RLS 回退过滤：只返回属于当前用户的记录
            # （如果 RLS 正确配置，这一步是冗余的但无害）
            result.data = [r for r in result.data if r.get("user_id") == uid]

        return result.data
    except Exception as e:
        st.error(f"获取数据集列表失败: {e}")
        return []


def update_dataset(dataset_id: str, name: str = None, df: pd.DataFrame = None) -> bool:
    """
    更新数据集

    v2 变更：使用 JWT 客户端，确保只能更新自己的数据
    """
    try:
        client = _get_client()
        _check_client(client)
        update_data = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if name:
            update_data["name"] = name
        if df is not None:
            update_data["data"] = json.loads(df.to_json(orient="records", force_ascii=False))
            update_data["row_count"] = len(df)

        client.table("datasets").update(update_data).eq("id", dataset_id).execute()
        return True
    except Exception as e:
        st.error(f"更新数据集失败: {e}")
        return False


def delete_dataset(dataset_id: str) -> bool:
    """
    删除数据集

    v2 变更：使用 JWT 客户端，RLS 确保只能删除自己的数据
    """
    try:
        client = _get_client()
        _check_client(client)
        client.table("datasets").delete().eq("id", dataset_id).execute()
        return True
    except Exception as e:
        st.error(f"删除数据集失败: {e}")
        return False


# ==================== 鱼骨图配置 CRUD ====================

def save_fishbone(name: str, problem: str, raw_input: str) -> Optional[dict]:
    """
    将鱼骨图配置保存到独立的 fishbone_configs 表

    v2 变更：自动注入 user_id
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

        result = client.table("fishbone_configs").insert(data).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"保存鱼骨图配置失败: {e}")
        return None


def list_fishbone_configs() -> list:
    """
    列出当前用户的所有鱼骨图配置

    v2 变更：RLS + fallback 过滤
    """
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("fishbone_configs").select("*").order("created_at", desc=True).execute()

        uid = _get_user_id()
        if uid and result.data:
            result.data = [r for r in result.data if r.get("user_id") == uid]

        return result.data
    except Exception as e:
        st.error(f"获取鱼骨图配置列表失败: {e}")
        return []


def load_fishbone_config(config_id: str) -> Optional[dict]:
    """
    加载指定鱼骨图配置
    """
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("fishbone_configs").select("*").eq("id", config_id).execute()
        if result.data:
            record = result.data[0]
            rid = record.get("user_id")
            uid = _get_user_id()
            if uid and rid and rid != uid:
                st.error("无权访问此配置")
                return None
            return record
        return None
    except Exception as e:
        st.error(f"加载鱼骨图配置失败: {e}")
        return None


def delete_fishbone_config(config_id: str) -> bool:
    """
    删除指定的鱼骨图配置
    """
    try:
        client = _get_client()
        _check_client(client)
        client.table("fishbone_configs").delete().eq("id", config_id).execute()
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


def save_report(name: str, report_md: str, analyses_summary: list,
                files_data: list, file_count: int = 0) -> Optional[dict]:
    """
    保存分析报告到 Supabase

    Args:
        name: 报告名称
        report_md: Markdown 格式的综合报告
        analyses_summary: 各文件分析摘要 [{filename, type, summary}, ...]
        files_data: 原始文件数据 [{filename, csv_data, data_type}, ...]
        file_count: 文件数量

    Returns:
        保存的记录，或 None
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

        result = client.table("analysis_reports").insert(data).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        err_msg = str(e)
        if "row-level security" in err_msg.lower() or "42501" in err_msg:
            st.error("保存报告失败: 数据库安全策略阻止了写入。请在 Supabase SQL Editor 中执行以下 SQL 修复：")
            with st.expander("📋 修复 SQL（点击展开）"):
                st.code(get_create_reports_table_sql(), language='sql')
        else:
            st.error(f"保存报告失败: {e}")
        return None


def list_reports() -> list:
    """
    列出当前用户的所有分析报告（按时间倒序）

    Returns:
        报告列表（不含 files_data 大字段，提升加载速度）
    """
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("analysis_reports").select(
            "id,name,file_count,analyses_summary,created_at,user_id"
        ).order("created_at", desc=True).execute()

        uid = _get_user_id()
        if uid and result.data:
            result.data = [r for r in result.data if r.get("user_id") == uid]

        return result.data
    except Exception as e:
        st.error(f"获取报告列表失败: {e}")
        return []


def load_report(report_id: str) -> Optional[dict]:
    """
    加载完整的分析报告（包含 report_md 和 files_data）

    Args:
        report_id: 报告 UUID

    Returns:
        报告完整记录，或 None
    """
    try:
        client = _get_client()
        _check_client(client)
        result = client.table("analysis_reports").select("*").eq("id", report_id).execute()
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
    """
    try:
        client = _get_client()
        _check_client(client)
        client.table("analysis_reports").delete().eq("id", report_id).execute()
        return True
    except Exception as e:
        st.error(f"删除报告失败: {e}")
        return False
