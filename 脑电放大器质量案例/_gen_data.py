# -*- coding: utf-8 -*-
"""脑电放大器质量问题案例数据生成脚本（生成后可删除）"""
import os
import numpy as np
import pandas as pd
from scipy.stats import weibull_min

OUT = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 案例：EEG-3200 型 32 导数字脑电图机 —— 等效输入噪声超标 + 工频干扰
# 企业技术要求：等效输入噪声 ≤ 1.0 μVp-p（内控），目标值 0.70
# ============================================================

# ---------- 01 SPC / 过程能力：等效输入噪声（25 子组 × 5 = 125 台） ----------
np.random.seed(20260801)
vals = []
for g in range(25):
    if g < 12:
        mu, sd = 0.72, 0.055
    else:
        # 第 13 子组起：选择焊锡机温度漂移 + 供应商B运放批次变更，均值渐升
        mu, sd = 0.72 + (g - 11) * 0.022, 0.068
    vals.extend(np.random.normal(mu, sd, 5))
vals = np.round(np.array(vals), 3)
pd.DataFrame({"等效输入噪声_uVp-p": vals}).to_csv(
    os.path.join(OUT, "01_噪声SPC与能力分析_125台.csv"),
    index=False, encoding="utf-8-sig")

# ---------- 02 帕累托：成品检验不良类型统计（近 30 天） ----------
pd.DataFrame({
    "不良类型": ["等效输入噪声超标", "工频干扰(CMRR不合格)", "基线漂移超限",
                "电极接触阻抗异常", "导联间串扰超标", "频响超差",
                "灵敏度误差超差", "外观标识不良"],
    "数量":     [47, 38, 22, 12, 15, 9, 7, 5],
}).to_csv(os.path.join(OUT, "02_不良类型帕累托.csv"),
          index=False, encoding="utf-8-sig")

# ---------- 03 计量型 Gage R&R：噪声测试台（10 台样机 × 3 检验员 × 2 次） ----------
np.random.seed(20260802)
true_vals = [0.55, 0.62, 0.68, 0.74, 0.80, 0.85, 0.90, 0.96, 1.02, 1.08]
op_bias = {"检验员A": 0.000, "检验员B": 0.035, "检验员C": -0.020}
rows = []
for p_id, tv in enumerate(true_vals, 1):
    for op, bias in op_bias.items():
        for _ in range(2):
            # 重复性标准差 0.045 偏大（测试夹具弹片疲劳 → 接触压力不一致）
            m = tv + bias + np.random.normal(0, 0.045)
            rows.append({"Part": p_id, "Operator": op,
                         "Measurement": round(m, 3)})
pd.DataFrame(rows).to_csv(
    os.path.join(OUT, "03_噪声测试台_GageRR.csv"),
    index=False, encoding="utf-8-sig")

# ---------- 04 FMEA：脑电放大器失效模式风险评估 ----------
pd.DataFrame({
    "模式": ["等效输入噪声超标(波形伪差)",
             "共模抑制比低导致50Hz工频干扰",
             "电极输入阻抗偏低/导联脱焊",
             "屏蔽外壳接地不良",
             "基线漂移超限",
             "导联间串扰超标",
             "低噪声运放芯片批次失效",
             "滤波频响参数漂移"],
    "严重度": [8, 7, 9, 7, 5, 6, 9, 5],
    "发生度": [7, 6, 4, 6, 7, 4, 3, 4],
    "探测度": [5, 4, 6, 8, 3, 5, 8, 4],
}).to_csv(os.path.join(OUT, "04_脑电放大器_FMEA.csv"),
          index=False, encoding="utf-8-sig")

# ---------- 05 Weibull：现场返修失效时间（天，装机后跟踪） ----------
np.random.seed(20260803)
t_main = 420 * weibull_min.rvs(2.3, size=42)      # 磨损失效
t_early = 60 * weibull_min.rvs(0.9, size=6)       # 早期失效（焊接/装配缺陷）
times = np.round(np.concatenate([t_main, t_early]), 1)
pd.DataFrame({"失效时间_天": times}).to_csv(
    os.path.join(OUT, "05_现场返修_Weibull.csv"),
    index=False, encoding="utf-8-sig")

