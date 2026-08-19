"""
认证模块 — 基于 Supabase Auth 的登录/注册/用户管理
=====================================================
功能:
  - 邮箱 + 密码 注册/登录
  - JWT 令牌管理（session 生命周期内有效）
  - 登出
  - 用户信息获取

关键设计（解决"额外注意项"）:
  1. 登录后使用用户的 JWT 创建 Supabase 客户端，替代 anon key
     这样 Supabase RLS 策略才能正确识别当前用户并隔离数据
  2. 每个页面渲染时通过 st.session_state 验证登录状态，
     解决 Streamlit 无内置路由/中间件的问题
"""

import streamlit as st
import os
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


def get_anon_client() -> Optional[Client]:
    """获取使用 anon key 的客户端（仅用于注册/登录，无权读写受 RLS 保护的数据）"""
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
        "auth_error": None,     # 登录/注册时的错误信息
        "auth_mode": "login",   # "login" | "register"
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
    
    与以往的 anon key 客户端不同：
    - anon key 客户端：匿名访问，RLS 策略无法识别用户
    - JWT 客户端：携带用户身份，RLS 策略可正确过滤数据
    
    每次调用都重新创建，因为 JWT 可能会过期；
    如果 JWT 过期，Supabase 会自动 refresh。
    """
    jwt = get_user_jwt()
    if not jwt:
        return None
    url = _get_supabase_url()
    if not url:
        return None
    return create_client(url, jwt)


# ==================== 认证操作 ====================

def login(email: str, password: str) -> bool:
    """邮箱登录
    
    Returns:
        True 表示登录成功，False 表示失败（错误信息在 st.session_state.auth_error）
    """
    client = get_anon_client()
    if client is None:
        st.session_state.auth_error = "Supabase 配置未设置，请检查 SUPABASE_URL 和 SUPABASE_ANON_KEY"
        return False

    try:
        response = client.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })
        st.session_state.authenticated = True
        st.session_state.user = response.user
        st.session_state.session = response.session
        st.session_state.auth_error = None
        return True
    except Exception as e:
        msg = str(e)
        if "Invalid login credentials" in msg:
            st.session_state.auth_error = "邮箱或密码错误"
        elif "Email not confirmed" in msg:
            st.session_state.auth_error = "邮箱未验证，请先验证邮箱"
        else:
            st.session_state.auth_error = f"登录失败: {msg}"
        return False


def register(email: str, password: str) -> bool:
    """邮箱注册
    
    注：默认不要求邮箱验证，如需验证请在 Supabase Dashboard 中开启。
    
    Returns:
        True 表示注册成功并自动登录，False 表示失败
    """
    client = get_anon_client()
    if client is None:
        st.session_state.auth_error = "Supabase 配置未设置，请检查 SUPABASE_URL 和 SUPABASE_ANON_KEY"
        return False

    if len(password) < 6:
        st.session_state.auth_error = "密码至少需要 6 个字符"
        return False

    try:
        response = client.auth.sign_up({
            "email": email,
            "password": password,
        })
        
        # Supabase sign_up 可能返回 user 也可能需要邮箱验证
        if response.user:
            # 如果开启了邮箱确认，user 存在但 session 为 None
            if response.session:
                st.session_state.authenticated = True
                st.session_state.user = response.user
                st.session_state.session = response.session
                st.session_state.auth_error = None
                return True
            else:
                st.session_state.auth_error = "注册成功！请在邮箱中确认验证链接后登录。"
                return False
        else:
            st.session_state.auth_error = "注册失败，请重试"
            return False
    except Exception as e:
        msg = str(e)
        if "already registered" in msg.lower() or "already exists" in msg.lower():
            st.session_state.auth_error = "该邮箱已注册，请直接登录"
        else:
            st.session_state.auth_error = f"注册失败: {msg}"
        return False


def logout():
    """登出：清除所有认证状态（不调用 Supabase sign_out，避免网络依赖）"""
    st.session_state.authenticated = False
    st.session_state.user = None
    st.session_state.session = None
    st.session_state.auth_error = None
    
    # 清除与用户相关的数据
    keys_to_clear = ["user_data", "saved_data"]
    for key in keys_to_clear:
        if key in st.session_state:
            del st.session_state[key]


# ==================== 登录页面渲染 ====================

def render_auth_page():
    """渲染登录/注册页面
    
    所有需要认证的页面调用前，先由 login_required 装饰器检查，
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
    st.caption("Quality Management System — 请登录后使用")

    # 支持从知识库跳转自动填充邮箱（URL 携带 ?email=xxx@qq.com）
    try:
        url_email = st.query_params.get("email", "")
    except Exception:
        url_email = ""
    if url_email and "login_email" not in st.session_state:
        st.session_state["login_email"] = str(url_email)

    mode = st.session_state.get("auth_mode", "login")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if mode == "login":
            st.subheader("登录")
            email = st.text_input("邮箱", placeholder="your@email.com", key="login_email")
            password = st.text_input("密码", type="password", placeholder="输入密码", key="login_password")

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("登录", type="primary", use_container_width=True):
                    if not email or not password:
                        st.error("请输入邮箱和密码")
                    else:
                        if login(email, password):
                            st.rerun()
                        else:
                            st.error(st.session_state.auth_error)

            with btn_col2:
                if st.button("没有账号？注册", use_container_width=True):
                    st.session_state.auth_mode = "register"
                    st.session_state.auth_error = None
                    st.rerun()

        else:  # register
            st.subheader("注册")
            email = st.text_input("邮箱", placeholder="your@email.com", key="reg_email")
            password = st.text_input("密码", type="password", placeholder="至少6个字符", key="reg_password")
            password2 = st.text_input("确认密码", type="password", placeholder="再次输入密码", key="reg_password2")

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("注册", type="primary", use_container_width=True):
                    if not email or not password:
                        st.error("请填写所有字段")
                    elif password != password2:
                        st.error("两次密码输入不一致")
                    else:
                        if register(email, password):
                            st.rerun()
                        else:
                            st.error(st.session_state.auth_error)

            with btn_col2:
                if st.button("已有账号？登录", use_container_width=True):
                    st.session_state.auth_mode = "login"
                    st.session_state.auth_error = None
                    st.rerun()

    # 显示错误
    err = st.session_state.get("auth_error")
    if err:
        st.error(err)
        st.session_state.auth_error = None


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
