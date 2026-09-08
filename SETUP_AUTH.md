# 认证 / 登录配置指南

> **当前认证策略（安全策略 v2，已锁死自助注册）**
> 系统已彻底关闭"注册"与"邮箱 + 密码登录"，页面不再提供任何注册/登录表单。
> 唯一入口：**从个人工作站跳转**进入本系统（URL 携带 `?email=xxx`），后端完成
> 免密静默登录；该邮箱不存在时自动创建账号，用户无需也无法自助注册。
> 因此下文只涉及让这套免密流程跑通的 Supabase / Secrets 配置。

## 步骤 1：在 Supabase 中开启 Email Auth

1. 进入 [Supabase Dashboard](https://app.supabase.com)
2. 选择你的项目 → **Authentication** → **Providers**
3. 找到 **Email** provider，确保已启用（默认启用）
4. **建议关闭邮箱验证**（Confirm email）：免密登录流程会在后台自动将账号标记为已确认，
   无需邮件验证环节
5. **建议关闭匿名注册（双保险）**：Authentication → Providers → Email，
   关闭 **Allow new users to sign up**。应用层已无注册入口，再关闭此开关可防止
   绕过页面直接调用 Supabase 的 sign_up 接口自建账号。
   注意：个人工作站跳转的自动建号走 service_role 管理接口，**不受此开关限制**，
   可正常为首次进入的人创建账号。

## 步骤 2：执行数据库迁移 SQL

1. Supabase Dashboard → **SQL Editor** → 新建查询
2. 粘贴 `migration.sql` 的全部内容（本目录下）
3. 点击 **Run** 执行

### 迁移内容说明：
- `datasets` 和 `fishbone_configs` 表增加 `user_id` 字段（UUID）
- 启用 Row-Level Security (RLS)
- 创建策略：每个用户只能读写自己的数据
- 旧数据（user_id = NULL）在 RLS 启用后将不可见，迁移 SQL 中有处理选项

## 步骤 3：配置环境变量

### 本地开发（.env 文件）
确保 `.env` 文件中已配置：
```
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOi...（你的 anon/public key）
```

### Streamlit Cloud 部署
在 Streamlit Cloud 的 App Settings → Secrets 中添加：
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`

**需要配置 SUPABASE_SERVICE_ROLE_KEY**（service_role 密钥）：
免密静默登录依赖它后台生成登录令牌/自动创建账号，务必妥善保管、只放服务端。

## 步骤 4：启动应用

```bash
streamlit run app.py
```

首次访问会显示登录页：直接打开（URL 无 `?email=` 参数）只会看到"请从个人工作站进入"
引导页，无法注册也无法输入密码；从个人工作站点击进入（携带 `?email=xxx`）会自动完成
免密登录，无需任何人工操作。已登录状态下再进入即可直接使用。

## 额外注意项检查清单

### ✅ ① Streamlit 无路由中间件
- **问题**：Streamlit 脚本每次 rerun 从头执行，无内置路由
- **解决**：`app.py` 顶部调用 `auth.login_required()`，未登录时渲染登录页 + `st.stop()` 阻止后续代码

### ✅ ② JWT 替代 anon key
- **问题**：anon key 客户端不受 RLS 限制，RLS 策略无法区分用户
- **解决**：`supabase_helper._get_client()` 用 anon key 创建客户端后，调用 `client.auth.set_session()` 注入用户 session，SDK 自动在请求头中附加 JWT

### ✅ ③ Streamlit Cloud Secrets
- 需要在 Streamlit Cloud 的 Secrets 中配置 `SUPABASE_URL` 和 `SUPABASE_ANON_KEY`
- 不需要 service_role key

### 🟡 已知限制
- **JWT 有效期**：默认 1 小时，SDK 通过 refresh_token 自动续期
- **会话持久**：浏览器刷新后需重新登录（Streamlit 无 cookie 机制）
- **登出不调 API**：只清除本地 session_state，不主动调 Supabase sign_out（避免网络依赖）
