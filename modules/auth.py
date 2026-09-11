"""
认证模块 — 基于 Supabase Auth 的免密登录 / 用户管理
=====================================================
功能:
  - 从个人工作站跳转的免密静默登录（未注册邮箱自动创建账号）
  - JWT 令牌管理（session 生命周期内有效）
  - 登出
  - 用户信息获取

安全策略 v2（锁死自助注册，仅允许从个人工作站进入）:
  - 自助注册与"邮箱 + 密码"登录已彻底关闭，页面不再提供任何注册/登录表单
  - 唯一入口：个人工作站跳转到本系统 URL 并携带 ?email=xxx，
    由 sso_login() 触发免密静默登录；该邮箱不存在时会自动创建账号并登录
  - 直接访问本系统（无 email 参数）时，只展示"请从个人工作站进入"引导页
  - 【v3 加固】配置 WORKSTATION_SSO_SECRET 后，跳转链接必须携带
    ts + sig（HMAC-SHA256 签名），验签不通过一律拒绝且不建号，
    防止任何人手拼 ?email= 伪造入口；未配置该密钥时为兼容模式（不验签）

关键设计（解决"额外注意项"）:
  1. 登录后使用用户的 JWT 创建 Supabase 客户端，替代 anon key
     这样 Supabase RLS 策略才能正确识别当前用户并隔离数据
  2. 每个页面渲染时通过 st.session_state 验证登录状态，
     解决 Streamlit 无内置路由/中间件的问题
"""

import streamlit as st
import os
import hmac
import hashlib
import time
from supabase import create_client, Client
from typing import Optional


# ==================== 凭证获取 ====================

def _get_supabase_url() -> str:
    try:
        return st.secrets.get("SUPABASE_URL", "") or os.environ.get("SUPABASE_URL", "")
    except Exception:
        return os.environ.get("SUPABASE_URL", "")


def _get_supabase_anon_key() -> str:
    try:
        return st.secrets.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
    except Exception:
        return os.environ.get("SUPABASE_ANON_KEY", "")


def _get_supabase_service_key() -> str:
    """获取 service_role key（仅服务端使用，用于免密登录生成令牌）"""
    try:
        return st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    except Exception:
        return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def get_anon_client() -> Optional[Client]:
    """获取使用 anon key 的客户端（仅用于免密登录环节，无权读写受 RLS 保护的数据）"""
    url = _get_supabase_url()
    key = _get_supabase_anon_key()
    if not url or not key:
        return None
    return create_client(url, key)


# ==================== Session 管理 ====================

