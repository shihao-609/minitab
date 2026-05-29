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
  - SUPABASE_URL: Supabase 项目 URL
  - SUPABASE_ANON_KEY: Supabase anon key
"""

import os
import sys
import logging
from datetime import datetime, timezone
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def get_supabase_client():
    """获取 Supabase 客户端"""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_ANON_KEY", "")

    if not url or not key:
        logger.error("❌ 缺少 SUPABASE_URL 或 SUPABASE_ANON_KEY 环境变量")
        return None

    try:
        return create_client(url, key)
    except Exception as e:
        logger.error(f"❌ 创建 Supabase 客户端失败: {e}")
        return None


def ping_database(client) -> bool:
    """
    Ping 数据库：执行一次轻量级查询，证明数据库在线。
    查询 datasets 表，limit=1，不造成负载。
    """
    try:
        result = client.table("datasets").select("id").limit(1).execute()
        logger.info(f"✅ 数据库心跳成功 — {datetime.now(timezone.utc).isoformat()}")
        return True
    except Exception as e:
        msg = str(e)
        if "does not exist" in msg.lower() or "404" in msg:
            # 表可能不存在（新项目），尝试其他方式 ping
            logger.warning(f"⚠️  datasets 表不存在，尝试原始 ping...")
        else:
            logger.error(f"❌ 数据库心跳失败: {e}")
            return False

    # 回退：使用 REST API health check 风格的方式
    try:
        # 直接尝试一个轻量级 REST 请求
        import requests
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_ANON_KEY", "")
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        resp = requests.get(f"{url}/rest/v1/", headers=headers, timeout=10)
        if resp.status_code == 200:
            logger.info(f"✅ 数据库心跳成功（REST API 回退）— {datetime.now(timezone.utc).isoformat()}")
            return True
        else:
            logger.warning(f"⚠️  REST API 回退返回 {resp.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ REST API 回退也失败: {e}")
        return False


def main():
    """主函数：执行一次数据库保活 ping"""
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

    if success:
        logger.info("🎉 保活成功！数据库保持活跃状态。")
        sys.exit(0)
    else:
        logger.error("❌ 保活失败！")
        sys.exit(1)


if __name__ == "__main__":
    main()
