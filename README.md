# 质量管理系统 QMS v2.0

基于 Streamlit 的类 Minitab 质量管理 Web 应用。

## 功能模块

### 📈 SPC 控制图
- 休哈特七图 (X-bar R / X-bar S / I-MR / P / NP / C / U)
- EWMA 控制图 (指数加权移动平均)
- CUSUM 控制图 (累积和)
- 多变量 Hotelling T² 控制图

### 🎯 过程能力分析
- Cp / Cpk / Pp / Ppk 能力指数
- Cg / Cgk 检具能力 (MSA Type 1)

### 📊 质量图形工具
- 帕累托图 (Pareto)
- 直方图 (含正态拟合)
- 箱线图 (Box Plot)
- 运行图 (Run Chart + 游程检验)
- 鱼骨图 (石川图 / Cause & Effect)

### 🔬 测量系统分析 MSA
- 计量型 Gage R&R (交叉型 平均值-极差法)
- 计数型 Gage R&R (Kappa 属性一致性)
- 测量不确定度评定 (GUM)

### 🔢 统计推断
- 正态性检验 (Shapiro-Wilk / Anderson-Darling / D'Agostino)
- 假设检验 (t 检验 / ANOVA / 等方差检验)
- 回归分析 (一元 & 多元线性回归)
- 相关性矩阵 (热力图)

### 🧪 高级分析
- DOE 试验设计 (全因子)
- Weibull 可靠性分析
- 抽样方案 (OC 曲线 / AQL / LTPD)
- FMEA 失效模式分析

## 本地运行

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 📮 每日未检验清单自动邮件

自动把「未检验清单」通过邮件发给配置的收件人。**发送日期与时间可在前端设置**（周一至周五可选任意几天 + 北京时间发送时刻），无需改代码。

- 触发方式：GitHub Actions 每小时触发一次（`.github/workflows/daily_unchecked_report.yml`），脚本按前端配置的排程判断当天/当刻是否发送
- 脚本：`scripts/daily_unchecked_report.py`（独立运行，复用检验对比逻辑）
- 发送时间配置：应用「🔍 质量管理 → 📮 邮件收件人」页 → **⏰ 发送时间设置**，勾选周一~周五任意几天并设置时间（北京时间，误差约 1 小时内），团队共享、立即生效
- 防重复：同一天只会发送一次（记录 `report_schedule.last_sent_date`）
- 未配置排程表时回退旧行为：周一至周五发送
- 无未检验记录时不会发邮件
- Excel 附件含两个 sheet：`未检验清单`（含"新增/持续"状态列 + 采购员列）、`变动摘要`（昨日 vs 今日对比）

### 配置 GitHub Secrets

进入 GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**，逐项添加：

| Secret 名称 | 说明 | 示例 |
|---|---|---|
| `SUPABASE_URL` | Supabase 项目地址 | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | 服务端密钥（Supabase → Settings → API） | `eyJ...` |
| `SMTP_USER` | 发件邮箱（QQ 邮箱） | `123456@qq.com` |
| `SMTP_PASS` | 发件邮箱**授权码**（非登录密码） | `abcdefghijklmnop` |
| `REPORT_RECIPIENTS` | （可选兜底）收件人邮箱，多个用英文逗号分隔；配置了前端收件人后无需此 Secret | `a@qq.com,b@163.com` |

### QQ 邮箱授权码获取

1. 登录 QQ 邮箱 → 设置 → 账号
2. 找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务」
3. 开启「IMAP/SMTP 服务」，按提示发送短信验证
4. 生成 16 位**授权码**，填入 `SMTP_PASS`

### 收件人修改（推荐在前端操作）

应用「数据导入」页 → **📮 邮件收件人** tab，登录后可直接添加/删除收件人邮箱，团队共享，立即生效，**无需改代码或 GitHub 配置**。

> 需先在 Supabase SQL Editor 执行一次「检验记录库」页面给出的完整 SQL（含 `report_recipients` 表），或直接在 SQL Editor 执行完整建表脚本后刷新页面。

### 手动触发测试

GitHub 仓库 → **Actions** → 左侧「每日未检验清单邮件」→ **Run workflow** → 点绿色按钮，即可立即发送一封测试邮件。

## 🔋 Supabase 保活（防睡眠）

Supabase 免费版约 7 天无活动会暂停项目，且 Streamlit 社区版应用休眠后首次打开会冷启动（变慢）。仓库内置保活机制：

- Workflow：`.github/workflows/keep_alive.yml`（每 3 小时执行一次）
- 脚本：`keep_alive.py`（ping `inspection_submissions` 等业务表 + REST 兜底 + 唤醒 Streamlit 应用）

> ⚠️ 若发现"防睡眠失效"（应用经常冷启动/打开慢），请检查：
> 1. GitHub 仓库 → **Actions** 页左侧是否有「Supabase Keep-Alive」工作流；
> 2. 若显示被禁用（Disable），点击启用；GitHub 会在仓库 60 天无活动时自动禁用定时工作流，重新 commit/push 一次即可恢复；
> 3. 检查运行日志是否有红色失败（如缺少 `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` Secret）；
> 4. `keep_alive.py` 已支持回退使用 `SUPABASE_SERVICE_ROLE_KEY`（邮件流程已配置，通常更可靠）。
