# 账号密码功能配置指南

## 步骤 1：在 Supabase 中开启 Email Auth

1. 进入 [Supabase Dashboard](https://app.supabase.com)
2. 选择你的项目 → **Authentication** → **Providers**
3. 找到 **Email** provider，确保已启用（默认启用）
4. **建议关闭邮箱验证**（开发阶段）：Settings → 关闭 "Confirm email"  
   如开启验证，用户注册后会收到确认邮件

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

**不需要配置 SUPABASE_SERVICE_ROLE_KEY**，不需要 service_role 密钥。

## 步骤 4：启动应用

```bash
streamlit run app.py
```

首次访问会显示登录页 → 点击"没有账号？注册"创建账号 → 登录后即可正常使用。

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