def init_auth_session():
    """初始化认证相关的 session_state 字段"""
    defaults = {
        "authenticated": False,
        "user": None,           # Supabase 返回的 user 对象
        "session": None,        # Supabase 返回的 session 对象（含 access_token）
        "auth_error": None,     # 登录时的错误信息
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def get_user_id() -> Optional[str]:
    """获取当前登录用户的 ID"""
    if st.session_state.authenticated and st.session_state.user:
        return st.session_state.user.id
    return None


def get_user_email() -> Optional[str]:
    """获取当前登录用户的邮箱"""
    if st.session_state.authenticated and st.session_state.user:
        return st.session_state.user.email
    return None


def get_user_jwt() -> Optional[str]:
    """获取当前用户的 access_token (JWT)

    这是核心：后续所有 Supabase 数据库操作都用此 JWT 创建客户端，
    而不是 anon key。RLS 策略通过 JWT 中的 sub 字段识别用户身份。
    """
    if st.session_state.authenticated and st.session_state.session:
        return st.session_state.session.access_token
    return None


def get_authenticated_client() -> Optional[Client]:
    """
    获取使用用户 JWT 的 Supabase 客户端（已认证）

    正确做法：以 anon key 创建客户端（apikey 合法），再用
    set_session(access_token, refresh_token) 注入用户 JWT。

    注意：不能把 JWT 直接当 key 传给 create_client —— supabase-py 2.x
    会把 JWT 原样放进 apikey header，被网关当作 Invalid API key 拒绝
    （已在本项目本地实测复现：T2 与云端报错一字不差）。

    set_session 行为（supabase_auth 2.x 源码确认）：
      - token 未过期：调 /auth/v1/user 校验并保存会话
      - token 已过期：用 refresh_token 自动刷新
    若校验/刷新失败则返回 None，上层提示重新登录。
    """
    session = st.session_state.session
    if not session:
        st.session_state.auth_error = "会话数据缺失，请重新登录"
        return None
    access_token = getattr(session, "access_token", None)
    if not access_token:
        st.session_state.auth_error = "会话缺少访问令牌，请重新登录"
        return None
    refresh_token = getattr(session, "refresh_token", None)
    if not refresh_token:
        st.session_state.auth_error = "会话缺少刷新令牌，请重新登录"
        return None
    anon = get_anon_client()
    if anon is None:
        st.session_state.auth_error = "匿名客户端不可用（检查 SUPABASE_URL / SUPABASE_ANON_KEY）"
        return None
    try:
        resp = anon.auth.set_session(access_token, refresh_token)
        new_session = getattr(resp, "session", None)
        if new_session is not None:
            # set_session 内部可能已刷新出全新 token，同步回 session_state
            st.session_state.session = new_session
            st.session_state.user = getattr(new_session, "user", None)
            st.session_state.auth_error = None
        return anon
    except Exception as e:
        st.session_state.auth_error = f"登录会话无效或已过期，请重新登录（{e}）"
        return None


# ==================== 认证操作 ====================

# 【安全策略 v2】自助注册与"邮箱 + 密码"登录已彻底关闭：
#   - 应用不再提供任何注册/登录表单（见下方 render_auth_page）；
#   - 唯一登录入口为"从个人工作站跳转"触发的免密静默登录 sso_login()；
#   - 若未来确有密码登录需求，可在此补回 login()/register()，
#     同时需在 Supabase Dashboard 重新开启 Email 注册，否则会被 Supabase 侧拒绝。


def logout():
    """登出：清除所有认证状态（不调用 Supabase sign_out，避免网络依赖）"""
    st.session_state.authenticated = False
    st.session_state.user = None
    st.session_state.session = None
    st.session_state.auth_error = None
    
    # 清除与用户相关的数据及免密登录标记（登出后再次带邮箱跳转需重新触发 SSO）
    keys_to_clear = ["user_data", "saved_data", "sso_attempted", "login_email"]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]


# ==================== HTTP 连接复用 ====================
# 复用同一个 httpx.Client：避免每次请求都重新做 DNS + TCP + TLS 握手，
# 跨区域访问 Supabase 时通常可省 100~500ms。
_HTTP_CLIENT = None


def _get_http_client():
    """获取进程内复用的 httpx.Client（不可用时返回 None）"""
    global _HTTP_CLIENT
    try:
        import httpx
    except Exception:
        return None
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.Client(
            timeout=15,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _HTTP_CLIENT


# ==================== 免密静默登录（从个人工作站跳转） ====================

SSO_SIGNATURE_TTL = 300  # 工作站跳转签名有效期（秒）


def _get_workstation_secret() -> str:
    """获取个人工作站与 QMS 的共享签名密钥（未配置则返回空字符串）"""
    try:
        return st.secrets.get("WORKSTATION_SSO_SECRET", "") or os.environ.get("WORKSTATION_SSO_SECRET", "")
    except Exception:
        return os.environ.get("WORKSTATION_SSO_SECRET", "")


def verify_workstation_signature(email: str, ts: str, sig: str):
    """
    校验个人工作站跳转链接的 HMAC-SHA256 签名。

    工作站侧生成的链接格式：
        ?email=<urlencode(email)>&ts=<unix秒>&sig=<小写十六进制>
        sig = HMAC_SHA256(secret, f"{email}|{ts}").hexdigest()

    要点：
      - email 必须是 URL 编码前的原值，双方保持一致（不要自行转小写/去空格）；
      - 邮箱含 \"+\" 时必须编码为 %2B（用标准 urlencode 即可）。

    Returns:
        (是否通过: bool, 提示信息: str)
        - 未配置 WORKSTATION_SSO_SECRET → (True, "")：兼容模式，不强制验签，
          保证现有"无签名跳转"仍可用，便于灰度上线。
    """
    secret = _get_workstation_secret()
    if not secret:
        return True, ""  # 兼容模式：未配置密钥则不验签

    if not email or not ts or not sig:
        return False, "链接缺少签名参数，请从个人工作站重新进入"

    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return False, "链接签名参数无效，请从个人工作站重新进入"

    if abs(time.time() - ts_int) > SSO_SIGNATURE_TTL:
        return False, "链接已过期，请从个人工作站重新进入"

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{email}|{ts}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, str(sig).strip().lower()):
        return False, "链接签名校验失败，请从个人工作站重新进入"

    return True, ""