# ---------- 06 工艺参数 + 噪声（相关性/回归/T²/箱线图/ANOVA） ----------
np.random.seed(20260804)
n = 60
z_t = np.random.normal(0, 1, n)
z_h = np.random.normal(0, 1, n)
z_r = np.random.normal(0, 1, n)
z_g = np.random.normal(0, 1, n)
焊接温度 = np.round(340 + 10 * z_t, 1)
环境湿度 = np.round(np.clip(45 + 8 * z_h, 25, 65), 1)
接地电阻 = np.round(np.clip(25 + 8 * z_r, 8, 48), 1)
屏蔽间隙 = np.round(np.clip(0.30 + 0.10 * z_g, 0.08, 0.55), 3)
产线 = np.where(np.arange(n) % 2 == 0, "A线", "B线")
芯片供应商 = np.random.choice(["华芯微", "微邦半导体", "海外N社"], n,
                              p=[0.5, 0.3, 0.2])
line_eff = np.where(产线 == "B线", 0.07, 0.0)
sup_eff = np.where(芯片供应商 == "微邦半导体", 0.09, 0.0)
噪声 = (0.76
        - 0.045 * z_t          # 温度偏低→虚焊→噪声升高
        - 0.038 * z_h          # 湿度偏低→静电→噪声升高
        + 0.085 * z_r          # 接地电阻越大→噪声越高
        + 0.065 * z_g          # 屏蔽间隙越大→屏蔽越差→噪声越高
        + line_eff + sup_eff
        + np.random.normal(0, 0.035, n))
pd.DataFrame({
    "序列号": [f"EEG{2608001+i}" for i in range(n)],
    "焊接温度_℃": 焊接温度,
    "环境湿度_pctRH": 环境湿度,
    "接地电阻_mΩ": 接地电阻,
    "屏蔽压接间隙_mm": 屏蔽间隙,
    "产线": 产线,
    "芯片供应商": 芯片供应商,
    "等效输入噪声_uVp-p": np.round(噪声, 3),
}).to_csv(os.path.join(OUT, "06_工艺参数与噪声_60台.csv"),
          index=False, encoding="utf-8-sig")

# ---------- 07 DOE：屏蔽接地工艺 2^3 全因子（2 次重复 = 16 次） ----------
np.random.seed(20260805)
rows = []
for A in [-1, 1]:          # 焊接温度 320℃ / 360℃
    for B in [-1, 1]:      # 焊接时间 1.5s / 3.0s
        for C in [-1, 1]:  # 压接压力 低 / 高
            for _ in range(2):
                y = (0.80 - 0.085*A - 0.050*B + 0.018*C
                     + 0.060*A*B + np.random.normal(0, 0.028))
                rows.append({"A_焊接温度": A, "B_焊接时间": B,
                             "C_压接压力": C,
                             "响应_噪声_uVp-p": round(y, 3)})
pd.DataFrame(rows).to_csv(
    os.path.join(OUT, "07_DOE屏蔽接地工艺.csv"),
    index=False, encoding="utf-8-sig")

# ---------- 08 P 图：连续 24 个交验批的不合格台数 ----------
np.random.seed(20260806)
rows = []
for b in range(24):
    size = int(np.random.randint(80, 121))
    p = 0.022 if b < 10 else 0.022 + (b - 9) * 0.006
    defect = int(np.random.binomial(size, min(p, 0.20)))
    rows.append({"批次号": f"P2026{b+1:03d}",
                 "交验台数": size, "不合格台数": defect})
pd.DataFrame(rows).to_csv(
    os.path.join(OUT, "08_批次不合格率_P图.csv"),
    index=False, encoding="utf-8-sig")

for f in sorted(os.listdir(OUT)):
    if f.endswith(".csv"):
        p = os.path.join(OUT, f)
        print(f"{f}  ({os.path.getsize(p)} bytes)")
