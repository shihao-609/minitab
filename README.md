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

工作日（周一至周五）凌晨自动把「未检验清单」通过邮件发给配置的收件人。

- 触发方式：GitHub Actions 定时任务（`.github/workflows/daily_unchecked_report.yml`）
- 脚本：`scripts/daily_unchecked_report.py`（独立运行，复用检验对比逻辑）
- 无未检验记录时不会发邮件
- Excel 附件含两个 sheet：`未检验清单`（含"新增/持续"状态列）、`变动摘要`（昨日 vs 今日对比）

### 配置 GitHub Secrets

进入 GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**，逐项添加：

| Secret 名称 | 说明 | 示例 |
|---|---|---|
| `SUPABASE_URL` | Supabase 项目地址 | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | 服务端密钥（Supabase → Settings → API） | `eyJ...` |
| `SMTP_USER` | 发件邮箱（QQ 邮箱） | `123456@qq.com` |
| `SMTP_PASS` | 发件邮箱**授权码**（非登录密码） | `abcdefghijklmnop` |
| `REPORT_RECIPIENTS` | 收件人邮箱，**多个用英文逗号分隔** | `a@qq.com,b@163.com` |

### QQ 邮箱授权码获取

1. 登录 QQ 邮箱 → 设置 → 账号
2. 找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务」
3. 开启「IMAP/SMTP 服务」，按提示发送短信验证
4. 生成 16 位**授权码**，填入 `SMTP_PASS`

### 收件人修改

编辑 `REPORT_RECIPIENTS` 这个 Secret，用英文逗号分隔多个邮箱即可，无需改代码。

### 手动触发测试

GitHub 仓库 → **Actions** → 左侧「每日未检验清单邮件」→ **Run workflow** → 点绿色按钮，即可立即发送一封测试邮件。