def _generate_magiclink_otp(url, service_key, email) -> Optional[str]:
    """
    后台生成 magiclink 令牌（不发邮件），返回可用的 email_otp（6 位数字）。
    直接用 GoTrue REST API 调用：
      - 新版 Supabase 的 action_link 里是 hash 后的 token（verify 不认），
        email_otp 才是能被 verify_otp 接受的令牌；
      - supabase-py 的 generate_link 只解析 user 对象，email_otp/action_link
        会被丢弃，所以必须用 REST 方式拿原始返回。
    """
    client = _get_http_client()
    if client is None:
        return None
    try:
        resp = client.post(
            f"{url}/auth/v1/admin/generate_link",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            },
            json={
                "type": "magiclink",
                "email": email,
                "options": {"should_send_link": False},
            },
        )
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
        email_otp = data.get("email_otp")
        if not email_otp:
            props = data.get("properties") or {}
            if isinstance(props, dict):
                email_otp = props.get("email_otp")
        return str(email_otp) if email_otp else None
    except Exception:
        return None


def _find_user_id(url, service_key, email) -> Optional[str]:
    """
    按邮箱查找用户 ID（直接调 GoTrue Admin API 分页遍历）。
    不用 supabase-py 的 list_users：不同版本返回结构不一致（列表 / 对象），
    解析失败会导致误判邮箱不存在。REST 调用已实测可靠。
    """
    client = _get_http_client()
    if client is None:
        return None
    try:
        headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
        }
        page = 1
        while page <= 50:
            resp = client.get(
                f"{url}/auth/v1/admin/users?page={page}&per_page=1000",
                headers=headers,
            )
            if resp.status_code != 200:
                return None
            data = resp.json() or {}
            users = data.get("users") or []
            for u in users:
                if u.get("email") == email:
                    return u.get("id")
            if len(users) < 1000:
                break
            page += 1
        return None
    except Exception:
        return None


def _verify_otp_login(anon, email, otp) -> bool:
    """用 OTP 验证登录，成功则写入 session_state"""
    if anon is None:
        return False
    try:
        resp = anon.auth.verify_otp({
            "email": email,
            "token": otp,
            "type": "magiclink",
        })
        st.session_state.authenticated = True
        st.session_state.user = resp.user
        st.session_state.session = resp.session
        st.session_state.auth_error = None
        return True
    except Exception:
        return False


def _password_login(anon, email: str, password: str) -> bool:
    """用"邮箱 + 密码"登录（仅用于刚由后台创建的新账号），成功则写入 session_state"""
    if anon is None:
        return False
    try:
        resp = anon.auth.sign_in_with_password({"email": email, "password": password})
        st.session_state.authenticated = True
        st.session_state.user = resp.user
        st.session_state.session = resp.session
        st.session_state.auth_error = None
        return True
    except Exception:
        return False


