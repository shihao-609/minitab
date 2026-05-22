"""
Supabase 数据库操作辅助模块
提供数据集的 CRUD 操作，实现质量数据的持久化存储
"""

import os
from datetime import datetime
from supabase import create_client, Client
import pandas as pd
import json
import streamlit as st

# ==================== 初始化 Supabase 客户端 ====================

def _get_supabase_url() -> str:
    """支持 st.secrets（Streamlit Cloud）和 .env（本地）"""
    try:
        return st.secrets.get("SUPABASE_URL", "") or os.environ.get("SUPABASE_URL", "")
    except Exception:
        return os.environ.get("SUPABASE_URL", "")


def _get_supabase_key() -> str:
    """支持 st.secrets（Streamlit Cloud）和 .env（本地）"""
    try:
        return st.secrets.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
    except Exception:
        return os.environ.get("SUPABASE_ANON_KEY", "")


@st.cache_resource
def get_supabase_client() -> Client:
    """创建并缓存 Supabase 客户端（单例模式）"""
    url = _get_supabase_url()
    key = _get_supabase_key()
    return create_client(url, key)


def _init_client() -> Client:
    """获取客户端（非缓存版本，用于内部调用）"""
    url = _get_supabase_url()
    key = _get_supabase_key()
    if not url or not key:
        raise ValueError("SUPABASE_URL 或 SUPABASE_ANON_KEY 未设置，请检查 .env 文件或 Streamlit Secrets")
    return create_client(url, key)


# ==================== 数据集 CRUD ====================

def save_dataset(name: str, df: pd.DataFrame, columns_info: dict = None,
                 fishbone_config: dict = None) -> dict | None:
    """
    将 DataFrame 保存到 Supabase 数据集表

    Args:
        name: 数据集名称
        df: 要保存的 DataFrame
        columns_info: 可选的列信息
        fishbone_config: 可选的鱼骨图配置 {'problem': str, 'raw_input': str}

    Returns:
        保存的记录，或 None（失败时）
    """
    try:
        client = _init_client()
        data = {
            "name": name,
            "data": json.loads(df.to_json(orient="records", force_ascii=False)),
            "columns_info": columns_info or list(df.columns),
            "row_count": len(df),
            "fishbone_config": fishbone_config,
        }
        result = client.table("datasets").insert(data).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        st.error(f"保存数据集失败: {e}")
        return None


def load_dataset(dataset_id: str) -> dict | None:
    """
    从 Supabase 加载指定的数据集

    Args:
        dataset_id: 数据集 UUID

    Returns:
        包含 'df'(DataFrame) 和可选的 'fishbone_config' 字典，或 None（失败时）
    """
    try:
        client = _init_client()
        result = client.table("datasets").select("*").eq("id", dataset_id).execute()
        if result.data:
            record = result.data[0]
            df = pd.DataFrame(record["data"])
            ret = {"df": df}
            if record.get("fishbone_config"):
                ret["fishbone_config"] = record["fishbone_config"]
            return ret
        return None
    except Exception as e:
        st.error(f"加载数据集失败: {e}")
        return None


def save_fishbone_config(dataset_id: str, fishbone_config: dict) -> bool:
    """
    单独更新数据集关联的鱼骨图配置

    Args:
        dataset_id: 数据集 UUID
        fishbone_config: 鱼骨图配置 {'problem': str, 'raw_input': str}

    Returns:
        是否成功
    """
    try:
        client = _init_client()
        client.table("datasets").update({"fishbone_config": fishbone_config}).eq("id", dataset_id).execute()
        return True
    except Exception as e:
        st.error(f"保存鱼骨图配置失败: {e}")
        return False


def list_datasets() -> list[dict]:
    """
    列出所有已保存的数据集

    Returns:
        数据集列表（按创建时间倒序）
    """
    try:
        client = _init_client()
        result = client.table("datasets").select("*").order("created_at", desc=True).execute()
        return result.data
    except Exception as e:
        st.error(f"获取数据集列表失败: {e}")
        return []


def update_dataset(dataset_id: str, name: str = None, df: pd.DataFrame = None) -> bool:
    """
    更新数据集

    Args:
        dataset_id: 数据集 UUID
        name: 新名称（可选）
        df: 新数据（可选）

    Returns:
        是否成功
    """
    try:
        client = _init_client()
        update_data = {"updated_at": datetime.utcnow().isoformat()}
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

    Args:
        dataset_id: 数据集 UUID

    Returns:
        是否成功
    """
    try:
        client = _init_client()
        client.table("datasets").delete().eq("id", dataset_id).execute()
        return True
    except Exception as e:
        st.error(f"删除数据集失败: {e}")
        return False
