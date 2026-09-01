"""
Supabase 数据库保活脚本
========================
Supabase 免费版在约7天无活动后会暂停项目。
该脚本定期 ping 数据库，防止其进入休眠状态。

使用方法:
  1. 直接运行: python keep_alive.py
  2. GitHub Actions 定时执行 (推荐): 见 .github/workflows/keep_alive.yml
  3. 外部 cron 服务 (如 cron-job.org)

支持的环境变量:
  - SUPABASE_URL: Supabase 项目 URL（必填）
  - SUPABASE_ANON_KEY: Supabase anon key（与 SERVICE_ROLE_KEY 二选一）
  - SUPABASE_SERVICE_ROLE_KEY: Supabase service_role key（与 ANON_KEY 二选一，推荐，
    因为系统邮件等流程已配置该密钥，保活不依赖额外配置）
"""

import os
import sys
import logging
from datetime import datetime, timezone

# Windows 控制台（GBK）下 emoji 输出会抛 UnicodeEncodeError → 允许替换而不是崩溃
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# 本地调试时可从项目根 .env 读取配置（GitHub Actions 中由 workflow 注入，优先级更高）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# 优先 ping 当前系统实际使用的业务表（比 datasets 更相关、更能证明业务可用）
PING_TABLES = ["inspection_submissions", "inspection_records", "datasets"]


def get_supabase_client():
    """获取 Supabase 客户端（ANON_KEY 优先，缺失时回退 SERVICE_ROLE_KEY）"""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    if not url or not key:
        logger.error("❌ 缺少 SUPABASE_URL，且 SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY 均未设置")
        return None

    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:
        logger.error(f"❌ 创建 Supabase 客户端失败: {e}")
        return None


def ping_database(client) -> bool:
    """
    Ping 数据库：依次尝试对业务表做一次轻量查询（limit=1），
    全部失败后回退 REST API 根路径探测，证明数据库在线。
    """
    tried = []
    for table in PING_TABLES:
        try:
            client.table(table).select("id").limit(1).execute()
            logger.info(f"✅ 数据库心跳成功（表 {table}）— {datetime.now(timezone.utc).isoformat()}")
            return True
        except Exception as e:
            msg = str(e)
            tried.append(f"{table}: {msg[:80]}")
            if "does not exist" in msg.lower() or "404" in msg or "column" in msg.lower():
                logger.warning(f"⚠️  表 {table} 不可用（{msg[:60]}），尝试下一个...")
            else:
                logger.warning(f"⚠️  表 {table} 心跳失败（{msg[:60]}），尝试下一个...")

    # 回退：REST API 根路径探测（只要数据库在线即返回 200）
    try:
        import requests
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        resp = requests.get(f"{url}/rest/v1/", headers=headers, timeout=10)
        if resp.status_code == 200:
            logger.info(f"✅ 数据库心跳成功（REST API 回退）— {datetime.now(timezone.utc).isoformat()}")
            return True
        logger.warning(f"⚠️  REST API 回退返回 {resp.status_code}")
    except Exception as e:
        logger.error(f"❌ REST API 回退也失败: {e}")
    logger.error(f"❌ 全部保活探测均失败: {tried}")
    return False


def wake_streamlit():
    """主动访问 Streamlit 应用，防止社区版应用休眠导致冷启动（打开慢）。"""
    import requests
    url = os.environ.get("STREAMLIT_URL") or "https://minitab-nappfbsncsxtsc9gst2mjvx.streamlit.app/"
    try:
        resp = requests.get(url, timeout=30)
        logger.info(f"✅ Streamlit 应用唤醒请求完成，HTTP {resp.status_code}")
        return True
    except Exception as e:
        # 冷启动可能需要 1~3 分钟，超时本身已触发应用启动
        logger.info(f"ℹ️  Streamlit 唤醒请求已发出（冷启动可能在后台进行）: {str(e)[:80]}")
        return True


def main():
    """主函数：执行一次数据库保活 ping + Streamlit 应用唤醒"""
    logger.info("=" * 50)
    logger.info("🔧 Supabase 数据库保活检查")
    logger.info(f"   时间: {datetime.now(timezone.utc).isoformat()}")
    logger.info(f"   URL:  {os.environ.get('SUPABASE_URL', '未设置')[:40]}...")
    logger.info("=" * 50)

    client = get_supabase_client()
    if client is None:
        logger.error("❌ 保活失败：无法连接到 Supabase")
        sys.exit(1)

    success = ping_database(client)
    # 无论数据库 ping 结果如何，都尝试唤醒 Streamlit 应用（两者独立）
    if os.environ.get("STREAMLIT_URL") or os.environ.get("WAKE_STREAMLIT", "1") != "0":
        wake_streamlit()

    if success:
        logger.info("🎉 保活成功！数据库保持活跃状态。")
        sys.exit(0)
    else:
        logger.error("❌ 保活失败！请检查网络与密钥配置。")
        sys.exit(1)


if __name__ == "__main__":
    main()