def sso_login(email: str) -> bool:
    """
    个人工作站跳转免密登录（当前系统唯一登录方式，支持未注册邮箱自动创建账号）。

    流程：
      1. 先直接 magiclink 登录（已注册且已确认的邮箱，最常用、最快）
      2. 失败则用 service_role 直接创建"已确认"账号：
         - 创建成功（新邮箱）→ 用刚设置的密码直接登录（省一次往返）
         - 报"已存在"（如未确认邮箱）→ 才回查 ID 标记为已确认
      3. 兜底：再次 magiclink 登录

    性能说明（关闭自助注册后的关键改动）：
      Supabase 关闭 "Allow new users to sign up" 后，admin/generate_link
      不再为陌生邮箱隐式建号，第一步必然失败。因此这里刻意避免
      "遍历整个用户表找 ID"（用户多时极慢），改为直接创建、
      仅在冲突这一极少分支才回查。

    需要 Streamlit Secrets 配置 SUPABASE_SERVICE_ROLE_KEY。

    Returns:
        True 表示登录成功，False 表示失败（错误信息在 st.session_state.auth_error）
    """
    import secrets as _secrets

    try:
        url = _get_supabase_url()
        service_key = _get_supabase_service_key()
        if not url or not service_key:
            st.session_state.auth_error = "自动登录失败：未配置 SUPABASE_SERVICE_ROLE_KEY，请在 Streamlit Secrets 中添加"
            return False

        admin = create_client(url, service_key)
        anon = get_anon_client()

        # 第一步：直接尝试登录（已注册且已确认的邮箱）
        otp = _generate_magiclink_otp(url, service_key, email)
        if otp and _verify_otp_login(anon, email, otp):
            return True

        # 第二步：确保用户存在且已确认（未注册邮箱也能登录）
        # 优化：不再先遍历用户表找 ID（用户量大时很慢）。
        # 直接用 service_role 创建"已确认"账号：
        #   - 成功（新邮箱）→ 用刚设置的密码一步登录；
        #   - 抛"已存在" → 该邮箱其实已注册（第一步失败多因未确认），
        #     此时才回查 ID 并标记已确认（极少触发）。
        pwd = _secrets.token_urlsafe(16)
        created_new = False
        try:
            admin.auth.admin.create_user({
                "email": email,
                "password": pwd,
                "email_confirm": True,
            })
            created_new = True
        except Exception as ce:
            uid = _find_user_id(url, service_key, email)
            if uid:
                admin.auth.admin.update_user_by_id(uid, {"email_confirm": True})
            else:
                raise ce

        # 新账号：用刚设置的密码直接登录（比再生成/校验令牌少一次往返）
        if created_new and _password_login(anon, email, pwd):
            return True

        # 第三步：再次尝试登录
        otp = _generate_magiclink_otp(url, service_key, email)
        if otp and _verify_otp_login(anon, email, otp):
            return True

        st.session_state.auth_error = "自动登录失败：无法获取登录令牌"
        return False
    except Exception as e:
        st.session_state.auth_error = f"自动登录失败: {e}"
        return False


# ==================== 登录页面渲染 ====================

