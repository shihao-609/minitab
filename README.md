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