def _render_portal_only_hint():
    """渲染"仅从个人工作站进入"引导页（不提供任何注册/登录表单）"""
    st.markdown(
        """
        <div style="text-align:center; padding:2.5rem 0;">
            <div style="font-size:3rem; margin-bottom:.5rem;">🚪</div>
            <h3>本系统仅支持从个人工作站进入</h3>
            <p style="color:#555; line-height:1.8;">
                请在 <b>个人工作站</b> 的应用列表中找到「质量管理系统」并点击进入，
                系统将自动完成免密登录，无需输入账号密码，也无需注册。
            </p>
            <p style="color:#999; font-size:.85rem; margin-top:1rem;">
                直接打开本页面无法登录。如确有使用需要，请联系管理员在个人工作站中为你开通入口。
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_auth_page():
    """渲染登录页 —— 仅支持从个人工作站跳转进入

    安全策略 v2（已锁死自助注册）：
      - 页面不提供任何"邮箱 + 密码"登录表单或注册表单；
      - 无跳转参数（直接访问）时，仅显示"请从个人工作站进入"的引导页；
      - 唯一登录入口：个人工作站跳转携带 ?email=xxx，
        触发下方免密静默登录（sso_login），未注册邮箱会自动创建账号。

    所有需要认证的页面调用前，先由 login_required 守卫检查，
    未登录时跳转到此页面。
    """
    st.set_page_config(
        page_title="QMS 登录",
        page_icon="🔐",
        layout="centered",
        initial_sidebar_state="collapsed",
    )

    # 隐藏 sidebar + 隐藏右上角工具栏 + 隐藏右下角 Made with Streamlit
    st.markdown("""
        <style>
        [data-testid="stSidebar"] { display: none; }
        [data-testid="stToolbar"] { display: none !important; }
        footer { visibility: hidden; }
        [data-testid="manage-app-button"] { display: none !important; }
        </style>
    """, unsafe_allow_html=True)

    st.title("🔐 质量管理系统 QMS")
    st.caption("Quality Management System — 请从个人工作站进入")

    # 个人工作站跳转参数：URL 需携带 ?email=xxx（配了共享密钥后还需 ts + sig 签名）
    try:
        url_email = st.query_params.get("email", "")
        url_ts = st.query_params.get("ts", "")
        url_sig = st.query_params.get("sig", "")
    except Exception:
        url_email = ""
        url_ts = ""
        url_sig = ""

    # 无跳转参数 → 不渲染任何表单，只提示从个人工作站进入
    if not url_email:
        _render_portal_only_hint()
        return

    # 签名校验：配置 WORKSTATION_SSO_SECRET 后强制生效；未配置则为兼容模式，
    # 验签失败一律拒绝，且不建号、不登录。
    _sig_ok, _sig_msg = verify_workstation_signature(str(url_email), str(url_ts), str(url_sig))
    if not _sig_ok:
        st.error(f"⚠️ {_sig_msg}")
        _render_portal_only_hint()
        return

    # ---- 已带 email 参数：触发免密静默登录 ----
    st.session_state["login_email"] = str(url_email)
    if not st.session_state.get("sso_attempted"):
        st.session_state["sso_attempted"] = True
        st.session_state.auth_error = None
        with st.spinner("正在通过个人工作站登录..."):
            _ok = sso_login(str(url_email))
        if _ok:
            # 登录成功：清理 URL 参数，避免刷新重复触发
            try:
                st.query_params.clear()
            except Exception:
                pass
            st.rerun()

    # 登录失败：给出明确提示 + 重试入口
    st.warning("⚠️ 未能自动登录，请确认你是通过个人工作站中的入口进入本系统。")
    err = st.session_state.get("auth_error")
    if err:
        st.error(err)
    st.info("若多次重试仍失败，请联系管理员确认该邮箱是否已在个人工作站开通本系统入口。")
    if st.button("🔄 重新尝试登录", type="primary", use_container_width=True):
        st.session_state["sso_attempted"] = False
        st.rerun()


# ==================== 登录守卫 ====================

def login_required() -> bool:
    """
    守卫函数：确保只有登录用户才能访问页面
    
    用法：
        if not auth.login_required():
            st.stop()
        # ... 页面逻辑 ...

    由于 Streamlit 没有路由中间件，需要在每个页面渲染前调用此守卫。
    未登录时渲染登录页并返回 False。
    
    Returns:
        True 表示已登录，可继续；False 表示未登录，已渲染登录页
    """
    init_auth_session()
    
    if not st.session_state.authenticated:
        render_auth_page()
        return False
    
    return True


def render_user_bar():
    """在侧边栏顶部渲染用户信息栏（登录状态下调用）"""
    email = get_user_email()
    if not email:
        return

    st.sidebar.markdown("---")
    st.sidebar.markdown(f"👤 {email}")

    if st.sidebar.button("🚪 登出", use_container_width=True):
        logout()
        st.rerun()
