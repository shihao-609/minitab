"""
质量管理系统 (Quality Management System) v2.0
=============================================
一个类 Minitab 的质量管理 Web 应用
功能模块：
  1. SPC 控制图 (休哈特七图 / EWMA / CUSUM / 多变量 T²)
  2. 过程能力分析 (Cp/Cpk/Pp/Ppk / Cg/Cgk 检具能力)
  3. 质量图形工具 (帕累托 / 直方图 / 箱线图 / 运行图 / 鱼骨图)
  4. 测量系统分析 MSA (计量型GRR / 计数型GRR / 测量不确定度)
  5. 统计推断 (正态性检验 / 假设检验 / 回归 / 相关性)
  6. 高级分析 (DOE / Weibull 可靠性 / 抽样方案 / FMEA)
"""

import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import time
import textwrap
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv

from modules import spc_charts, capability, pareto_histogram, gage_rr, supabase_helper
from modules import spc_advanced, msa_advanced, stats_tools, quality_tools, advanced_analysis
from modules import auth, batch_analysis, inspection_match, supplier_normalize

load_dotenv()

# 注：页面初始化（登录守卫、set_page_config、侧边栏）已移入 main() 中，
#     使所有渲染阶段异常都能被同一 try/except 捕获，避免 Cloud 白屏。


# ==================== 工具函数 ====================
def check_data():
    if 'user_data' not in st.session_state or st.session_state.user_data is None:
        return None
    return st.session_state.user_data

# 新数据加载（上传/示例/云端）时调用，重置保存基线
def set_new_data(df):
    st.session_state.user_data = df
    st.session_state.saved_data = None  # 重置基线，show_data_info 会自动重新初始化

def parse_uploaded_file(uploaded_file):
    """解析上传的 CSV/Excel 文件，返回 DataFrame 或 None"""
    if uploaded_file.name.endswith('.csv'):
        raw_bytes = uploaded_file.getvalue()
        df = None
        for enc in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
            try:
                from io import BytesIO
                df = pd.read_csv(BytesIO(raw_bytes), encoding=enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if df is None:
            st.error('无法识别文件编码，请将文件另存为 UTF-8 格式')
            return None
        st.success(f'✅ 编码: {enc} · {df.shape[0]} 行 × {df.shape[1]} 列')
        return df
    else:
        df = pd.read_excel(uploaded_file)
        st.success(f'✅ 成功加载: {df.shape[0]} 行 × {df.shape[1]} 列')
        return df

@st.dialog('✏️ 修改列名')
def rename_column_dialog(df):
    cols = list(df.columns)
    col_to_rename = st.selectbox('选择要修改的列', cols, key='rename_col_select')
    new_name = st.text_input('新列名', value=col_to_rename, key='rename_col_input')
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button('✅ 确认', use_container_width=True):
            if new_name.strip() and new_name.strip() != col_to_rename:
                if new_name.strip() in [c for c in cols if c != col_to_rename]:
                    st.error('列名已存在，请换一个')
                else:
                    rename_map = {col_to_rename: new_name.strip()}
                    st.session_state.user_data = st.session_state.user_data.rename(columns=rename_map)
                    st.rerun()
            else:
                st.rerun()
    with c2:
        if st.button('❌ 取消', use_container_width=True):
            st.rerun()

@st.dialog('📤 上传文件')
def upload_file_dialog():
    """弹窗上传 CSV/Excel，上传后替换当前数据"""
    uploaded_file = st.file_uploader('选择 CSV 或 Excel 文件', type=['csv', 'xlsx', 'xls'])
    if uploaded_file:
        try:
            df = parse_uploaded_file(uploaded_file)
            if df is not None:
                set_new_data(df)
                st.success(f'✅ 已替换数据: {df.shape[0]} 行 × {df.shape[1]} 列')
                if st.button('确定', use_container_width=True, key='upload_dialog_ok'):
                    st.rerun()
        except Exception as e:
            st.error(f'加载失败: {e}')


def show_data_info():
    if 'user_data' in st.session_state and st.session_state.user_data is not None:
        # 首次加载时初始化 saved_data（基线）
        if 'saved_data' not in st.session_state or st.session_state.saved_data is None:
            st.session_state.saved_data = st.session_state.user_data.copy()

        df = st.session_state.user_data
        with st.expander('📋 当前数据预览', expanded=False):
            # ===== 列名编辑 + 弹窗 =====
            if st.session_state.get('rename_dialog_open'):
                rename_column_dialog(df)
                st.session_state.rename_dialog_open = False

            col_edit_btn, col_upload_btn, _ = st.columns([2, 2, 8])
            with col_edit_btn:
                if st.button('✏️ 修改列名', key='show_rename_dialog', use_container_width=True):
                    st.session_state.rename_dialog_open = True
                    st.rerun()
            with col_upload_btn:
                if st.button('📤 上传文件', key='show_upload_dialog', use_container_width=True):
                    st.session_state.upload_dialog_open = True
                    st.rerun()

            # ===== 上传文件弹窗 =====
            if st.session_state.get('upload_dialog_open'):
                upload_file_dialog()
                st.session_state.upload_dialog_open = False

            # ===== 数据编辑器（可直接修改单元格） =====
            edited = st.data_editor(df, use_container_width=True,
                                    num_rows='dynamic', hide_index=True,
                                    key='live_data_edit')

            # ★ 关键：自动同步编辑器修改到 session，各分析模块实时生效
            st.session_state.user_data = edited.reset_index(drop=True)

            st.caption(f'{edited.shape[0]} 行 × {edited.shape[1]} 列')

            # ===== 操作按钮行 =====
            btn1, btn2, btn3, btn4, btn5, btn6 = st.columns([1.1, 1.1, 0.9, 0.9, 0.9, 0.9])

            with btn1:
                if st.button('💾 保存修改', key='save_data', use_container_width=True):
                    st.session_state.saved_data = st.session_state.user_data.copy()
                    st.toast('✅ 已保存')

            with btn2:
                if st.button('🔄 恢复原样', key='reset_data', use_container_width=True):
                    if 'saved_data' in st.session_state and st.session_state.saved_data is not None:
                        st.session_state.user_data = st.session_state.saved_data.copy()
                        st.success('已恢复到上次保存的状态')
                    else:
                        st.warning('没有可恢复的基线数据')
                    st.rerun()

            with btn3:
                download_csv = edited.to_csv(index=False).encode('utf-8-sig')
                st.download_button('💾 下载', download_csv,
                                   'qms_data.csv', 'text/csv',
                                   use_container_width=True)

            with btn4:
                if st.button('🗑️ 删行', key='del_rows_btn', use_container_width=True):
                    st.session_state.show_del_rows = True
            with btn5:
                if st.button('🗑️ 删列', key='del_cols_btn', use_container_width=True):
                    st.session_state.show_del_cols = True
            with btn6:
                if st.button('➕ 加列', key='add_col_btn', use_container_width=True):
                    st.session_state.show_add_col = True

            # ===== 添加列功能 =====
            if st.session_state.get('show_add_col'):
                st.divider()
                st.caption('**➕ 添加新列** — 新列会以空值填充所有行')
                c1, c2, c3 = st.columns([3, 1.5, 1])
                with c1:
                    new_col_name = st.text_input('新列名称', placeholder='例: 新指标',
                                                 key='new_col_name')
                with c2:
                    default_val = st.text_input('默认填充值', placeholder='留空=空值',
                                                value='', key='new_col_default')
                with c3:
                    st.write('&nbsp;')
                    if st.button('✅ 确认添加', key='confirm_add_col',
                                 use_container_width=True):
                        if not new_col_name.strip():
                            st.error('请输入列名')
                        elif new_col_name.strip() in edited.columns:
                            st.error('列名已存在')
                        else:
                            fill_val = default_val.strip() or None
                            edited_copy = edited.copy()
                            edited_copy[new_col_name.strip()] = fill_val
                            st.session_state.user_data = edited_copy.reset_index(drop=True)
                            st.session_state.show_add_col = False
                            st.success(f'已添加列: {new_col_name.strip()}')
                            st.rerun()
                if st.button('❌ 取消添加', key='cancel_add_col'):
                    st.session_state.show_add_col = False
                    st.rerun()

            # ===== 删除行功能 =====
            if st.session_state.get('show_del_rows'):
                st.divider()
                all_row_indices = list(range(len(edited)))
                del_rows = st.multiselect(
                    '选择要删除的行（行号从0开始）',
                    options=all_row_indices,
                    default=[],
                    format_func=lambda i: f'第 {i+1} 行: {str(edited.iloc[i].tolist())[:50]}...',
                    key='row_selector'
                )
                c1, c2 = st.columns([1.5, 1])
                with c1:
                    if del_rows and st.button('确认删除所选行', key='confirm_del_rows',
                                              use_container_width=True):
                        keep_mask = [i not in del_rows for i in range(len(edited))]
                        st.session_state.user_data = edited.loc[keep_mask].reset_index(drop=True)
                        st.session_state.show_del_rows = False
                        st.success(f'已删除 {len(del_rows)} 行')
                        st.rerun()
                with c2:
                    if st.button('❌ 取消', key='cancel_del_rows',
                                 use_container_width=True):
                        st.session_state.show_del_rows = False
                        st.rerun()

            # ===== 删除列功能 =====
            if st.session_state.get('show_del_cols'):
                st.divider()
                del_cols = st.multiselect(
                    '选择要删除的列',
                    options=list(edited.columns),
                    default=[],
                    key='col_selector'
                )
                c1, c2 = st.columns([1.5, 1])
                with c1:
                    if del_cols and st.button('确认删除所选列', key='confirm_del_cols',
                                              use_container_width=True):
                        cols_to_keep = [c for c in edited.columns if c not in del_cols]
                        st.session_state.user_data = edited[cols_to_keep].copy()
                        st.session_state.show_del_cols = False
                        st.success(f'已删除列: {", ".join(del_cols)}')
                        st.rerun()
                with c2:
                    if st.button('❌ 取消', key='cancel_del_cols',
                                 use_container_width=True):
                        st.session_state.show_del_cols = False
                        st.rerun()


# ==================== 1. 数据导入 ====================
def page_data_import():
    st.header('📁 数据导入')
    sub = st.segmented_control(
        '数据来源', ['📤 上传文件', '📝 示例数据', '✏️ 手动输入', '☁️ Supabase'],
        default='📤 上传文件', key='di_sub', label_visibility='collapsed')
    if sub is None:
        sub = '📤 上传文件'

    if sub == '📤 上传文件':
        uploaded_file = st.file_uploader('选择 CSV 或 Excel 文件', type=['csv', 'xlsx', 'xls'])
        if uploaded_file:
            try:
                df = parse_uploaded_file(uploaded_file)
                if df is not None:
                    set_new_data(df)
                    st.dataframe(df.head(10), use_container_width=True)
            except Exception as e:
                st.error(f'加载失败: {e}')

    elif sub == '📝 示例数据':
        # ---- 📈 SPC 控制图 ----
        st.subheader('📈 SPC 控制图')
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button('📐 正态分布样本', use_container_width=True, key='ex_norm'):
                np.random.seed(42)
                set_new_data(pd.DataFrame({'测量值': np.random.normal(10.0, 0.5, 100)}))
                st.success('已加载 100 个正态分布样本')
                st.rerun()
        with c2:
            if st.button('📏 SPC 多子组样本', use_container_width=True, key='ex_spc'):
                np.random.seed(42)
                data = [v for i in range(25) for v in np.random.normal(10.0 + (i % 5) * 0.1, 0.3, 5)]
                set_new_data(pd.DataFrame({'测量值': data}))
                st.success('已加载 125 个多子组样本 (25组 × 5)')
                st.rerun()
        with c3:
            if st.button('⚙️ 含偏移样本', use_container_width=True, key='ex_shift'):
                np.random.seed(42)
                d1 = list(np.random.normal(10.0, 0.5, 50))
                d2 = list(np.random.normal(11.5, 0.5, 30))
                set_new_data(pd.DataFrame({'测量值': d1 + d2}))
                st.success('已加载含过程偏移的样本')
                st.rerun()

        # ---- 🎯 过程能力 / 质量图形 ----
        st.subheader('🎯 过程能力 / 质量图形')
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button('📊 帕累托图示例', use_container_width=True, key='ex_pareto'):
                set_new_data(pd.DataFrame({
                    '不良类型': ['外观磕碰', '屏幕黑点', '白平衡不良', '频闪', '无画面', '画面倾斜', '黑屏', '收边不良'],
                    '数量': [83, 19, 14, 21, 8, 13, 6, 5],
                }))
                st.success('已加载帕累托图示例')
                st.rerun()
        with c2:
            if st.button('📦 箱线图分组示例', use_container_width=True, key='ex_box'):
                np.random.seed(42)
                groups = ['A线'] * 30 + ['B线'] * 30 + ['C线'] * 30
                vals = list(np.random.normal(10.0, 0.4, 30)) + \
                       list(np.random.normal(10.5, 0.6, 30)) + \
                       list(np.random.normal(9.8, 0.3, 30))
                set_new_data(pd.DataFrame({'产线': groups, '测量值': vals}))
                st.success('已加载分组箱线图示例')
                st.rerun()
        with c3:
            if st.button('📈 运行图示例', use_container_width=True, key='ex_run'):
                np.random.seed(7)
                set_new_data(pd.DataFrame({
                    '序号': list(range(1, 51)),
                    '测量值': np.random.normal(10.0, 0.5, 50) + np.linspace(0, 1.5, 50),
                }))
                st.success('已加载运行图示例')
                st.rerun()

        # ---- 🔬 测量系统分析 MSA ----
        st.subheader('🔬 测量系统分析 MSA')
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button('🔬 Gage R&R 示例', use_container_width=True, key='ex_grr'):
                np.random.seed(123)
                parts, operators, measurements = [], [], []
                true_vals = [10.0, 10.2, 10.5, 10.3, 10.8, 11.0, 11.2, 10.9, 11.5, 11.8]
                for p_id, tv in enumerate(true_vals, 1):
                    for op in [1, 2, 3]:
                        for _ in range(2):
                            parts.append(p_id)
                            operators.append(op)
                            measurements.append(tv + np.random.normal(0, 0.05) + np.random.normal(0, 0.02))
                set_new_data(pd.DataFrame({'Part': parts, 'Operator': operators, 'Measurement': measurements}))
                st.success('已加载: 10部件 × 3操作员 × 2次试验')
                st.rerun()
        with c2:
            if st.button('🧮 计数型 GRR 示例', use_container_width=True, key='ex_attr'):
                np.random.seed(1)
                ref = [1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1, 1]
                op1 = [r if np.random.rand() > 0.1 else 1 - r for r in ref]
                op2 = [r if np.random.rand() > 0.15 else 1 - r for r in ref]
                op3 = [r if np.random.rand() > 0.1 else 1 - r for r in ref]
                set_new_data(pd.DataFrame({
                    '参考结果': ref,
                    '操作员A': op1,
                    '操作员B': op2,
                    '操作员C': op3,
                }))
                st.success('已加载计数型 GRR 示例 (20件 × 3操作员)')
                st.rerun()
        with c3:
            if st.button('📐 测量不确定度示例', use_container_width=True, key='ex_unc'):
                np.random.seed(19)
                # 对标称值 50.000mm 的量块重复测量 30 次
                set_new_data(pd.DataFrame({'重复测量': np.round(
                    np.random.normal(50.000, 0.003, 30), 4
                )}))
                st.success('已加载测量不确定度示例 (30次重复测量)')
                st.rerun()

        # ---- 🔢 统计推断 / 回归 ----
        st.subheader('🔢 统计推断 / 回归')
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button('🔀 双样本 t 检验示例', use_container_width=True, key='ex_ttest'):
                np.random.seed(5)
                set_new_data(pd.DataFrame({
                    '样本A': np.random.normal(10.0, 0.5, 40),
                    '样本B': np.random.normal(11.0, 0.6, 40),
                }))
                st.success('已加载双样本 t 检验示例')
                st.rerun()
        with c2:
            if st.button('🔗 相关性矩阵示例', use_container_width=True, key='ex_corr'):
                np.random.seed(8)
                n = 80
                x1 = np.random.normal(50, 5, n)
                x2 = x1 * 0.8 + np.random.normal(0, 3, n)
                x3 = x1 * -0.6 + np.random.normal(0, 4, n)
                x4 = np.random.normal(30, 4, n)
                set_new_data(pd.DataFrame({
                    '温度': x1, '压力': x2, '速度': x3, '湿度': x4,
                }))
                st.success('已加载相关性矩阵示例')
                st.rerun()
        with c3:
            if st.button('📉 回归分析示例', use_container_width=True, key='ex_reg'):
                np.random.seed(9)
                n = 60
                x = np.random.normal(25, 3, n)
                y = 2.5 * x + 10 + np.random.normal(0, 5, n)
                set_new_data(pd.DataFrame({'温度': x, '产量': y}))
                st.success('已加载一元回归示例')
                st.rerun()

        # ---- 🧪 高级分析 ----
        st.subheader('🧪 高级分析')
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button('🧪 DOE 示例', use_container_width=True, key='ex_doe'):
                np.random.seed(99)
                data = []
                for A in [-1, 1]:
                    for B in [-1, 1]:
                        for C in [-1, 1]:
                            for _ in range(2):
                                val = 25 + 3 * A + 1.5 * B - 1 * C + 2 * A * B + np.random.normal(0, 0.5)
                                data.append({'A_温度': A, 'B_压力': B, 'C_速度': C, '响应': val})
                set_new_data(pd.DataFrame(data))
                st.success('已加载 2³ 全因子 DOE 数据 (16 次试验)')
                st.rerun()
        with c2:
            if st.button('⏱️ Weibull 可靠性示例', use_container_width=True, key='ex_weibull'):
                np.random.seed(11)
                # Weibull(shape=2, scale=1000) 的随机样本
                from scipy.stats import weibull_min
                times = 1000 * weibull_min.rvs(2, size=50)
                set_new_data(pd.DataFrame({'失效时间': np.round(times, 1)}))
                st.success('已加载 Weibull 可靠性示例 (50 条失效时间)')
                st.rerun()
        with c3:
            if st.button('🛡️ FMEA 示例', use_container_width=True, key='ex_fmea'):
                set_new_data(pd.DataFrame({
                    '模式': ['焊接虚焊', '尺寸超差', '表面划伤', '装配错位', '漏装零件'],
                    '严重度': [8, 6, 3, 5, 9],
                    '发生度': [4, 7, 8, 3, 2],
                    '探测度': [5, 3, 2, 4, 6],
                }))
                st.success('已加载 5 条 FMEA 记录')
                st.rerun()

    elif sub == '✏️ 手动输入':
        st.caption('💡 在表格中直接输入，Tab 跳格；支持自动扩展行')
        if 'manual_df' not in st.session_state:
            st.session_state.manual_df = pd.DataFrame({'测量值': [''] * 10})

        df_cols = list(st.session_state.manual_df.columns)

        with st.expander('📝 列设置', expanded=True):
            c1, c2 = st.columns([3, 1])
            new_names = []
            with c1:
                nc = st.columns(len(df_cols)) if df_cols else [st]
                for i, cn in enumerate(df_cols):
                    with nc[i]:
                        nn = st.text_input(f'列{i+1}', value=cn, key=f'mcol_{i}', label_visibility='collapsed')
                        new_names.append(nn.strip() or f'列{i+1}')
            with c2:
                if st.button('➕ 添加列', use_container_width=True):
                    st.session_state.manual_df[f'列{len(df_cols)+1}'] = [''] * len(st.session_state.manual_df)
                    st.rerun()
                if len(df_cols) > 1 and st.button('➖ 删列', use_container_width=True):
                    st.session_state.manual_df = st.session_state.manual_df.iloc[:, :-1]
                    st.rerun()

        if new_names != df_cols:
            tmp = st.session_state.manual_df.copy()
            tmp.columns = new_names
            st.session_state.manual_df = tmp
            st.rerun()

        edited = st.data_editor(st.session_state.manual_df, use_container_width=True,
                                height=300, num_rows='dynamic', hide_index=True, key='manual_edit')

        bc1, bc2, bc3 = st.columns([1.5, 1, 1])
        with bc1:
            if st.button('✅ 导入数据', use_container_width=True, type='primary'):
                vd = edited.replace('', pd.NA).dropna(how='all').dropna(axis=1, how='all')
                if vd.empty:
                    st.error('表格为空')
                else:
                    for c in vd.columns:
                        cv = pd.to_numeric(vd[c], errors='coerce')
                        if cv.notna().sum() > 0:
                            vd[c] = cv
                    set_new_data(vd.reset_index(drop=True))
                    st.success(f'已导入 {len(vd)} 行')
        with bc2:
            if st.button('🗑️ 清空', use_container_width=True):
                st.session_state.manual_df = pd.DataFrame({c: [''] * len(edited) for c in edited.columns})
                st.rerun()
        with bc3:
            if st.button('📝 填充示例', use_container_width=True):
                nc = edited.columns.tolist()
                nr = max(len(edited), 10)
                st.session_state.manual_df = pd.DataFrame(
                    {c: np.round(np.random.normal(10, 0.5, nr), 3) for c in nc}
                )
                st.rerun()

    else:
        st.caption('☁️ 数据持久化到 Supabase，刷新不丢失')
        c1, c2 = st.columns([3, 1])
        with c1:
            st.write('**数据集名称**')
            save_name = st.text_input('', placeholder='例: 2024Q1_产线A', key='supa_save',
                                      label_visibility='collapsed')
        with c2:
            st.write('&nbsp;')
            if st.button('☁️ 保存', use_container_width=True, key='supa_save_btn'):
                if check_data() is None:
                    st.error('请先加载数据')
                elif not save_name.strip():
                    st.error('请输入名称')
                else:
                    r = supabase_helper.save_dataset(save_name.strip(), st.session_state.user_data,
                                                     columns_info=list(st.session_state.user_data.columns))
                    if r:
                        st.success(f'✅ 已保存 "{save_name.strip()}"')
                        st.rerun()

        st.divider()
        st.subheader('📂 从云端加载')
        if st.button('🔄 刷新', use_container_width=False):
            st.rerun()
        datasets = supabase_helper.list_datasets()
        if not datasets:
            st.info('暂无已保存的数据集')
        else:
            for ds in datasets:
                c1, c2, c3 = st.columns([4, 2, 1])
                with c1:
                    st.write(f'**{ds["name"]}**')
                    st.caption(f'{ds.get("row_count","?")} 行 · {str(ds.get("created_at",""))[:19]}')
                with c2:
                    if st.button('📥 加载', key=f'load_{ds["id"]}', use_container_width=True):
                        df = supabase_helper.load_dataset(ds['id'])
                        if df is not None:
                            set_new_data(df)
                            st.success(f'✅ 已加载 "{ds["name"]}"')
                            st.rerun()
                with c3:
                    if st.button('🗑️', key=f'del_{ds["id"]}', help='删除'):
                        if supabase_helper.delete_dataset(ds['id']):
                            st.success(f'已删除')
                            st.rerun()

    show_data_info()


# ==================== SPC 结果展示辅助函数 ====================
def _show_spc_results(r):
    """统一展示 SPC 控制图的分析结果：参数、目标偏差、判异规则违规"""
    # 参数表
    with st.expander('📊 控制图参数', expanded=True):
        cols = st.columns(min(len(r['stats']), 5))
        for i, (k, v) in enumerate(r['stats'].items()):
            with cols[i % len(cols)]:
                st.metric(k, f'{v:.4f}' if isinstance(v, (int, float)) else str(v))

    # 目标偏差
    tgt_dev = r.get('target_deviation')
    if tgt_dev:
        st.info(
            f'🎯 **目标偏差分析**: 中心线 {tgt_dev["center"]:.4f} → 目标值 {tgt_dev["target"]:.4f} | '
            f'偏差 = {tgt_dev["deviation"]:+.4f} ({tgt_dev["deviation_pct"]:+.2f}%)'
        )

    # 超限点汇总
    ooc = r.get('ooc_points', {})
    if ooc:
        ooc_total = sum(v for v in ooc.values())
        if ooc_total > 0:
            ooc_str = ' | '.join(f'{k}: {v}个' for k, v in ooc.items() if v > 0)
            st.warning(f'⚠️ **超限点检测**: 共 {ooc_total} 个超限点 ({ooc_str}) — 图中以红色 ✕ 标记')
        else:
            st.success('✅ **超限点检测**: 无超限点，过程受控')

    # WECO/Nelson 判异规则
    weco = r.get('weco', {})
    violations = weco.get('violations', {})
    if violations:
        with st.expander(f'🔴 **判异规则违规** ({weco.get("total_violations", 0)} 条规则触发)', expanded=True):
            rule_names = {
                1: '规则1: 超出控制限',
                2: '规则2: 连续7点同侧 (WECO)',
                3: '规则3: 连续6点递增/递减 (趋势)',
                4: '规则4: 连续14点交替上下',
                5: '规则5: 连续3点中2点超出2σ',
                6: '规则6: 连续5点中4点超出1σ',
                7: '规则7: 连续15点在±1σ内',
                8: '规则8: 连续8点在±1σ外',
            }
            for rule_id, v in violations.items():
                rule_name = rule_names.get(rule_id, f'规则{rule_id}')
                st.error(f'**{rule_name}** — {v["description"]}')
                if 'detail' in v:
                    for d in v['detail']:
                        st.caption(f'  • {d}')
    else:
        st.success('✅ **判异规则**: 未触发任何 Nelson 判异规则，过程稳定')


# ==================== 2. SPC 控制图 ====================
def page_spc():
    st.header('📈 SPC 控制图')
    df = check_data()
    if df is None:
        st.warning('⚠️ 请先在「数据导入」中加载数据')
        return

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        st.error('数据中没有数值列')
        return

    tab1, tab2, tab3, tab4 = st.tabs(['休哈特控制图', 'EWMA 控制图', 'CUSUM 控制图', '多变量 T²'])

    # --- Tab1: 休哈特 ---
    with tab1:
        ct = st.selectbox('控制图类型', [
            'X-bar R (均值-极差)', 'X-bar S (均值-标准差)', 'I-MR (单值-移动极差)',
            'P 图 (不合格品率)', 'NP 图 (不合格品数)', 'C 图 (缺陷数)', 'U 图 (单位缺陷数)'
        ], key='shewhart_type')

        # 目标值输入（可选）
        tgt_input = st.text_input('🎯 目标值 (可选)', placeholder='留空=不设目标', key='shewhart_target',
                                  help='设置后将计算中心线与目标值的偏差，并在图中显示目标线')
        target_val = float(tgt_input) if tgt_input else None

        if ct in ['X-bar R (均值-极差)', 'X-bar S (均值-标准差)']:
            c1, c2 = st.columns(2)
            with c1:
                dc = st.selectbox('数据列', numeric_cols, key='xbar_col')
            with c2:
                ss = st.number_input('子组大小', 2, 10, 5)
            if dc not in df.columns:
                st.warning(f'列 "{dc}" 已变更，请重新选择')
            else:
                data = df[dc].dropna().values
                if len(data) < ss * 2:
                    st.error(f'数据不足，需至少 {ss*2} 个点')
                else:
                    r = spc_charts.xbar_r_chart(data, ss, target_val) if ct == 'X-bar R (均值-极差)' else spc_charts.xbar_s_chart(data, ss, target_val)
                    st.plotly_chart(r['chart'], use_container_width=True)
                    _show_spc_results(r)

        elif ct == 'I-MR (单值-移动极差)':
            dc = st.selectbox('数据列', numeric_cols, key='imr_col')
            if dc not in df.columns:
                st.warning(f'列 "{dc}" 已变更，请重新选择')
            else:
                data = df[dc].dropna().values
                if len(data) < 2:
                    st.error('需至少2个数据点')
                else:
                    r = spc_charts.imr_chart(data, target_val)
                    st.plotly_chart(r['chart'], use_container_width=True)
                    _show_spc_results(r)

        elif ct == 'P 图 (不合格品率)':
            c1, c2 = st.columns(2)
            with c1:
                dcol = st.selectbox('不合格品数列', numeric_cols, key='p_col')
            with c2:
                scol = st.selectbox('样本量列', numeric_cols, key='p_size')
            if dcol in df.columns and scol in df.columns:
                # 两列按行对齐后再过滤，避免分别 dropna 导致配对错位
                tmp = df[[dcol, scol]].replace([np.inf, -np.inf], np.nan).dropna()
                d = tmp[dcol].values
                s = tmp[scol].values
                valid = s > 0
                if not np.any(valid):
                    st.error('样本量全部为 0，无法绘制 P 图')
                else:
                    d, s = d[valid], s[valid]
                    if len(d) >= 2:
                        r = spc_charts.p_chart(d, s, target_val)
                        st.plotly_chart(r['chart'], use_container_width=True)
                        _show_spc_results(r)
            else:
                st.warning('数据列已变更，请重新选择')

        elif ct == 'NP 图 (不合格品数)':
            c1, c2 = st.columns(2)
            with c1:
                dcol = st.selectbox('不合格品数列', numeric_cols, key='np_col')
            with c2:
                sz = st.number_input('固定样本量', 1, 10000, 100)
            if dcol not in df.columns:
                st.warning(f'列 "{dcol}" 已变更，请重新选择')
            else:
                d = df[dcol].replace([np.inf, -np.inf], np.nan).dropna().values
                if len(d) >= 2:
                    if np.max(d) > sz:
                        st.warning(f'⚠️ 有不合格品数({np.max(d):g})超过固定样本量({sz})，请核对数据（控制限已按截断处理）')
                    r = spc_charts.np_chart(d, sz, target_val)
                    st.plotly_chart(r['chart'], use_container_width=True)
                    _show_spc_results(r)

        elif ct in ['C 图 (缺陷数)', 'U 图 (单位缺陷数)']:
            dcol = st.selectbox('缺陷数列', numeric_cols, key='cu_col')
            if dcol not in df.columns:
                st.warning(f'列 "{dcol}" 已变更，请重新选择')
            elif ct == 'C 图 (缺陷数)':
                d = df[dcol].dropna().values
                r = spc_charts.c_chart(d, target_val)
                st.plotly_chart(r['chart'], use_container_width=True)
                _show_spc_results(r)
            else:
                scol = st.selectbox('单位数列', numeric_cols, key='u_size')
                if scol not in df.columns:
                    st.warning(f'列 "{scol}" 已变更，请重新选择')
                else:
                    # 两列按行对齐后再过滤，避免分别 dropna 导致配对错位
                    tmp = df[[dcol, scol]].replace([np.inf, -np.inf], np.nan).dropna()
                    d = tmp[dcol].values
                    s = tmp[scol].values
                    valid = s > 0
                    if not np.any(valid):
                        st.error('单位数全部为 0，无法绘制 U 图')
                    elif len(d[valid]) >= 2:
                        r = spc_charts.u_chart(d[valid], s[valid], target_val)
                        st.plotly_chart(r['chart'], use_container_width=True)
                        _show_spc_results(r)

    # --- Tab2: EWMA ---
    with tab2:
        dc = st.selectbox('数据列', numeric_cols, key='ewma_col')
        c1, c2, c3 = st.columns(3)
        with c1:
            lam = st.slider('平滑系数 λ', 0.05, 1.0, 0.2, 0.05)
        with c2:
            L = st.slider('控制限倍数 L', 2.0, 4.0, 2.7, 0.1)
        with c3:
            tgt_input_ewma = st.text_input('目标值 (可选)', placeholder='留空=不设', key='ewma_target')
        target_ewma = float(tgt_input_ewma) if tgt_input_ewma else None
        if dc not in df.columns:
            st.warning(f'列 "{dc}" 已变更，请重新选择')
        else:
            data = df[dc].dropna().values
            if len(data) >= 2:
                r = spc_advanced.ewma_chart(data, lam, L, target_ewma)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.plotly_chart(r['chart'], use_container_width=True)
                    _show_spc_results(r)
            else:
                st.error('至少需要 2 个数据点')

    # --- Tab3: CUSUM ---
    with tab3:
        dc = st.selectbox('数据列', numeric_cols, key='cusum_col')
        c1, c2, c3 = st.columns(3)
        with c1:
            k_val = st.slider('参考值 k (σ倍数)', 0.1, 2.0, 0.5, 0.1)
        with c2:
            h_val = st.slider('决策区间 h (σ倍数)', 2.0, 8.0, 4.0, 0.5)
        with c3:
            tgt_input_cusum = st.text_input('目标值 (可选)', placeholder='留空=用数据均值', key='cusum_target')
        target_cusum = float(tgt_input_cusum) if tgt_input_cusum else None
        if dc not in df.columns:
            st.warning(f'列 "{dc}" 已变更，请重新选择')
        else:
            data = df[dc].dropna().values
            if len(data) >= 2:
                r = spc_advanced.cusum_chart(data, target=target_cusum, k=k_val, h=h_val)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.plotly_chart(r['chart'], use_container_width=True)
                    _show_spc_results(r)
            else:
                st.error('至少需要 2 个数据点')

    # --- Tab4: 多变量 T² ---
    with tab4:
        st.caption('对多列数值变量联合监控 — 自动使用所有数值列')
        alpha = st.slider('显著性水平 α', 0.001, 0.05, 0.0027, 0.0001, key='t2_alpha',
                          help='0.0027 ≈ 3σ 控制限')
        r = spc_advanced.t2_chart(df, alpha)
        if 'error' in r:
            st.error(r['error'])
        else:
            st.plotly_chart(r['chart'], use_container_width=True)
            _show_spc_results(r)

    show_data_info()


# ==================== 3. 过程能力分析 ====================
def page_capability():
    st.header('🎯 过程能力分析')
    df = check_data()
    if df is None:
        st.warning('⚠️ 请先加载数据')
        return

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        st.error('无数值列')
        return

    tab1, tab2, tab3 = st.tabs(['Cp/Cpk/Pp/Ppk', '非正态 (Box-Cox)', 'Cg/Cgk 检具能力'])

    # --- Tab1: Cp/Cpk ---
    with tab1:
        dc = st.selectbox('数据列', numeric_cols, key='cpk_col')
        data = df[dc].dropna().values
        if len(data) < 2:
            st.error('至少需要 2 个数据点')
        else:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                usl = st.text_input('规格上限 USL', placeholder='留空=不设')
            with c2:
                lsl = st.text_input('规格下限 LSL', placeholder='留空=不设')
            with c3:
                tgt = st.text_input('目标值', placeholder='留空=不设')
            with c4:
                ss = st.number_input('子组大小', 1, 10, 1,
                                     help='1=单值(移动极差法), >1=子组法')

            c5, c6 = st.columns([1, 3])
            with c5:
                wm = st.selectbox('组内σ方法', ['Rbar', 'Sbar'],
                                  help='Rbar=R̄/d₂(极差法), Sbar=S̄/c₄(标准差法, 对正态数据更高效)',
                                  disabled=(ss <= 1))
                if ss <= 1:
                    wm = 'Rbar'  # 单值只能用移动极差

            usl = float(usl) if usl else None
            lsl = float(lsl) if lsl else None
            tgt = float(tgt) if tgt else None

            if usl is None and lsl is None:
                st.info('请至少输入一个规格限')
            elif usl is not None and lsl is not None and usl <= lsl:
                st.error('USL 必须大于 LSL')
            else:
                try:
                    r = capability.process_capability(data, usl, lsl, tgt, ss, wm)
                except Exception as e:
                    st.error(f'能力分析计算失败: {e}')
                else:
                    if 'error' in r:
                        st.error(r['error'])
                    else:
                        st.subheader('📊 能力指标')
                        cs = st.columns(6)
                        with cs[0]: st.metric('Cp (短期)', f'{r["Cp"]:.2f}' if r['Cp'] is not None else 'N/A')
                        with cs[1]: st.metric('Cpk (短期)', f'{r["Cpk"]:.2f}' if r['Cpk'] is not None else 'N/A')
                        with cs[2]: st.metric('Pp (长期)', f'{r["Pp"]:.2f}' if r['Pp'] is not None else 'N/A')
                        with cs[3]: st.metric('Ppk (长期)', f'{r["Ppk"]:.2f}' if r['Ppk'] is not None else 'N/A')
                        with cs[4]: st.metric('Cpk 评级', r.get('cpk_level', 'N/A'))
                        with cs[5]: st.metric('预计 PPM',
                                               f'{r["ppm_total"]:,.0f}',
                                               help=f'实测 PPM: {r.get("ppm_observed_total", 0):,.0f}')

                        cs2 = st.columns(5)
                        with cs2[0]: st.metric('均值', f'{r["mean"]:.4f}')
                        with cs2[1]: st.metric('整体 σ', f'{r["std_overall"]:.4f}')
                        wm_label = r.get('within_method', 'Rbar')
                        if r.get('subgroup_size', 1) <= 1:
                            sigma_method = '移动极差 MR̄/d₂'
                        else:
                            sigma_method = f'子组{wm_label}法 (n={r.get("subgroup_size",1)})'
                        with cs2[2]: st.metric('组内 σ', f'{r["std_within"]:.4f}', help=f'估计方法: {sigma_method}')
                        with cs2[3]: st.metric('样本量', f'{r["n"]} (子组={r.get("subgroup_size",1)})')
                        with cs2[4]: st.metric('实测 PPM',
                                               f'{r.get("ppm_observed_total", 0):,.0f}',
                                               help='实际超规格数据点数 / 总数 × 1M')

                        st.plotly_chart(r['chart'], use_container_width=True)

                        with st.expander('📋 评级标准'):
                            st.table(pd.DataFrame({
                                'Cpk': ['≥ 1.67', '1.33~1.67', '1.00~1.33', '0.67~1.00', '< 0.67'],
                                '评级': ['优秀', '良好', '尚可', '不足', '差'],
                                '建议': ['可放宽抽检', '维持现状', '加强控制', '需改进', '急需改进'],
                            }))

    # --- Tab2: Box-Cox 非正态能力 ---
    with tab2:
        st.caption('数据非正态时，使用 Box-Cox 变换后计算能力指数 (Minitab 兼容)')
        dc_bc = st.selectbox('数据列', numeric_cols, key='cpk_boxcox_col')
        data_bc = df[dc_bc].dropna().values
        if len(data_bc) < 5:
            st.error('Box-Cox 变换至少需要 5 个数据点')
        else:
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                usl_bc = st.text_input('规格上限 USL', key='bc_usl', placeholder='留空=不设')
            with c2:
                lsl_bc = st.text_input('规格下限 LSL', key='bc_lsl', placeholder='留空=不设')
            with c3:
                ss_bc = st.number_input('子组大小', 1, 10, 1, key='bc_ss',
                                        help='1=单值, >1=子组法')
            with c4:
                wm_bc = st.selectbox('组内σ方法', ['Rbar', 'Sbar'], key='bc_wm')

            usl_bc_f = float(usl_bc) if usl_bc else None
            lsl_bc_f = float(lsl_bc) if lsl_bc else None

            if usl_bc_f is None and lsl_bc_f is None:
                st.info('请至少输入一个规格限')
            elif usl_bc_f is not None and lsl_bc_f is not None and usl_bc_f <= lsl_bc_f:
                st.error('USL 必须大于 LSL')
            else:
                r_bc = capability.process_capability_boxcox(
                    data_bc, usl_bc_f, lsl_bc_f, subgroup_size=ss_bc, within_method=wm_bc)
                if 'error' in r_bc:
                    st.error(r_bc['error'])
                else:
                    trans = r_bc.get('transformation', {})
                    st.info(f'Box-Cox λ = {trans.get("lambda", "N/A")} '
                            f'{trans.get("shift", "")}')
                    st.subheader('📊 变换后能力指标')
                    cs_bc = st.columns(6)
                    with cs_bc[0]: st.metric('Cp', f'{r_bc["Cp"]:.2f}' if r_bc['Cp'] is not None else 'N/A')
                    with cs_bc[1]: st.metric('Cpk', f'{r_bc["Cpk"]:.2f}' if r_bc['Cpk'] is not None else 'N/A')
                    with cs_bc[2]: st.metric('Pp', f'{r_bc["Pp"]:.2f}' if r_bc['Pp'] is not None else 'N/A')
                    with cs_bc[3]: st.metric('Ppk', f'{r_bc["Ppk"]:.2f}' if r_bc['Ppk'] is not None else 'N/A')
                    with cs_bc[4]: st.metric('Cpk 评级', r_bc.get('cpk_level', 'N/A'))
                    with cs_bc[5]: st.metric('预计 PPM', f'{r_bc["ppm_total"]:,.0f}',
                                             help=f'实测PPM: {r_bc.get("ppm_observed_total", 0):,.0f}')
                    st.plotly_chart(r_bc['chart'], use_container_width=True)

    # --- Tab3: Cg/Cgk ---
    with tab3:
        st.caption('MSA Type 1 — 检具能力指数评估')
        dc = st.selectbox('重复测量列', numeric_cols, key='cg_col')
        data = df[dc].dropna().values
        c1, c2, c3 = st.columns(3)
        with c1:
            tol = st.text_input('公差 T = USL - LSL', value='', key='cg_tol',
                                placeholder='例: 0.1')
        with c2:
            ref_val = st.text_input('参考值 (标准值)', value='', key='cg_ref',
                                    placeholder='留空=用数据均值')
        with c3:
            pct = st.selectbox('容差百分比', [20, 100], index=0, key='cg_pct',
                               help='20% = VDA 5 标准, 100% = 完整公差法')

        if tol:
            tolerance = float(tol)
            ref = float(ref_val) if ref_val else None
            r = msa_advanced.cg_cgk(data, tolerance, ref, percent_tol=pct)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.plotly_chart(r['chart'], use_container_width=True)
                st.subheader('能力指数')
                cs = st.columns(4)
                with cs[0]: st.metric('Cg', r['stats']['Cg'])
                with cs[1]: st.metric('Cgk', r['stats']['Cgk'])
                with cs[2]: st.metric('Cg 评级', r['stats']['Cg 评级'])
                with cs[3]: st.metric('Cgk 评级', r['stats']['Cgk 评级'])
                with st.expander('📊 详细参数'):
                    for k, v in r['details'].items():
                        st.metric(k, v)

    show_data_info()


# ==================== 4. 质量图形工具 ====================
def page_quality_tools():
    st.header('📊 质量图形工具')
    df = check_data()
    show_data_required = df is None

    t1, t2, t3, t4, t5 = st.tabs(['帕累托图', '直方图', '箱线图', '运行图', '鱼骨图'])

    # ===== 数据相关提示 =====
    if show_data_required:
        with t1: st.warning('⚠️ 请先在「数据导入」中加载数据')
        with t2: st.warning('⚠️ 请先在「数据导入」中加载数据')
        with t3: st.warning('⚠️ 请先在「数据导入」中加载数据')
        with t4: st.warning('⚠️ 请先在「数据导入」中加载数据')
    else:
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        text_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()

        # --- 帕累托图 ---
        with t1:
            if not numeric_cols:
                st.error('无数值列')
            else:
                c1, c2 = st.columns(2)
                with c1:
                    cat_col = st.selectbox('类别列', text_cols + numeric_cols, key='pareto_cat')
                with c2:
                    cnt_col = st.selectbox('频数列', numeric_cols, key='pareto_cnt')
                r = pareto_histogram.pareto_chart(df[cat_col].astype(str).tolist(), df[cnt_col].values)
                st.plotly_chart(r['chart'], use_container_width=True)
                c1, c2 = st.columns(2)
                with c1: st.metric('总计', r['total'])
                with c2: st.dataframe(r['data'], use_container_width=True)

        # --- 直方图 ---
        with t2:
            if not numeric_cols:
                st.error('无数值列')
            else:
                dc = st.selectbox('数据列', numeric_cols, key='hist_col')
                r = pareto_histogram.histogram_with_stats(df[dc].dropna().values)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.plotly_chart(r['chart'], use_container_width=True)
                    cs = st.columns(len(r['stats']))
                    for i, (k, v) in enumerate(r['stats'].items()):
                        cs[i].metric(k, v)

        # --- 箱线图 ---
        with t3:
            if not numeric_cols:
                st.error('无数值列')
            else:
                dc = st.selectbox('数据列', numeric_cols, key='box_col')
                gc = st.selectbox('分组列 (可选)', ['无分组'] + text_cols + numeric_cols, key='box_group')
                if gc == '无分组':
                    r = pareto_histogram.box_plot(df[dc].dropna().values)
                else:
                    grps = df.groupby(gc, sort=True)
                    r = pareto_histogram.box_plot(
                        [g[dc].dropna().values for _, g in grps],
                        group_labels=[str(n) for n in sorted(df[gc].unique())])
                st.plotly_chart(r['chart'], use_container_width=True)

        # --- 运行图 ---
        with t4:
            if not numeric_cols:
                st.error('无数值列')
            else:
                dc = st.selectbox('数据列', numeric_cols, key='run_col')
                tgt = st.text_input('目标线 (可选)', placeholder='留空=不显示', key='run_tgt')
                target = float(tgt) if tgt else None
                r = quality_tools.run_chart(df[dc].dropna().values, target)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.plotly_chart(r['chart'], use_container_width=True)
                    with st.expander('📊 运行图统计'):
                        for k, v in r['stats'].items():
                            st.metric(k, v)

    # ===== 鱼骨图 (独立运行，无需数据) =====
    with t5:
        st.caption('根本原因分析 — 支持多级细分')

        default_text_template = (
            '人员: 操作技能不足, {培训体系: 新员工多, 考核不严}, 疲劳作业, 质量意识淡薄\n'
            '机器: 设备老化, {维护管理: 保养不及时, 备件短缺}, 参数漂移\n'
            '材料: 来料批次差异, {供应商: 审核不严格, 变更未验证}, 存储不当\n'
            '方法: SOP 不清晰, {工艺设计: 参数窗口过宽, 未做DOE验证}\n'
            '环境: 温湿度波动, 洁净度不足, 照明不够\n'
            '测量: 量具精度不够, {校准管理: 周期过长, 标准件失效}, 测量方法不当'
        )

        # ---- 加载已有配置区域 ----
        with st.expander('📂 加载已保存的鱼骨图配置', expanded=False):
            if st.button('🔄 刷新列表', key='fb_reload_list'):
                st.rerun()
            configs = supabase_helper.list_fishbone_configs()
            if not configs:
                st.info('暂无已保存的鱼骨图配置')
            else:
                for cfg in configs:
                    c1, c2, c3 = st.columns([4, 1, 1])
                    with c1:
                        st.write(f'**{cfg["name"]}**')
                        st.caption(f'问题: {cfg.get("problem","")} · {str(cfg.get("created_at",""))[:19]}')
                    with c2:
                        if st.button('📥 加载', key=f'fb_load_{cfg["id"]}', use_container_width=True):
                            # 直接写入 text_input/text_area 对应的 session_state key
                            st.session_state.fish_problem = cfg.get('problem', '产品合格率下降')
                            st.session_state.fish_input = cfg.get('raw_input', '')
                            st.session_state.fb_loaded_name = cfg['name']
                            st.session_state.fb_loaded_time = 0  # 本轮渲染计数
                            st.rerun()
                    with c3:
                        if st.button('🗑️', key=f'fb_del_{cfg["id"]}', help='删除此配置'):
                            if supabase_helper.delete_fishbone_config(cfg['id']):
                                st.success('已删除')
                                st.rerun()

        # ---- 已加载提示（约10秒自动消失）----
        if 'fb_loaded_name' in st.session_state and st.session_state.get('fb_loaded_time') is not None:
            loaded_name = st.session_state.fb_loaded_name
            count = st.session_state.get('fb_loaded_time', 0)
            if count < 12:  # 约显示10-12秒
                st.success(f'✅ 已加载 "{loaded_name}"')
            else:
                del st.session_state.fb_loaded_name
                del st.session_state.fb_loaded_time
            # 每次渲染递增计数（Streamlit 自动 rerun 约1s/次）
            st.session_state.fb_loaded_time = count + 1

        # ---- 问题描述 ----
        prob = st.text_input('问题描述', value='产品合格率下降', key='fish_problem')

        st.write('**输入格式说明**（中英文标点均可）')
        st.caption('• 每行一个大类，冒号/：后跟原因，逗号/，分隔\n'
                   '• 一级原因直接写名称\n'
                   '• 二级分类用 `{分类名: 子原因1, 子原因2}` 或 `｛分类名：子原因1，子原因2｝` 格式')

        raw = st.text_area('输入原因',
                           value=default_text_template,
                           height=220, key='fish_input')

        # ---- 操作按钮 ----
        c_gen, c_save = st.columns([3, 1])
        with c_gen:
            if st.button('🔄 生成鱼骨图', use_container_width=True):
                # 自动转换中文标点为英文（冒号、逗号、大括号、分号）
                raw_norm = (raw.strip()
                            .replace('：', ':')
                            .replace('，', ',')
                            .replace('｛', '{')
                            .replace('｝', '}')
                            .replace('；', ';'))

                cats = {}
                for line in raw_norm.split('\n'):
                    line = line.strip()
                    if not line or ':' not in line:
                        continue
                    name, causes_str = line.split(':', 1)
                    name = name.strip()
                    cats[name] = []

                    # 解析原因列表，支持 {二级分类: 子原因} 嵌套
                    parts = []
                    depth = 0
                    current = ''
                    for ch in causes_str:
                        if ch == '{':
                            depth += 1
                            if depth == 1:
                                if current.strip():
                                    parts.append(current.strip())
                                current = ''
                            else:
                                current += ch
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                inner = current.strip()
                                if ':' in inner:
                                    sub_name, sub_causes = inner.split(':', 1)
                                    sub_list = [c.strip() for c in sub_causes.split(',') if c.strip()]
                                    parts.append({sub_name.strip(): sub_list})
                                current = ''
                            else:
                                current += ch
                        elif ch == ',' and depth == 0:
                            if current.strip():
                                parts.append(current.strip())
                            current = ''
                        else:
                            current += ch
                    if current.strip():
                        parts.append(current.strip())

                    cats[name] = parts

                if cats:
                    r = quality_tools.fishbone_diagram(prob, cats)
                    st.plotly_chart(r['chart'], use_container_width=True)
                else:
                    st.error('请按格式输入')

        with c_save:
            if st.button('💾 保存到云端', use_container_width=True, key='fb_save_cloud',
                         type='primary'):
                raw_val = st.session_state.get('fish_input', '')
                if not raw_val.strip():
                    st.error('请先输入原因')
                else:
                    # 弹出命名输入框
                    st.session_state.fb_show_name_input = True
                    st.rerun()

        # ---- 保存命名对话框 ----
        if st.session_state.get('fb_show_name_input'):
            nc1, nc2 = st.columns([3, 1])
            with nc1:
                st.write('**配置名称**')
                cfg_name = st.text_input('',
                                        placeholder='例: 2024Q1产线A异常分析',
                                        key='fb_cfg_name', label_visibility='collapsed')
            with nc2:
                st.write('&nbsp;')
                if st.button('✅ 确认保存', use_container_width=True, key='fb_confirm_save'):
                    if not cfg_name.strip():
                        st.error('请输入名称')
                    else:
                        r = supabase_helper.save_fishbone(
                            cfg_name.strip(),
                            prob,
                            st.session_state.get('fish_input', '')
                        )
                        if r:
                            st.success(f'✅ 已保存 "{cfg_name.strip()}"')
                            st.session_state.fb_show_name_input = False
                            st.rerun()

    if not show_data_required:
        show_data_info()


# ==================== 5. 测量系统分析 MSA ====================
def page_msa():
    st.header('🔬 测量系统分析 MSA')
    df = check_data()
    if df is None:
        st.warning('⚠️ 请先加载数据')
        return

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    all_cols = df.columns.tolist()

    t1, t2, t3 = st.tabs(['计量型 Gage R&R', '计数型 Gage R&R', '测量不确定度'])

    # --- 计量型 GRR ---
    with t1:
        # 方法选择
        grr_method = st.radio(
            '分析方法',
            ['平均值-极差法 (X-bar R)', 'ANOVA 法 (方差分析)'],
            horizontal=True, key='grr_method',
            help='平均值-极差法: AIAG MSA 第4版经典方法\n'
                 'ANOVA 法: 双因素随机效应方差分析，可检测交互效应'
        )

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            pc = st.selectbox('部件列', all_cols, key='grr_part')
        with c2:
            oc = st.selectbox('操作员列', all_cols, key='grr_op')
        with c3:
            mc = st.selectbox('测量值列', numeric_cols, key='grr_meas')
        with c4:
            tol_val = st.text_input('公差 (USL-LSL)', placeholder='留空=不计算%Tol',
                                     key='grr_tol',
                                     help='用于计算 %Tolerance: 5.15σ/Tol×100')

        parts = df[pc].values
        ops = df[oc].values
        meas = df[mc].values
        tolerance = float(tol_val) if tol_val else None
        n_parts = len(np.unique(parts))
        n_ops = len(np.unique(ops))
        st.info(f'📋 {n_parts} 部件 × {n_ops} 操作员 × {len(meas)} 次测量')

        if n_parts < 2 or n_ops < 2:
            st.error('需至少 2 部件 2 操作员')
        else:
            # 根据选择的方法调用不同函数
            if 'ANOVA' in grr_method:
                r = gage_rr.gage_rr_anova(parts, ops, meas, tolerance)
            else:
                r = gage_rr.gage_rr_crossed(parts, ops, meas, tolerance)

            if 'error' in r:
                st.error(r['error'])
            else:
                # ── ANOVA 特有信息 ──
                if r.get('method') == 'ANOVA':
                    pooling_msg = r.get('pooling_msg')
                    if pooling_msg:
                        st.info(f'💡 {pooling_msg}')
                    elif r.get('interaction_significant'):
                        p_int = r.get('interaction_p_value', 0)
                        st.warning(f'⚠️ 交互效应显著 (p={p_int:.4f})，保留交互项')

                    # ANOVA 表
                    with st.expander('📊 ANOVA 方差分析表', expanded=True):
                        st.table(r['anova_table'])

                # ── 方差分量 (两种方法共用) ──
                st.subheader('📊 方差分量')
                cs = st.columns(5)
                with cs[0]: st.metric('EV 重复性 σ', r['stddev_contributions']['重复性 (EV)'])
                with cs[1]: st.metric('AV 再现性 σ', r['stddev_contributions']['再现性 (AV)'])
                with cs[2]: st.metric('GRR σ', r['stddev_contributions']['GRR'])
                with cs[3]: st.metric('PV 部件 σ', r['stddev_contributions']['部件间 (PV)'])
                with cs[4]: st.metric('ndc', r['ndc'])

                # ── ANOVA 详细方差分量 ──
                if r.get('method') == 'ANOVA' and 'variance_components_detail' in r:
                    with st.expander('🔍 详细方差分量 (σ²)', expanded=False):
                        detail = r['variance_components_detail']
                        cols = st.columns(len(detail))
                        for i, (k, v) in enumerate(detail.items()):
                            with cols[i]:
                                st.metric(k, v)

                st.subheader('📈 变异占比')
                pcts = r['percent_studyvar']
                contribs = r['percent_contribution']
                cs2 = st.columns(4)
                with cs2[0]: st.metric('%GRR (StudyVar)', pcts['%GRR'],
                                       help='标准差比值: σ_GRR / σ_TV × 100')
                with cs2[1]: st.metric('%GRR (贡献率)', contribs['%GRR'],
                                       help='方差比值: σ²_GRR / σ²_TV × 100')
                with cs2[2]: st.metric('评级', r['evaluation'])
                with cs2[3]: st.metric('%PV (StudyVar)',
                                       pcts['%PV'],
                                       help='部件间标准差占比')

                # %Tolerance (如果提供了公差)
                pct_tol = r.get('percent_tolerance')
                if pct_tol:
                    st.subheader('📏 公差占比 (%Tolerance = 5.15σ / Tol × 100)')
                    cs3 = st.columns(4)
                    with cs3[0]: st.metric('%Tol EV', pct_tol['%Tol EV'])
                    with cs3[1]: st.metric('%Tol AV', pct_tol['%Tol AV'])
                    with cs3[2]: st.metric('%Tol GRR', pct_tol['%Tol GRR'])
                    with cs3[3]: st.metric('%Tol PV', pct_tol['%Tol PV'])

                grr_pct = float(r['percent_contributions']['GRR占比 %GRR'].replace('%', ''))
                st.plotly_chart(r['chart'], use_container_width=True)
                with st.expander('📋 评估标准'):
                    st.table(pd.DataFrame({
                        '%GRR': ['< 10%', '10%~30%', '> 30%'],
                        '评级': ['优秀', '临界', '不可接受'],
                        '说明': ['测量系统能力充足', '可接受但可能需改进', '需要改进'],
                        'ndc': ['≥ 5', '2~4', '< 2'],
                    }))

    # --- 计数型 GRR ---
    with t2:
        st.caption('属性一致性分析 — Kappa 统计法')
        st.write('**数据格式要求**: 参考列 (0/1) + 各操作员判定列 (0/1)')

        if not numeric_cols:
            st.error('无可用数值列')
        else:
            ref_col = st.selectbox('参考结果列', numeric_cols, key='attr_ref')
            op_cols = st.multiselect('操作员判定列 (可多选)', [c for c in numeric_cols if c != ref_col],
                                     key='attr_ops')
            if op_cols and st.button('🔍 执行分析', use_container_width=True):
                ref = df[ref_col].values
                appraisers = {c: df[c].values for c in op_cols}
                r = msa_advanced.attribute_gage_rr(ref, appraisers)
                st.plotly_chart(r['chart'], use_container_width=True)
                st.subheader('Kappa 汇总')
                st.table(pd.DataFrame(r['kappa_summary']))
                st.subheader('操作员间一致性')
                st.metric('两两一致性均值', f'{r["between_operators_agreement"]:.1%}')

    # --- 测量不确定度 ---
    with t3:
        st.caption('基于 GUM 法的测量不确定度评定')
        if not numeric_cols:
            st.error('无可用数值列')
        else:
            dc = st.selectbox('重复测量列', numeric_cols, key='unc_col')
            data = df[dc].dropna().values
            c1, c2 = st.columns(2)
            with c1:
                res = st.number_input('仪器分辨率', value=0.001, format='%.6f', key='unc_res')
                cal = st.number_input('校准不确定度 (k=2)', value=0.0, format='%.6f', key='unc_cal')
            with c2:
                tr = st.number_input('温度波动范围 (°C)', value=0.0, step=0.5, key='unc_tr')
                tc = st.number_input('温度系数 (/°C)', value=0.0, format='%.6f', key='unc_tc')
            if st.button('📐 评定不确定度', use_container_width=True):
                r = msa_advanced.measurement_uncertainty(data, res, cal, tr, tc)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.plotly_chart(r['chart'], use_container_width=True)
                    st.subheader('不确定度结果')
                    for k, v in r['result'].items():
                        st.metric(k, v)
                    with st.expander('📊 不确定度预算'):
                        for k, v in r['budget'].items():
                            st.metric(k, v)

    show_data_info()


# ==================== 6. 统计推断 ====================
def page_stats():
    st.header('🔢 统计推断')
    df = check_data()
    if df is None:
        st.warning('⚠️ 请先加载数据')
        return

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    text_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()

    t1, t2, t3, t4 = st.tabs(['正态性检验', '假设检验', '回归分析', '相关性矩阵'])

    # --- 正态性检验 ---
    with t1:
        if not numeric_cols:
            st.error('无数值列')
        else:
            dc = st.selectbox('数据列', numeric_cols, key='norm_col')
            data = df[dc].dropna().values
            if len(data) < 3:
                st.error('至少 3 个数据点')
            else:
                alpha = st.slider('α', 0.01, 0.10, 0.05, 0.01, key='norm_alpha')
                r = pareto_histogram.normality_test(data, alpha)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.subheader('检验结果')
                    cs = st.columns(len(r))
                    for i, (tn, tr) in enumerate(r.items()):
                        with cs[i]:
                            is_n = tr['normal']
                            st.metric(tn, '正态 ✓' if is_n else '非正态 ✗',
                                      delta=f'p={tr.get("p_value", "N/A"):.4f}' if 'p_value' in tr else '',
                                      delta_color='normal' if is_n else 'inverse')
                    hist_r = pareto_histogram.histogram_with_stats(data, '数据分布与正态拟合')
                    if 'chart' in hist_r:
                        st.plotly_chart(hist_r['chart'], use_container_width=True)

    # --- 假设检验 ---
    with t2:
        if not numeric_cols:
            st.error('无数值列')
        else:
            test_type = st.selectbox('检验类型', [
                '单样本 t 检验', '双样本 t 检验 (独立)', '双样本 t 检验 (配对)',
                '单因素 ANOVA', '等方差检验'
            ], key='ht_type')

            if test_type == '单样本 t 检验':
                dc = st.selectbox('数据列', numeric_cols, key='t1_col')
                mu0 = st.number_input('原假设均值 μ₀', value=0.0, key='t1_mu')
                if st.button('执行检验', use_container_width=True):
                    r = stats_tools.t_test_one_sample(df[dc].dropna().values, mu0)
                    if 'error' in r:
                        st.error(r['error'])
                    else:
                        s = '显著 ✓ (拒绝 H₀)' if r['significant'] else '不显著 (保留 H₀)'
                        st.subheader(s)
                        cs = st.columns(5)
                        with cs[0]: st.metric('t 统计量', f'{r["t_stat"]:.4f}')
                        with cs[1]: st.metric('p 值', f'{r["p_val"]:.6f}')
                        with cs[2]: st.metric('样本均值', f'{r["mean"]:.4f}')
                        with cs[3]: st.metric('样本量', r['n'])
                        with cs[4]: st.metric('95% CI', f'[{r["ci_95"][0]:.4f}, {r["ci_95"][1]:.4f}]')

            elif test_type == '双样本 t 检验 (配对)':
                c1, c2 = st.columns(2)
                with c1: dc1 = st.selectbox('样本 1 列', numeric_cols, key='t2a')
                with c2: dc2 = st.selectbox('样本 2 列', numeric_cols, key='t2b')
                if st.button('执行检验', use_container_width=True):
                    r = stats_tools.t_test_two_sample(df[dc1].dropna().values, df[dc2].dropna().values, True)
                    if 'error' in r:
                        st.error(r['error'])
                    else:
                        s = '显著 ✓ (两组有差异)' if r['significant'] else '不显著 (无显著差异)'
                        st.subheader(s)
                        cs = st.columns(6)
                        with cs[0]: st.metric('t', f'{r["t_stat"]:.4f}')
                        with cs[1]: st.metric('p', f'{r["p_val"]:.6f}')
                        with cs[2]: st.metric('均值1', f'{r["mean1"]:.4f}')
                        with cs[3]: st.metric('均值2', f'{r["mean2"]:.4f}')
                        with cs[4]: st.metric('n1', r['n1'])
                        with cs[5]: st.metric('n2', r['n2'])

            elif test_type == '双样本 t 检验 (独立)':
                mode = st.radio('数据格式', ['两列分别存放', '同一列按分组拆分'],
                                horizontal=True, key='t2_ind_mode')
                if mode == '两列分别存放':
                    c1, c2 = st.columns(2)
                    with c1: dc1 = st.selectbox('样本 1 列', numeric_cols, key='t2a')
                    with c2: dc2 = st.selectbox('样本 2 列', numeric_cols, key='t2b')
                    if st.button('执行检验', use_container_width=True):
                        r = stats_tools.t_test_two_sample(
                            df[dc1].dropna().values, df[dc2].dropna().values, False)
                        if 'error' in r:
                            st.error(r['error'])
                        else:
                            s = '显著 ✓ (两组有差异)' if r['significant'] else '不显著 (无显著差异)'
                            st.subheader(s)
                            cs = st.columns(6)
                            with cs[0]: st.metric('t', f'{r["t_stat"]:.4f}')
                            with cs[1]: st.metric('p', f'{r["p_val"]:.6f}')
                            with cs[2]: st.metric('均值1', f'{r["mean1"]:.4f}')
                            with cs[3]: st.metric('均值2', f'{r["mean2"]:.4f}')
                            with cs[4]: st.metric('n1', r['n1'])
                            with cs[5]: st.metric('n2', r['n2'])
                else:
                    st.write('**按分组列拆分同一数值列进行比较**')
                    c1, c2 = st.columns(2)
                    with c1: vc = st.selectbox('数值列', numeric_cols, key='t2_val')
                    with c2: gc = st.selectbox('分组列', text_cols + numeric_cols, key='t2_group')
                    groups = sorted(df[gc].dropna().unique().tolist())
                    if len(groups) < 2:
                        st.warning('分组列至少需要 2 个不同值')
                    else:
                        g1, g2 = st.columns(2)
                        with g1: g1_sel = st.selectbox('组 1', groups, key='t2_g1')
                        with g2: g2_sel = st.selectbox('组 2', groups,
                                                        index=min(1, len(groups) - 1), key='t2_g2')
                        if st.button('执行检验', use_container_width=True):
                            d1 = df[df[gc] == g1_sel][vc].dropna().values
                            d2 = df[df[gc] == g2_sel][vc].dropna().values
                            r = stats_tools.t_test_two_sample(d1, d2, False)
                            if 'error' in r:
                                st.error(r['error'])
                            else:
                                s = '显著 ✓ (两组有差异)' if r['significant'] else '不显著 (无显著差异)'
                                st.subheader(s)
                                cs = st.columns(6)
                                with cs[0]: st.metric('t', f'{r["t_stat"]:.4f}')
                                with cs[1]: st.metric('p', f'{r["p_val"]:.6f}')
                                with cs[2]: st.metric('均值1', f'{r["mean1"]:.4f}')
                                with cs[3]: st.metric('均值2', f'{r["mean2"]:.4f}')
                                with cs[4]: st.metric('n1', r['n1'])
                                with cs[5]: st.metric('n2', r['n2'])

            elif test_type == '单因素 ANOVA':
                st.write('**选择分组列 + 数值列**')
                c1, c2 = st.columns(2)
                with c1:
                    gc = st.selectbox('分组列 (类别)', text_cols + numeric_cols, key='anova_g')
                with c2:
                    vc = st.selectbox('数值列', numeric_cols, key='anova_v')
                if st.button('执行 ANOVA', use_container_width=True):
                    groups = {str(n): g[vc].dropna().values for n, g in df.groupby(gc)}
                    r = stats_tools.one_way_anova(groups)
                    if 'error' in r:
                        st.error(r['error'])
                    else:
                        sig = '显著 ✓ (组间有差异)' if r['significant'] else '不显著 (组间无差异)'
                        st.subheader(sig)
                        st.metric('F 值', f'{r["f_stat"]:.4f}')
                        st.metric('p 值', f'{r["p_val"]:.6f}')
                        st.table(pd.DataFrame(r['anova_table']))
                        st.plotly_chart(r['chart'], use_container_width=True)

            elif test_type == '等方差检验':
                gc = st.selectbox('分组列', text_cols + numeric_cols, key='ev_g')
                vc = st.selectbox('数值列', numeric_cols, key='ev_v')
                if st.button('执行检验', use_container_width=True):
                    groups = {str(n): g[vc].dropna().values for n, g in df.groupby(gc)}
                    r = stats_tools.equal_variance_test(groups)
                    if 'error' in r:
                        st.error(r['error'])
                    else:
                        st.subheader('等方差检验结果')
                        cs = st.columns(2)
                        with cs[0]:
                            lr = r['Levene']
                            st.metric('Levene', '等方差 ✓' if lr['equal'] else '不相等 ✗',
                                      delta=f'p={lr["p_value"]:.4f}')
                        with cs[1]:
                            br = r['Bartlett']
                            st.metric('Bartlett', '等方差 ✓' if br['equal'] else '不相等 ✗',
                                      delta=f'p={br["p_value"]:.4f}')
                        st.write('**各组标准差**')
                        for k, v in r['group_stds'].items():
                            st.metric(k, v)

    # --- 回归分析 ---
    with t3:
        if len(numeric_cols) < 2:
            st.error('至少需要 2 个数值列')
        else:
            reg_type = st.radio('回归类型', ['一元线性回归', '多元线性回归', '多Y-X批量对比'],
                                horizontal=True, key='reg_type')
            if reg_type == '一元线性回归':
                c1, c2 = st.columns(2)
                with c1: xc = st.selectbox('X 轴', numeric_cols, key='s_x')
                with c2: yc = st.selectbox('Y 轴', numeric_cols, index=min(1, len(numeric_cols)-1), key='s_y')
                x, y = df[xc].dropna().values, df[yc].dropna().values
                ml = min(len(x), len(y))
                if ml >= 3:
                    r = pareto_histogram.scatter_plot(x[:ml], y[:ml], x_label=xc, y_label=yc)
                    if 'error' in r:
                        st.error(r['error'])
                    else:
                        st.plotly_chart(r['chart'], use_container_width=True)
                        cs = st.columns(5)
                        with cs[0]: st.metric('斜率', f'{r["slope"]:.4f}')
                        with cs[1]: st.metric('截距', f'{r["intercept"]:.4f}')
                        with cs[2]: st.metric('R²', f'{r["r_squared"]:.4f}')
                        with cs[3]: st.metric('r', f'{r["r_value"]:.4f}')
                        with cs[4]: st.metric('p', f'{r["p_value"]:.6f}')
                        if r['p_value'] < 0.05:
                            st.success('✓ 回归关系显著 (p < 0.05)')
                        else:
                            st.info('回归关系不显著 (p ≥ 0.05)')
            elif reg_type == '多元线性回归':
                yc = st.selectbox('因变量 Y', numeric_cols, key='mr_y')
                r = stats_tools.multiple_regression(df, yc)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.plotly_chart(r['chart'], use_container_width=True)
                    st.subheader('回归系数')
                    st.dataframe(r['coef_df'], use_container_width=True)
                    cs = st.columns(3)
                    with cs[0]: st.metric('R²', f'{r["r_squared"]:.4f}')
                    with cs[1]: st.metric('Adjust R²', f'{r["adj_r2"]:.4f}')
                    with cs[2]: st.metric('样本量', r['n'])
            else:
                # 多 Y-X 批量对比
                st.caption('选择多个变量，一键自动对比所有变量两两之间的回归关系')
                sel_vars = st.multiselect(
                    '对比变量', numeric_cols,
                    default=numeric_cols[:min(4, len(numeric_cols))] if numeric_cols else [],
                    key='yx_sel'
                )

                if len(sel_vars) >= 2:
                    show_grid = st.checkbox('📊 显示散点图矩阵', value=False, key='yx_grid')
                    if st.button('🔍 一键对比', use_container_width=True, key='yx_btn'):
                        with st.spinner('正在计算...'):
                            r = stats_tools.yx_pair_analysis(df, sel_vars, sel_vars,
                                                             show_scatter_grid=show_grid,
                                                             exclude_self=True)
                        if 'error' in r:
                            st.error(r['error'])
                        else:
                            total = r['n_pairs']
                            sig = r['n_significant']
                            st.success(f'共 {len(sel_vars)} 个变量，{total} 对关系中 {sig} 对显著 (p < 0.05)')

                            st.subheader('📈 R² 热力图')
                            st.plotly_chart(r['heatmap'], use_container_width=True)

                            st.subheader('📋 回归结果汇总')
                            summary = r['summary_df'].copy()
                            def highlight_sig(row):
                                if row['显著性'] == '✓ 显著':
                                    return ['background-color: #e6ffe6'] * len(row)
                                return [''] * len(row)
                            st.dataframe(
                                summary.style.apply(highlight_sig, axis=1)
                                .format({'R²': '{:.4f}', '调整R²': '{:.4f}',
                                         'Pearson r': '{:.4f}', '斜率': '{:.4f}',
                                         '截距': '{:.4f}', 'p值': '{:.6f}'}),
                                use_container_width=True, height=35 * (len(summary) + 1) + 3
                            )

                            csv = summary.to_csv(index=False).encode('utf-8-sig')
                            st.download_button(
                                '📥 导出结果 (CSV)', csv, 'yx_pair_analysis.csv',
                                'text/csv', key='yx_dl'
                            )

                            if show_grid and 'scatter_grid' in r:
                                st.subheader('📊 散点图矩阵')
                                st.plotly_chart(r['scatter_grid'], use_container_width=True)
                else:
                    st.info('👆 请至少选择 2 个变量进行对比')

    # --- 相关性矩阵 ---
    with t4:
        if len(numeric_cols) < 2:
            st.error('至少需要 2 个数值列')
        else:
            r = stats_tools.correlation_matrix(df)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.plotly_chart(r['chart'], use_container_width=True)
                st.caption(f'{r["n"]} 样本 · {r["n_cols"]} 变量')
                with st.expander('📋 相关系数表'):
                    st.dataframe(r['corr_df'].style.background_gradient(cmap='RdBu_r', vmin=-1, vmax=1))

    show_data_info()


# ==================== 7. 高级分析 ====================
def page_advanced():
    st.header('🧪 高级分析')
    df = check_data()
    if df is None:
        st.warning('⚠️ 请先加载数据')
        return

    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    all_cols = df.columns.tolist()

    t1, t2, t3, t4 = st.tabs(['DOE 试验设计', 'Weibull 可靠性', '抽样方案', 'FMEA'])

    # --- DOE ---
    with t1:
        st.caption('全因子 DOE — 因子列需为 2 水平')
        if len(numeric_cols) < 2:
            st.warning('需至少 2 列 (因子 + 响应)')
        else:
            rc = st.selectbox('响应变量列', numeric_cols, key='doe_resp')
            r = advanced_analysis.doe_full_factorial(df, rc)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.plotly_chart(r['chart'], use_container_width=True)
                st.subheader('因子效应表')
                st.dataframe(r['effects_table'], use_container_width=True)
                st.caption(f'{r["total_runs"]} 次试验 · {r["factors"]} 个因子')

    # --- Weibull ---
    with t2:
        st.caption('可靠性分析 — 失效时间数据')
        if not numeric_cols:
            st.error('无数值列')
        else:
            dc = st.selectbox('失效时间列', numeric_cols, key='weibull_col')
            r = advanced_analysis.weibull_analysis(df[dc].dropna().values)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.plotly_chart(r['chart'], use_container_width=True)
                cs = st.columns(len(r['params']))
                for i, (k, v) in enumerate(r['params'].items()):
                    cs[i].metric(k, v)

    # --- 抽样方案 ---
    with t3:
        st.caption('OC 曲线分析 — 评估抽样方案效能')
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            N = st.number_input('批数量 N', 10, 100000, 1000, key='sp_N')
        with c2:
            n = st.number_input('样本量 n', 1, N, min(50, N // 5), key='sp_n')
        with c3:
            c_val = st.number_input('合格判定数 Ac', 0, 50, 0, key='sp_c')
        with c4:
            aql = st.number_input('AQL (%)', 0.01, 10.0, 1.0, step=0.1, key='sp_aql')

        if st.button('📊 生成 OC 曲线', use_container_width=True):
            r = advanced_analysis.sampling_plan_oc_curve(N, n, c_val, aql)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.plotly_chart(r['chart'], use_container_width=True)
                with st.expander('📊 抽样参数'):
                    for k, v in r['stats'].items():
                        st.metric(k, v)

    # --- FMEA ---
    with t4:
        st.caption('失效模式与影响分析 — RPN 风险评估')
        st.write('**输入 FMEA 数据**')

        # 检查当前数据是否已是 FMEA 格式
        is_fmea_format = all(c in df.columns for c in ['模式', '严重度', '发生度', '探测度'])

        if is_fmea_format:
            st.success('✅ 检测到 FMEA 格式数据')
            if st.button('🔍 分析当前数据', use_container_width=True, type='primary'):
                fmea_data = df[['模式', '严重度', '发生度', '探测度']].to_dict('records')
                r = advanced_analysis.fmea_analysis(fmea_data)
                if 'error' in r:
                    st.error(r['error'])
                else:
                    st.plotly_chart(r['chart'], use_container_width=True)
                    st.subheader('风险评估汇总')
                    cs = st.columns(4)
                    with cs[0]: st.metric('总失效模式', r['summary']['总失效模式'])
                    with cs[1]: st.metric('高风险', r['summary']['高风险 (RPN≥200)'], delta_color='inverse')
                    with cs[2]: st.metric('中风险', r['summary']['中风险 (100≤RPN<200)'])
                    with cs[3]: st.metric('低风险', r['summary']['低风险 (RPN<100)'])

                    st.subheader('TOP 3 风险项')
                    st.table(pd.DataFrame(r['top_risks']))
                    st.dataframe(r['fmea_df'], use_container_width=True)
        else:
            st.info('💡 请在「数据导入」中加载 FMEA 示例数据，或手动输入以下格式：')
            st.caption('列: 模式, 严重度, 发生度, 探测度')

            # 手动输入
            with st.form('fmea_form'):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    mode = st.text_input('失效模式', key='fm_mode')
                with c2:
                    sev = st.number_input('严重度 S (1-10)', 1, 10, 5, key='fm_sev')
                with c3:
                    occ = st.number_input('发生度 O (1-10)', 1, 10, 5, key='fm_occ')
                with c4:
                    det = st.number_input('探测度 D (1-10)', 1, 10, 5, key='fm_det')
                if st.form_submit_button('➕ 添加'):
                    if 'fmea_records' not in st.session_state:
                        st.session_state.fmea_records = []
                    st.session_state.fmea_records.append({
                        '模式': mode or f'模式{len(st.session_state.fmea_records)+1}',
                        '严重度': sev, '发生度': occ, '探测度': det
                    })
                    st.success('已添加')
                    st.rerun()

            if 'fmea_records' in st.session_state and st.session_state.fmea_records:
                st.write(f'已录入 {len(st.session_state.fmea_records)} 条记录')
                st.dataframe(pd.DataFrame(st.session_state.fmea_records), use_container_width=True)
                if st.button('🔍 执行 FMEA 分析', use_container_width=True, type='primary'):
                    r = advanced_analysis.fmea_analysis(st.session_state.fmea_records)
                    if 'error' in r:
                        st.error(r['error'])
                    else:
                        st.plotly_chart(r['chart'], use_container_width=True)
                        st.subheader('风险评估汇总')
                        cs = st.columns(4)
                        with cs[0]: st.metric('总模式', r['summary']['总失效模式'])
                        with cs[1]: st.metric('高风险', r['summary']['高风险 (RPN≥200)'])
                        with cs[2]: st.metric('中风险', r['summary']['中风险 (100≤RPN<200)'])
                        with cs[3]: st.metric('低风险', r['summary']['低风险 (RPN<100)'])
                        st.subheader('TOP 3 风险项')
                        st.table(pd.DataFrame(r['top_risks']))
                        st.dataframe(r['fmea_df'], use_container_width=True)

                if st.button('🗑️ 清空 FMEA 记录'):
                    st.session_state.fmea_records = []
                    st.rerun()

    show_data_info()


# ==================== 批量分析与报告 ====================

# 各数据类型推荐的分析模块映射
TYPE_MODULE_MAP = {
    'pareto':     [('📊 质量图形工具', '帕累托图/直方图')],
    'grr':        [('🔬 测量系统分析 MSA', 'Gage R&R')],
    'component':  [('📈 SPC 控制图', 'SPC/EWMA/CUSUM'),
                   ('🎯 过程能力分析', 'Cp/Cpk'),
                   ('🔢 统计推断', '相关性/回归')],
    'mechanics':  [('📈 SPC 控制图', 'SPC/EWMA/CUSUM'),
                   ('🎯 过程能力分析', 'Cp/Cpk'),
                   ('🔢 统计推断', '相关性/回归')],
    'dimension':  [('📈 SPC 控制图', 'SPC/EWMA/CUSUM'),
                   ('🎯 过程能力分析', 'Cp/Cpk')],
    'continuous': [('📈 SPC 控制图', 'SPC/EWMA/CUSUM'),
                   ('🎯 过程能力分析', 'Cp/Cpk')],
}


def _parse_preview_df(uploaded_file):
    """解析上传文件为 DataFrame 用于预览，返回 (df, None) 或 (None, error_msg)"""
    raw_bytes = uploaded_file.getvalue()
    df = None
    for enc in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'gb18030', 'latin-1']:
        try:
            df = pd.read_csv(BytesIO(raw_bytes), encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    return df, None if df is not None else '无法解析编码'


def _show_analysis_detail(analysis, data_dict, file_idx):
    """渲染单个文件的分析详情（复用于上传分析和历史报告查看）"""
    atype = analysis.get('type', 'unknown')
    fname = analysis.get('filename', '')
    dtype_label = batch_analysis.ALL_MODULES.get(analysis.get('data_type', atype), {}).get('label', atype)
    modules = analysis.get('modules_selected', [])
    modules_str = ', '.join([batch_analysis.ALL_MODULES.get(m, {}).get('label', m) for m in modules]) if modules else '全部模块'

    if atype == 'error':
        st.error(f'❌ {analysis.get("error", "分析错误")}')
        return

    st.caption(f'🔍 分析类型: {dtype_label}' + (f' | 模块: {modules_str}' if modules else ''))

    # ---- 推荐跳转模块 ----
    recommended = TYPE_MODULE_MAP.get(analysis.get('data_type', atype), [])
    if recommended:
        with st.expander('🚀 跳转到分析模块', expanded=False):
            st.caption('点击下方按钮，将数据加载到工作区，然后用侧边栏切换到对应模块：')
            cols = st.columns(len(recommended))
            df = data_dict.get(fname)
            for idx, (mod_name, mod_desc) in enumerate(recommended):
                with cols[idx]:
                    if st.button(f'📂 预加载数据\n→ {mod_name}',
                                 key=f'jump_{file_idx}_{idx}',
                                 use_container_width=True,
                                 help=f'数据已加载，请在侧边栏选择「{mod_name}」查看'):
                        if df is not None:
                            set_new_data(df)
                            st.success(f'✅ 数据已加载！请在左侧侧边栏选择「{mod_name}」模块进行深度分析')
                            st.info(f'👈 左侧菜单 → {mod_name}')

    # ===== 各类型分析详情 =====
    if atype == 'pareto':
        result = analysis.get('result', {})
        if 'chart' in result:
            st.plotly_chart(result['chart'], use_container_width=True)
        st.caption(f"总缺陷数: {analysis['summary'].get('总缺陷数', 'N/A')}")

    elif atype == 'grr':
        saved_params = st.session_state.get('batch_param_map', {}).get(fname, {})
        saved_method = saved_params.get('grr_method', '平均值-极差法 (X-bar R)')
        grr_method = st.radio(
            '分析方法', ['平均值-极差法 (X-bar R)', 'ANOVA 法 (方差分析)'],
            horizontal=True,
            index=0 if 'ANOVA' not in saved_method else 1,
            key=f'batch_grr_method_{file_idx}'
        )
        result_key = 'result_anova' if 'ANOVA' in grr_method else 'result_xbar'
        result = analysis.get(result_key, {})

        if 'chart' in result:
            st.plotly_chart(result['chart'], use_container_width=True)

        std_contrib = result.get('stddev_contributions', {})
        if std_contrib:
            st.caption('**方差分量**')
            cs = st.columns(5)
            with cs[0]: st.metric('重复性 EV', std_contrib.get('重复性 (EV)', 'N/A'))
            with cs[1]: st.metric('再现性 AV', std_contrib.get('再现性 (AV)', 'N/A'))
            with cs[2]: st.metric('GRR σ', std_contrib.get('GRR', 'N/A'))
            with cs[3]: st.metric('部件 PV', std_contrib.get('部件间 (PV)', 'N/A'))
            with cs[4]: st.metric('ndc', result.get('ndc', 'N/A'))

        pcts = result.get('percent_studyvar', {})
        contribs = result.get('percent_contribution', {})
        if pcts:
            cs2 = st.columns(4)
            with cs2[0]: st.metric('%GRR (StudyVar)', pcts.get('%GRR', 'N/A'))
            with cs2[1]: st.metric('%GRR (贡献率)', contribs.get('%GRR', 'N/A'))
            with cs2[2]: st.metric('评级', result.get('evaluation', 'N/A'))
            with cs2[3]: st.metric('%PV', pcts.get('%PV', 'N/A'))

        if 'ANOVA' in grr_method and 'anova_table' in result:
            with st.expander('ANOVA 方差分析表'):
                st.table(result['anova_table'])

    elif atype in ('component', 'mechanics', 'continuous'):
        results = analysis.get('results', {})

        # ---- SPC 休哈特控制图（支持全部7种子类型）----
        spc_result = results.get('spc', {})
        if isinstance(spc_result, dict) and spc_result:
            # 汇总表
            spc_summary = spc_result.get('summary', [])
            if spc_summary:
                st.caption('**SPC 控制图分析**')
                st.dataframe(pd.DataFrame(spc_summary), use_container_width=True, hide_index=True)
                bad_spc = [s for s in spc_summary if '超限' in s.get('受控状态', '')]
                if bad_spc:
                    st.warning(f'{len(bad_spc)} 个变量存在超限点')

            # 各子类型的图表（I-MR, X-bar R, X-bar S, P, NP, C, U）
            sub_keys = ['imr', 'xbar_r', 'xbar_s', 'p', 'np', 'c', 'u']
            for sk in sub_keys:
                sub_data = spc_result.get(sk, {})
                sub_charts = sub_data.get('charts', {}) if isinstance(sub_data, dict) else {}
                sub_summary = sub_data.get('summary', []) if isinstance(sub_data, dict) else []
                if sub_charts:
                    label = batch_analysis.SPC_SUB_MODES.get(sk, {}).get('label', sk)
                    if sub_summary:
                        st.caption(f'**{label}**')
                        st.dataframe(pd.DataFrame(sub_summary), use_container_width=True, hide_index=True)
                    # 按列分组显示图表，每组加标题区分
                    for chart_key, chart in sub_charts.items():
                        if chart:
                            st.subheader(f'📐 {chart_key}')
                            st.plotly_chart(chart, use_container_width=True,
                                           key=f'batch_spc_{sk}_{file_idx}_{chart_key}')
        elif isinstance(spc_result, list):
            # 向后兼容：旧格式（普通列表）
            spc_list = spc_result
            if spc_list:
                st.caption('**SPC 控制图分析**')
                st.dataframe(pd.DataFrame(spc_list), use_container_width=True, hide_index=True)
                bad_spc = [s for s in spc_list if '超限' in s.get('受控状态', '')]
                if bad_spc:
                    st.warning(f'{len(bad_spc)} 个变量存在超限点')
                    df = data_dict.get(fname)
                    if df is not None:
                        for s in bad_spc[:2]:
                            col = s['列名']
                            if col in df.columns:
                                data = df[col].dropna().values
                                if len(data) >= 2:
                                    r = spc_charts.imr_chart(data)
                                    st.caption(f'{col} — I-MR 控制图')
                                    st.plotly_chart(r['chart'], use_container_width=True,
                                                   key=f'batch_imr_{file_idx}_{col}')

        # 过程能力
        cap_list = results.get('capability', [])
        if cap_list:
            st.caption('**过程能力分析**')
            st.dataframe(pd.DataFrame(cap_list), use_container_width=True, hide_index=True)

        # 相关性矩阵
        corr = results.get('correlation')
        if corr and 'chart' in corr:
            st.caption('**相关性矩阵**')
            st.plotly_chart(corr['chart'], use_container_width=True, key=f'batch_corr_{file_idx}')

        # 回归关系
        reg_list = results.get('regression', [])
        if reg_list:
            st.caption('**显著回归关系 (p < 0.05)**')
            st.dataframe(pd.DataFrame(reg_list), use_container_width=True, hide_index=True)

        # 正态性检验
        normality_list = results.get('normality', [])
        if normality_list:
            st.caption('**正态性检验 (Shapiro-Wilk)**')
            st.dataframe(pd.DataFrame(normality_list), use_container_width=True, hide_index=True)
            non_normal = [n for n in normality_list if '❌' in n.get('正态性', '')]
            if non_normal:
                st.warning(f'{len(non_normal)} 个变量不服从正态分布: {", ".join([n["列名"] for n in non_normal])}')

        # 箱线图
        boxplot_result = results.get('boxplot', {})
        box_charts = boxplot_result.get('charts', {})
        if box_charts:
            st.caption('**箱线图**')
            bp_cols = list(box_charts.keys())
            bp_per_row = min(3, len(bp_cols))
            if bp_per_row > 0:
                bp_rows = (len(bp_cols) + bp_per_row - 1) // bp_per_row
                for ri in range(bp_rows):
                    cs = st.columns(bp_per_row)
                    for ci in range(bp_per_row):
                        vi = ri * bp_per_row + ci
                        if vi < len(bp_cols):
                            with cs[ci]:
                                st.plotly_chart(box_charts[bp_cols[vi]], use_container_width=True,
                                               key=f'batch_box_{file_idx}_{bp_cols[vi]}')

        # 运行图
        runchart_list = results.get('run_chart', [])
        if runchart_list:
            st.caption('**运行图分析**')
            st.dataframe(pd.DataFrame(runchart_list), use_container_width=True, hide_index=True)

        # 描述性统计
        stats_list = results.get('stats_summary', [])
        if stats_list:
            st.caption('**描述性统计**')
            st.dataframe(pd.DataFrame(stats_list), use_container_width=True, hide_index=True)

        # 直方图
        hist_result = results.get('histogram', {})
        hist_charts = hist_result.get('charts', {})
        if hist_charts:
            st.caption('**直方图 (含正态拟合)**')
            h_cols = list(hist_charts.keys())
            h_per_row = min(3, len(h_cols))
            if h_per_row > 0:
                h_rows = (len(h_cols) + h_per_row - 1) // h_per_row
                for ri in range(h_rows):
                    cs = st.columns(h_per_row)
                    for ci in range(h_per_row):
                        vi = ri * h_per_row + ci
                        if vi < len(h_cols):
                            with cs[ci]:
                                st.plotly_chart(hist_charts[h_cols[vi]], use_container_width=True,
                                               key=f'batch_hist_{file_idx}_{h_cols[vi]}')

        # EWMA 控制图
        ewma_result = results.get('ewma', {})
        ewma_charts = ewma_result.get('charts', {})
        ewma_summary = ewma_result.get('summary', [])
        if ewma_summary:
            st.caption('**EWMA 控制图**')
            st.dataframe(pd.DataFrame(ewma_summary), use_container_width=True, hide_index=True)
        if ewma_charts:
            for col, chart in list(ewma_charts.items())[:3]:
                st.plotly_chart(chart, use_container_width=True, key=f'batch_ewma_{file_idx}_{col}')

        # CUSUM 控制图
        cusum_result = results.get('cusum', {})
        cusum_charts = cusum_result.get('charts', {})
        cusum_summary = cusum_result.get('summary', [])
        if cusum_summary:
            st.caption('**CUSUM 控制图**')
            st.dataframe(pd.DataFrame(cusum_summary), use_container_width=True, hide_index=True)
        if cusum_charts:
            for col, chart in list(cusum_charts.items())[:3]:
                st.plotly_chart(chart, use_container_width=True, key=f'batch_cusum_{file_idx}_{col}')

        # Box-Cox 过程能力
        boxcox_result = results.get('box_cox', {})
        boxcox_charts = boxcox_result.get('charts', {})
        boxcox_summary = boxcox_result.get('summary', [])
        if boxcox_summary:
            st.caption('**Box-Cox 变换过程能力**')
            st.dataframe(pd.DataFrame(boxcox_summary), use_container_width=True, hide_index=True)
        if boxcox_charts:
            for col, chart in list(boxcox_charts.items())[:3]:
                st.plotly_chart(chart, use_container_width=True, key=f'batch_bc_{file_idx}_{col}')

        # Cg/Cgk 检具能力
        cg_result = results.get('cg_cgk', {})
        cg_charts = cg_result.get('charts', {})
        cg_summary = cg_result.get('summary', [])
        if cg_summary:
            st.caption('**Cg/Cgk 检具能力**')
            st.dataframe(pd.DataFrame(cg_summary), use_container_width=True, hide_index=True)
        if cg_charts:
            for col, chart in list(cg_charts.items())[:2]:
                st.plotly_chart(chart, use_container_width=True, key=f'batch_cg_{file_idx}_{col}')

        # Weibull 可靠性
        weibull_result = results.get('weibull', {})
        weibull_charts = weibull_result.get('charts', {})
        weibull_summary = weibull_result.get('summary', [])
        if weibull_summary:
            st.caption('**Weibull 可靠性分析**')
            st.dataframe(pd.DataFrame(weibull_summary), use_container_width=True, hide_index=True)
        if weibull_charts:
            for col, chart in list(weibull_charts.items())[:2]:
                st.plotly_chart(chart, use_container_width=True, key=f'batch_wbl_{file_idx}_{col}')

        # 测量不确定度
        unc_result = results.get('uncertainty', {})
        unc_charts = unc_result.get('charts', {})
        unc_summary = unc_result.get('summary', [])
        if unc_summary:
            st.caption('**测量不确定度 (GUM)**')
            st.dataframe(pd.DataFrame(unc_summary), use_container_width=True, hide_index=True)
        if unc_charts:
            for col, chart in list(unc_charts.items())[:2]:
                st.plotly_chart(chart, use_container_width=True, key=f'batch_unc_{file_idx}_{col}')

        # 分布直方图
        st.caption('**数据分布**')
        df = data_dict.get(fname)
        if df is not None:
            # 优先使用分析结果中已过滤的数值列（尊重用户在参数配置中的选择）
            numeric_cols = analysis.get('numeric_cols')
            if numeric_cols is None:
                numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            # 排除明显的批次列
            numeric_cols = [c for c in numeric_cols if not any(k in str(c).lower() for k in ['批次', 'batch', '批号'])]
            cols_per_row = min(3, len(numeric_cols))
            if cols_per_row > 0:
                rows = (len(numeric_cols) + cols_per_row - 1) // cols_per_row
                for ri in range(rows):
                    cs = st.columns(cols_per_row)
                    for ci in range(cols_per_row):
                        vi = ri * cols_per_row + ci
                        if vi < len(numeric_cols):
                            col = numeric_cols[vi]
                            with cs[ci]:
                                data = df[col].dropna().values
                                if len(data) >= 3:
                                    try:
                                        hist_r = pareto_histogram.histogram_with_stats(data, title=col)
                                        if 'chart' in hist_r:
                                            st.plotly_chart(hist_r['chart'], use_container_width=True,
                                                           key=f'batch_hist_{file_idx}_{col}')
                                    except Exception:
                                        pass

    elif atype == 'dimension':
        summary = analysis.get('summary', {})
        cs = st.columns(4)
        with cs[0]: st.metric('批次数', summary.get('批次数', 'N/A'))
        with cs[1]: st.metric('测量点/批', summary.get('每批测量次数', 'N/A'))
        with cs[2]: st.metric('整体均值', summary.get('整体均值', 'N/A'))
        with cs[3]: st.metric('整体标准差', summary.get('整体标准差', 'N/A'))

        for spc_key, spc_title in [('spc_overall', '整体 I-MR'), ('spc_means', '批次均值 I-MR')]:
            spc = analysis.get(spc_key)
            if spc and 'chart' in spc:
                st.caption(f'**{spc_title} 控制图**')
                st.plotly_chart(spc['chart'], use_container_width=True, key=f'batch_{spc_key}_{file_idx}')

        cap = analysis.get('capability')
        if cap and 'chart' in cap:
            st.caption('**过程能力分析**')
            st.plotly_chart(cap['chart'], use_container_width=True, key=f'batch_cap_{file_idx}')

    elif atype == 'grr_attribute':
        chart = analysis.get('chart')
        kappa = analysis.get('kappa_summary', [])
        agreement = analysis.get('between_operators_agreement', 0)
        if chart:
            st.plotly_chart(chart, use_container_width=True, key=f'batch_attr_{file_idx}')
        if kappa:
            st.caption('**Kappa 一致性汇总**')
            st.table(pd.DataFrame(kappa))
        st.metric('两两一致性均值', f'{agreement:.1%}')




def _show_config_dialog(fname, file_idx, cols_list, numeric_cols,
                         global_tol, global_ewma_lam, global_ewma_L,
                         global_cusum_k, global_cusum_h, global_cg_pct,
                         global_unc_res, global_unc_cal):
    """弹窗：两步式配置 — Step1选模块 → Step2设参数 → 确认保存"""
    step = st.session_state.get(f'_dlg_step_{file_idx}', 'modules')

    @st.dialog(f'配置分析 — {fname}', width='large')
    def _dlg():
        if step == 'modules':
            _dlg_step_modules(fname, file_idx)
        else:
            _dlg_step_params(fname, file_idx, cols_list, numeric_cols,
                             global_tol, global_ewma_lam, global_ewma_L,
                             global_cusum_k, global_cusum_h, global_cg_pct,
                             global_unc_res, global_unc_cal)
    _dlg()


def _dlg_step_modules(fname, file_idx):
    """Step 1: 分析模块分组选择 — 复用 batch_analysis.render_module_selector 共享组件"""
    st.caption('👇 按分类勾选分析模块，点击「下一步」设置参数')

    current_modules = list(st.session_state.batch_module_map.get(fname, []))

    # 调用共享组件，不显示确认按钮（由外层"下一步"按钮统一控制）
    new_modules = batch_analysis.render_module_selector(
        current_modules=current_modules,
        session_key_prefix=f'dlg_mods_{file_idx}',
        columns=3,
        show_confirm_button=False,
    )

    # ---- 底部按钮（保持原有两步流程） ----
    st.divider()
    c1, c2 = st.columns([2, 1])
    with c1:
        sel_count = len(new_modules)
        if sel_count > 0:
            labels = [batch_analysis.ALL_MODULES[m]['label'] for m in new_modules if m in batch_analysis.ALL_MODULES]
            st.caption(f'已选择 {sel_count} 个模块：' + ' · '.join(labels))
        else:
            st.caption('⚠️ 请至少选择一个分析模块')
    with c2:
        if st.button('下一步：参数设置 →', type='primary',
                     use_container_width=True, key=f'dlg_next_{file_idx}',
                     disabled=len(new_modules) == 0):
            # 清理临时状态
            temp_key = f'dlg_mods_{file_idx}_temp'
            st.session_state.pop(temp_key, None)
            st.session_state.batch_module_map[fname] = new_modules
            st.session_state[f'_dlg_step_{file_idx}'] = 'params'
            st.rerun()


def _dlg_step_params(fname, file_idx, cols_list, numeric_cols,
                      global_tol, global_ewma_lam, global_ewma_L,
                      global_cusum_k, global_cusum_h, global_cg_pct,
                      global_unc_res, global_unc_cal):
    """Step 2: 参数配置（根据已选模块动态显示）"""
    modules = st.session_state.batch_module_map.get(fname, [])
    params = dict(st.session_state.batch_param_map.get(fname, {}))

    st.caption(f'为 **{fname}** 配置分析参数')

    # === 帕累托 ===
    if 'pareto' in modules:
        st.caption('▸ **帕累托图**')
        if len(cols_list) == 2:
            params['cat_col'] = cols_list[0]
            params['cnt_col'] = cols_list[1]
            st.success(f'自动匹配: 类别={cols_list[0]}, 数量={cols_list[1]}')
        else:
            pc1, pc2 = st.columns(2)
            with pc1:
                params['cat_col'] = st.selectbox('类别列', cols_list, key=f'dlg_par_cat_{file_idx}')
            with pc2:
                remain = [c for c in cols_list if c != params.get('cat_col')]
                params['cnt_col'] = st.selectbox('数量列', remain, key=f'dlg_par_cnt_{file_idx}')

    # === 计量型 GRR ===
    if 'grr' in modules:
        st.caption('▸ **计量型 Gage R&R**')
        # 分析方法选择
        saved_method = params.get('grr_method', '平均值-极差法 (X-bar R)')
        params['grr_method'] = st.radio(
            '分析方法',
            ['平均值-极差法 (X-bar R)', 'ANOVA 法 (方差分析)'],
            horizontal=True,
            index=0 if 'ANOVA' not in saved_method else 1,
            key=f'dlg_grr_method_{file_idx}'
        )
        if len(cols_list) >= 3 and all(
            kw in ' '.join(c.lower() for c in cols_list)
            for kw in ['part', 'operator', 'measurement']
        ):
            params['part_col'] = cols_list[0]
            params['op_col'] = cols_list[1]
            params['meas_col'] = cols_list[2]
            st.success(f'自动匹配: P={cols_list[0]}, Op={cols_list[1]}, M={cols_list[2]}')
        else:
            gc1, gc2, gc3 = st.columns(3)
            with gc1:
                params['part_col'] = st.selectbox('Part列', cols_list, key=f'dlg_grr_part_{file_idx}')
            with gc2:
                params['op_col'] = st.selectbox('Operator列',
                    [c for c in cols_list if c != params.get('part_col')], key=f'dlg_grr_op_{file_idx}')
            with gc3:
                params['meas_col'] = st.selectbox('Measurement列',
                    [c for c in cols_list if c not in (params.get('part_col'), params.get('op_col'))],
                    key=f'dlg_grr_meas_{file_idx}')
        grr_tol = st.text_input('公差 (USL-LSL)', placeholder='留空=不计算%Tol',
                                 key=f'dlg_grr_tol_{file_idx}',
                                 value=global_tol if global_tol else '')
        if grr_tol:
            params['tolerance'] = grr_tol

    # === 计数型 GRR ===
    if 'grr_attribute' in modules:
        st.caption('▸ **计数型 Gage R&R** (属性一致性 Kappa)')
        params['ref_col'] = st.selectbox('参考列 (0/1)', numeric_cols, key=f'dlg_attr_ref_{file_idx}')
        params['op_cols'] = st.multiselect('操作员判定列',
            [c for c in numeric_cols if c != params.get('ref_col')],
            default=[c for c in numeric_cols if c != params.get('ref_col')][:3],
            key=f'dlg_attr_ops_{file_idx}')

    # === 型材尺寸 ===
    if 'dimension' in modules:
        st.caption('▸ **型材尺寸分析**')
        batch_candidates = [c for c in cols_list if '批' in str(c) or 'batch' in str(c).lower()]
        default_batch = batch_candidates[0] if batch_candidates else (cols_list[0] if cols_list else None)
        bidx = cols_list.index(default_batch) if default_batch in cols_list else 0
        dc1, dc2 = st.columns(2)
        with dc1:
            params['batch_col'] = st.selectbox('批次列', cols_list, index=bidx, key=f'dlg_dim_batch_{file_idx}')
        with dc2:
            default_meas = [c for c in numeric_cols if c != params.get('batch_col')]
            params['meas_cols'] = st.multiselect('测量值列', numeric_cols,
                                                 default=default_meas, key=f'dlg_dim_meas_{file_idx}')

    # === 连续型通用参数 ===
    continuous_keys = {'histogram', 'boxplot', 'run_chart', 'normality',
                      'correlation', 'regression', 'stats_summary',
                      'spc', 'ewma', 'cusum',
                      'capability', 'box_cox', 'cg_cgk',
                      'uncertainty', 'weibull'}
    has_continuous = any(m in continuous_keys for m in modules)
    if has_continuous and numeric_cols:
        st.caption('▸ **数值分析通用**')
        params['cols'] = st.multiselect('分析列（默认全部）', numeric_cols,
                                        default=numeric_cols[:min(8, len(numeric_cols))],
                                        key=f'dlg_cols_{file_idx}')

        # ----- SPC 休哈特控制图子类型 -----
        if 'spc' in modules:
            st.caption('▸ **SPC 控制图 — 休哈特类型**')
            saved_spc = params.get('spc_sub_modes', ['imr'])
            spc_options = list(batch_analysis.SPC_SUB_MODES.keys())
            spc_sub_modes = st.multiselect(
                '选择控制图类型 (可多选)',
                options=spc_options,
                format_func=lambda k: batch_analysis.SPC_SUB_MODES[k]['label'],
                default=saved_spc,
                key=f'dlg_spc_sub_{file_idx}',
                help='连续型(I-MR/X̄-R/X̄-S)自动按列分析；计数型需配置列映射'
            )
            params['spc_sub_modes'] = spc_sub_modes

            # 子组/目标参数
            has_bar = any(m in spc_sub_modes for m in ('xbar_r', 'xbar_s'))
            has_attr = any(m in spc_sub_modes for m in ('p', 'np', 'c', 'u'))
            if has_bar:
                c_sub1, c_sub2 = st.columns(2)
                with c_sub1:
                    params['spc_subgroup_size'] = st.number_input(
                        '子组大小', 2, 10, params.get('spc_subgroup_size', 5),
                        key=f'dlg_spc_ss_{file_idx}',
                        help='常数表支持 2~10，超出范围将无法绘制')
                with c_sub2:
                    spc_tgt = st.text_input('目标值 (可选)', placeholder='留空=不设',
                                            key=f'dlg_spc_tgt_{file_idx}')
            elif has_attr:
                spc_tgt = st.text_input('目标值 (可选)', placeholder='留空=不设',
                                         key=f'dlg_spc_tgt_{file_idx}')
            else:
                spc_tgt = st.text_input('目标值 (可选)', placeholder='留空=不设',
                                         key=f'dlg_spc_tgt_{file_idx}')
            if spc_tgt:
                try:
                    params['spc_target'] = float(spc_tgt)
                except ValueError:
                    params.pop('spc_target', None)

            # 计数型 SPC 列映射
            if has_attr:
                st.caption('▸ **计数型 SPC 列映射**')
                attr_cfg = params.get('spc_attr_cols', {})
                if 'p' in spc_sub_modes:
                    ac1, ac2 = st.columns(2)
                    with ac1:
                        attr_cfg['p_defect_col'] = st.selectbox(
                            'P图-不良品数列', numeric_cols, index=0,
                            key=f'dlg_spc_p_def_{file_idx}')
                    with ac2:
                        remain_p = [c for c in numeric_cols if c != attr_cfg.get('p_defect_col')]
                        attr_cfg['p_size_col'] = st.selectbox(
                            'P图-样本量列', remain_p if remain_p else numeric_cols,
                            index=0, key=f'dlg_spc_p_sz_{file_idx}')
                if 'np' in spc_sub_modes:
                    attr_cfg['np_col'] = st.selectbox(
                        'NP图-不良品数列', numeric_cols, index=0,
                        key=f'dlg_spc_np_col_{file_idx}')
                    params['spc_np_size'] = st.number_input(
                        'NP图-固定样本量', 10, 100000,
                        params.get('spc_np_size', 100),
                        key=f'dlg_spc_np_sz_{file_idx}')
                if 'c' in spc_sub_modes:
                    attr_cfg['c_col'] = st.selectbox(
                        'C图-缺陷数列', numeric_cols, index=0,
                        key=f'dlg_spc_c_col_{file_idx}')
                if 'u' in spc_sub_modes:
                    uc1, uc2 = st.columns(2)
                    with uc1:
                        attr_cfg['u_defect_col'] = st.selectbox(
                            'U图-缺陷数列', numeric_cols, index=0,
                            key=f'dlg_spc_u_def_{file_idx}')
                    with uc2:
                        remain_u = [c for c in numeric_cols if c != attr_cfg.get('u_defect_col')]
                        attr_cfg['u_size_col'] = st.selectbox(
                            'U图-样本量列', remain_u if remain_u else numeric_cols,
                            index=0, key=f'dlg_spc_u_sz_{file_idx}')
                params['spc_attr_cols'] = attr_cfg

        # 能力分析参数
        has_cap = any(m in modules for m in ('capability', 'box_cox', 'cg_cgk'))
        if has_cap:
            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                cap_usl = st.text_input('规格上限 USL', placeholder='留空=不设',
                                         key=f'dlg_cap_usl_{file_idx}')
            with cc2:
                cap_lsl = st.text_input('规格下限 LSL', placeholder='留空=不设',
                                         key=f'dlg_cap_lsl_{file_idx}')
            with cc3:
                cap_ss = st.number_input('子组大小', 1, 10,
                                         params.get('bc_subgroup', 1),
                                         key=f'dlg_cap_ss_{file_idx}')
            if cap_usl:
                params['usl'] = float(cap_usl)
            if cap_lsl:
                params['lsl'] = float(cap_lsl)
            params['bc_subgroup'] = cap_ss

        # SPC 高级参数
        if any(m in modules for m in ('ewma', 'cusum')):
            params['ewma_lam'] = global_ewma_lam
            params['ewma_L'] = global_ewma_L
            params['cusum_k'] = global_cusum_k
            params['cusum_h'] = global_cusum_h

        # Cg/Cgk
        if 'cg_cgk' in modules:
            if global_tol:
                try:
                    params['cg_tolerance'] = float(global_tol)
                except ValueError:
                    pass
            params['cg_pct'] = global_cg_pct

        # 不确定度
        if 'uncertainty' in modules:
            params['unc_res'] = global_unc_res
            params['unc_cal'] = global_unc_cal

    # ---- 底部按钮 ----
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button('← 返回选择模块', use_container_width=True, key=f'dlg_back_{file_idx}'):
            st.session_state[f'_dlg_step_{file_idx}'] = 'modules'
            st.session_state.batch_param_map[fname] = params
            st.rerun()
    with c2:
        if st.button('✅ 确认保存', type='primary', use_container_width=True,
                     key=f'dlg_save_{file_idx}'):
            st.session_state.batch_param_map[fname] = params
            # 清理弹窗状态
            st.session_state.pop(f'_dlg_open_{file_idx}', None)
            st.session_state.pop(f'_dlg_step_{file_idx}', None)
            st.rerun()


def page_batch_analysis():
    st.header('📋 批量导入与手动分析报告')
    st.caption('上传 CSV 文件，直接选择要执行的分析模块')

    # ---- session 初始化 ----
    for key in ['batch_module_map', 'batch_param_map']:
        if key not in st.session_state:
            st.session_state[key] = {}

    sub = st.segmented_control(
        '视图', ['📤 上传与手动分析', '📂 历史报告'],
        default='📤 上传与手动分析', key='ba_sub', label_visibility='collapsed')
    if sub is None:
        sub = '📤 上传与手动分析'

    # ================================================================
    # 视图 1: 上传与手动分析
    # ================================================================
    if sub == '📤 上传与手动分析':
        uploaded_files = st.file_uploader(
            '📤 选择 CSV 文件（支持多选）',
            type=['csv'],
            accept_multiple_files=True,
            key='batch_uploader',
            help='为每个文件勾选要执行的分析模块'
        )

        if uploaded_files:
            st.info(f'📁 已选择 **{len(uploaded_files)}** 个文件')

            st.subheader('🔧 分析模块设置')
            st.caption('👇 按分析类型分组勾选模块，展开参数设置可调整每个模块的详细配置')


            # ---- 全局参数（所有文件共用） ----
            with st.expander('⚙️ 全局默认参数', expanded=False):
                gc1, gc2, gc3, gc4 = st.columns(4)
                with gc1:
                    global_tolerance = st.text_input('公差 (USL-LSL)', placeholder='用于Cg/Cgk/能力', key='g_tol',
                                                      help='留空=不计算%Tolerance')
                with gc2:
                    global_ewma_lam = st.slider('EWMA λ', 0.05, 1.0, 0.2, 0.05, key='g_ewma_lam',
                                                help='平滑系数，越小越灵敏')
                with gc3:
                    global_ewma_L = st.slider('EWMA 控制限 L', 2.0, 4.0, 2.7, 0.1, key='g_ewma_L')
                with gc4:
                    global_cusum_k = st.slider('CUSUM k (σ)', 0.1, 2.0, 0.5, 0.1, key='g_cusum_k',
                                               help='参考值，检测目标偏移量的一半')
                gc5, gc6, gc7, gc8 = st.columns(4)
                with gc5:
                    global_cusum_h = st.slider('CUSUM h (σ)', 2.0, 8.0, 4.0, 0.5, key='g_cusum_h',
                                               help='决策区间，越大越不敏感')
                with gc6:
                    global_cg_pct = st.selectbox('Cg/Cgk 容差%', [20, 100], index=0, key='g_cg_pct',
                                                 help='20%=VDA5, 100%=完整公差')
                with gc7:
                    global_unc_res = st.number_input('不确定度 分辨率', value=0.001, format='%.6f', key='g_unc_res')
                with gc8:
                    global_unc_cal = st.number_input('不确定度 校准(k=2)', value=0.0, format='%.6f', key='g_unc_cal')

            _build_preview_data = []  # 在循环中收集预览
            _file_metas = {}          # {filename: {cols_list, numeric_cols, df_preview}}

            for i, uf in enumerate(uploaded_files):
                df_preview, err = _parse_preview_df(uf)
                if df_preview is None:
                    st.error(f'❌ {uf.name}: {err}')
                    _file_metas[uf.name] = {'error': err, 'cols_list': [], 'numeric_cols': []}
                    continue

                # 去重：CSV 可能包含同名列（如两个"屈服强度"），pandas 会都保留
                def _dedup(cols):
                    seen = set()
                    return [c for c in cols if not (c in seen or seen.add(c))]
                cols_list = _dedup(df_preview.columns.tolist())
                numeric_cols = _dedup(df_preview.select_dtypes(include=[np.number]).columns.tolist())
                _file_metas[uf.name] = {
                    'cols_list': cols_list,
                    'numeric_cols': numeric_cols,
                    'df_preview': df_preview,
                }



                # ---- 文件信息 + 当前配置摘要 + 配置按钮 ----
                with st.container(border=True):
                    st.markdown(
                        f'**{uf.name}** · `{len(df_preview)}`行 × `{len(cols_list)}`列  '
                        f'| 数值列: `{", ".join(numeric_cols[:4])}{"…" if len(numeric_cols) > 4 else ""}`'
                    )

                    current_modules = st.session_state.batch_module_map.get(uf.name, [])
                    current_params = st.session_state.batch_param_map.get(uf.name, {})

                    if current_modules:
                        selected_labels = [batch_analysis.ALL_MODULES[m]['label']
                                          for m in current_modules if m in batch_analysis.ALL_MODULES]
                        pkeys = [k for k in current_params if k not in (
                            'tolerance', 'ewma_lam', 'ewma_L', 'cusum_k', 'cusum_h',
                            'unc_res', 'unc_cal', 'cg_pct', 'bc_subgroup')]
                        pstr = ', '.join([f'{k}={current_params[k]}' for k in pkeys]) if pkeys else '默认'
                        st.caption(f'✅ 已配置 ({len(current_modules)})：' + ' · '.join(selected_labels))
                        st.caption(f'⚙️ 参数：{pstr[:80]}{"…" if len(pstr) > 80 else ""}')
                    else:
                        st.caption('⚠️ 尚未配置分析模块')

                    cbtn1, cbtn2 = st.columns([1, 4])
                    with cbtn1:
                        if st.button('📋 配置' if not current_modules else '⚙️ 重新配置',
                                     key=f'open_dlg_{i}', use_container_width=True):
                            st.session_state[f'_dlg_open_{i}'] = True
                            st.session_state.pop(f'_dlg_step_{i}', None)  # reset step
                            st.rerun()

                # ---- 弹窗：两步式模块选择 + 参数配置 ----
                if st.session_state.get(f'_dlg_open_{i}', False):
                    _show_config_dialog(uf.name, i, cols_list, numeric_cols,
                                        global_tolerance, global_ewma_lam, global_ewma_L,
                                        global_cusum_k, global_cusum_h, global_cg_pct,
                                        global_unc_res, global_unc_cal)

            # ---- 从 session_state 重建汇总预览 ----
            preview_data = []
            all_ready = True
            for uf in uploaded_files:
                fname = uf.name
                meta = _file_metas.get(fname, {})
                if meta.get('error'):
                    all_ready = False
                    continue
                modules = st.session_state.batch_module_map.get(fname, [])
                params = st.session_state.batch_param_map.get(fname, {})
                if not modules:
                    all_ready = False
                modules_str = ', '.join([batch_analysis.ALL_MODULES.get(m, {}).get('label', m)
                                        for m in modules]) if modules else '-'
                param_keys_shown = [k for k in params if k not in (
                    'tolerance', 'ewma_lam', 'ewma_L', 'cusum_k', 'cusum_h',
                    'unc_res', 'unc_cal', 'cg_pct', 'bc_subgroup')]
                param_str = ', '.join([f'{k}={params[k]}' for k in param_keys_shown]) if param_keys_shown else '默认'
                df_p = meta.get('df_preview')
                preview_data.append({
                    '文件名': fname,
                    '分析模块': modules_str,
                    '参数': param_str[:60] + ('…' if len(param_str) > 60 else ''),
                    '行数': len(df_p) if df_p is not None else '?',
                    '列数': len(meta.get('cols_list', [])),
                })

            # ---- 汇总预览 ----
            with st.expander('📋 汇总预览', expanded=True):
                st.dataframe(pd.DataFrame(preview_data), use_container_width=True, hide_index=True)

            # ---- 分析按钮 ----
            st.divider()
            c1, c2, c3 = st.columns([2, 1, 2])
            with c2:
                btn_disabled = not all_ready
                analyze_btn = st.button(
                    '🚀 开始分析',
                    use_container_width=True,
                    type='primary',
                    key='batch_analyze_btn',
                    disabled=btn_disabled
                )

            if analyze_btn:
                module_selections = {}
                params_map = {}
                grr_tolerance = None
                for uf in uploaded_files:
                    mods = st.session_state.batch_module_map.get(uf.name, [])
                    if mods:
                        module_selections[uf.name] = mods
                    p = st.session_state.batch_param_map.get(uf.name, {})
                    if p:
                        params_map[uf.name] = dict(p)  # 保留 tolerance 等所有参数
                        if 'tolerance' in p and p['tolerance']:
                            try:
                                grr_tolerance = float(p['tolerance'])
                            except ValueError:
                                pass

                with st.spinner('正在导入和分析...'):
                    data_dict, analyses, report = batch_analysis.batch_import_and_analyze(
                        uploaded_files, grr_tolerance,
                        module_selections=module_selections,
                        params_map=params_map
                    )
                    st.session_state.batch_data = data_dict
                    st.session_state.batch_analyses = analyses
                    st.session_state.batch_report = report
                    st.session_state.batch_uploaded_files = uploaded_files
                    st.session_state.batch_module_selections_snapshot = dict(module_selections)
                    st.session_state.batch_params_snapshot = dict(params_map)
                    for fname, df in data_dict.items():
                        set_new_data(df)
                        break
                st.success(f'分析完成！共处理 {len(analyses)} 组分析结果')
                st.rerun()

        # ---- 显示分析结果 ----
        if 'batch_analyses' in st.session_state and st.session_state.batch_analyses:
            analyses = st.session_state.batch_analyses
            data_dict = st.session_state.get('batch_data', {})

            st.divider()
            st.subheader('📊 分析结果')

            result_tabs = st.tabs(
                ['📝 综合报告'] +
                [f'📊 {a.get("filename", f"文件{i+1}")}' for i, a in enumerate(analyses)]
            )

            # === 综合报告 ===
            with result_tabs[0]:
                report = st.session_state.get('batch_report', '')
                if report:
                    st.markdown(report)
                    c1, c2, c3 = st.columns([1, 1, 2])
                    with c1:
                        st.download_button('📥 下载报告 (MD)', report.encode('utf-8-sig'),
                                          '质量综合分析报告.md', 'text/markdown',
                                          key='download_report', use_container_width=True)
                    with c2:
                        if st.button('💾 保存到数据库', key='save_report_db', use_container_width=True, type='primary'):
                            mod_map = st.session_state.get('batch_module_selections_snapshot', {})
                            params_map = st.session_state.get('batch_params_snapshot', {})
                            files_data = batch_analysis.build_files_data(
                                st.session_state.get('batch_uploaded_files', []), None, mod_map, params_map)
                            analyses_summary = batch_analysis.build_analyses_summary(analyses)

                            report_name = f'质量分析报告_{(datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y%m%d_%H%M%S")}'
                            result = supabase_helper.save_report(
                                report_name, report, analyses_summary, files_data, len(analyses))

                            if result:
                                st.success(f'✅ 已保存报告: {report_name}')
                                st.session_state.pop('_db_reports', None)
                            else:
                                st.error('保存失败，可能数据库表未创建')
                                with st.expander('📋 建表 SQL（请在 Supabase SQL Editor 中执行）'):
                                    st.code(supabase_helper.get_create_reports_table_sql(), language='sql')

            # === 各文件详情 ===
            for i, (analysis, t) in enumerate(zip(analyses, result_tabs[1:])):
                with t:
                    _show_analysis_detail(analysis, data_dict, i)

            # 清除
            st.divider()
            if st.button('🗑️ 清除分析结果', key='clear_batch'):
                for k in ['batch_data', 'batch_analyses', 'batch_report',
                          'batch_uploaded_files',
                          'batch_module_selections_snapshot', 'batch_params_snapshot']:
                    st.session_state.pop(k, None)
                st.rerun()

        # 空状态
        if 'batch_analyses' not in st.session_state or not st.session_state.batch_analyses:
            st.info('👆 上传 CSV 文件，手动选择数据类型和分析模块，然后点击「开始分析」')
            with st.expander('📖 支持的分析模块 (19个)', expanded=False):
                st.markdown("""
                | 分类 | 分析模块 | 说明 |
                |------|---------|------|
                | 📊 基础图形 | 帕累托图、直方图、箱线图、运行图 | 缺陷/分布/异常值/趋势分析 |
                | 📈 SPC 控制 | SPC控制图(7种)、EWMA、CUSUM | 过程稳定性和小偏移检测 |
                | 🎯 能力分析 | Cp/Cpk、Box-Cox、Cg/Cgk | 过程能力/非正态/检具评估 |
                | 🔢 统计推断 | 正态性检验、相关性、回归、描述性统计 | 分布检验/关联分析/回归建模 |
                | 🔬 测量系统 | 计量型GRR、计数型GRR、测量不确定度 | MSA 全面评估 |
                | 📏 特殊分析 | 型材尺寸、Weibull可靠性 | 批次尺寸/寿命分析 |
                """)

        show_data_info()

    # ================================================================
    # 视图 2: 历史报告（不变）
    # ================================================================
    else:
        st.subheader('📂 已保存的分析报告')

        table_ok = supabase_helper.ensure_reports_table()

        if st.button('🔄 刷新列表', key='refresh_reports'):
            st.session_state.pop('_db_reports', None)
            st.rerun()

        if '_db_reports' not in st.session_state:
            st.session_state._db_reports = supabase_helper.list_reports()

        reports = st.session_state.get('_db_reports', [])

        if not reports:
            if not table_ok:
                st.warning('⚠️ 数据库表 `analysis_reports` 尚未创建')
                st.info('请在 Supabase Dashboard → SQL Editor 中执行以下 SQL 创建表：')
                st.code(supabase_helper.get_create_reports_table_sql(), language='sql')
            else:
                st.info('暂无已保存的报告。上传分析后点击「💾 保存到数据库」即可。')
        else:
            if 'selected_report_id' not in st.session_state:
                st.session_state.selected_report_id = None

            for rpt in reports:
                rid = rpt.get('id', '')
                rname = rpt.get('name', '未命名')
                fcount = rpt.get('file_count', 0)
                created = str(rpt.get('created_at', ''))[:19]
                analyses_summary = rpt.get('analyses_summary', [])

                summary_parts = []
                if isinstance(analyses_summary, list):
                    for a in analyses_summary:
                        fn = a.get('filename', '?')
                        dt = a.get('data_type', a.get('type', 'unknown'))
                        summary_parts.append(f'{fn} [{batch_analysis.ALL_MODULES.get(dt, {}).get("label", dt)}]')

                with st.expander(f'📋 {rname}  ({created})', expanded=(rid == st.session_state.selected_report_id)):
                    st.caption(f'包含 {fcount} 个文件: {"; ".join(summary_parts[:3])}'
                              f'{"..." if len(summary_parts) > 3 else ""}')

                    c1, c2, c3 = st.columns([1, 1, 1])
                    with c1:
                        if st.button('📖 查看报告', key=f'view_{rid}', use_container_width=True):
                            with st.spinner('加载报告...'):
                                full_rpt = supabase_helper.load_report(rid)
                                if full_rpt:
                                    st.session_state.viewed_report = full_rpt
                                    st.session_state.selected_report_id = rid
                            st.rerun()
                    with c2:
                        if st.button('🔄 重新分析', key=f'reanalyze_{rid}', use_container_width=True):
                            with st.spinner('正在重新分析...'):
                                full_rpt = supabase_helper.load_report(rid)
                                if full_rpt:
                                    files_data = full_rpt.get('files_data', [])
                                    if isinstance(files_data, str):
                                        import json
                                        files_data = json.loads(files_data)
                                    data_dict, analyses = batch_analysis.restore_analyses_from_files(files_data)
                                    if analyses:
                                        report_md = batch_analysis.generate_report(
                                            [a for a in analyses if a.get('type') != 'error'],
                                            [a.get('filename', '') for a in analyses if a.get('type') != 'error']
                                        )
                                        st.session_state.batch_data = data_dict
                                        st.session_state.batch_analyses = analyses
                                        st.session_state.batch_report = report_md
                                        st.session_state.selected_report_id = rid
                                        for fname, df in data_dict.items():
                                            set_new_data(df)
                                            break
                                        st.success(f'已重新分析 {len(analyses)} 个文件')
                            st.rerun()
                    with c3:
                        if st.button('🗑️ 删除', key=f'del_{rid}', use_container_width=True):
                            if supabase_helper.delete_report(rid):
                                st.session_state.pop('_db_reports', None)
                                st.session_state.selected_report_id = None
                                st.session_state.pop('viewed_report', None)
                                st.success('已删除')
                                st.rerun()

            st.divider()

            if 'viewed_report' in st.session_state and st.session_state.viewed_report:
                viewed = st.session_state.viewed_report
                st.subheader(f'📝 {viewed.get("name", "报告详情")}')

                detail_tabs = st.tabs(['📝 Markdown 报告', '📊 交互式分析'])

                with detail_tabs[0]:
                    report_md = viewed.get('report_md', '')
                    if report_md:
                        st.markdown(report_md)
                        st.download_button('📥 下载报告', report_md.encode('utf-8-sig'),
                                          f'{viewed.get("name", "report")}.md', 'text/markdown',
                                          key='dl_history_report')
                    else:
                        st.info('报告中无文字内容')

                with detail_tabs[1]:
                    st.caption('基于已保存数据重新生成交互式图表')
                    if st.button('🔄 加载交互式分析', key='load_interactive', use_container_width=True, type='primary'):
                        with st.spinner('正在分析...'):
                            files_data = viewed.get('files_data', [])
                            if isinstance(files_data, str):
                                import json
                                files_data = json.loads(files_data)
                            data_dict, analyses = batch_analysis.restore_analyses_from_files(files_data)
                            if analyses:
                                valid = [a for a in analyses if a.get('type') != 'error']
                                st.session_state.batch_data = data_dict
                                st.session_state.batch_analyses = analyses
                                st.session_state.batch_report = batch_analysis.generate_report(
                                    valid, [a.get('filename', '') for a in valid]) if valid else ''
                                for fname, df in data_dict.items():
                                    set_new_data(df)
                                    break
                                st.success(f'已加载 {len(analyses)} 个文件的交互分析')
                                st.rerun()

                if st.button('❌ 关闭', key='close_report_view'):
                    st.session_state.pop('viewed_report', None)
                    st.rerun()


# ==================== 送检/检验对比 ====================

# 检验工序类型统一从 inspection_match.INSPECT_TYPE_CONFIGS 读取（新增工序 = 加一段配置即可）


def _load_sub_records(show_type=None):
    """送检记录按工序加载（缓存 key 含工序，切换工序互不污染）"""
    cache_key = f'_sub_records|{show_type or "全部"}'
    recs = st.session_state.get(cache_key)
    if recs is None:
        recs = supabase_helper.list_inspection_submissions(limit=2000, inspect_type=show_type)
        st.session_state[cache_key] = recs
    return recs


def _load_sub_total(show_type=None):
    """送检总数按工序加载（缓存 key 含工序）"""
    cache_key = f'_sub_total|{show_type or "全部"}'
    total = st.session_state.get(cache_key)
    if total is None:
        total = supabase_helper.count_inspection_submissions(inspect_type=show_type)
        st.session_state[cache_key] = total
    return total


def _clear_sub_caches():
    """清空送检页全部工序的缓存（入库/清空后调用）"""
    for k in [k for k in st.session_state.keys()
              if k.startswith('_sub_records|') or k.startswith('_sub_total|')]:
        del st.session_state[k]
    st.session_state._sub_compare = None
    st.session_state._sub_df_cache = None


def _load_compare_records():
    """对比用轻量查询：全部累计送检记录（6 业务列，不含 id）"""
    recs = st.session_state.get('_sub_compare')
    if recs is None:
        recs = supabase_helper.fetch_submission_records()
        st.session_state._sub_compare = recs
    return recs


def _style_unchecked(df):
    """未检验行红底高亮"""
    def _red(row):
        return ['background-color: #fdecea'] * len(row)
    try:
        return df.style.apply(_red, axis=1).hide(axis='index')
    except Exception:
        return df


def _render_submission_tab(inspect_type):
    # 工序切换后重置本 tab 的临时状态，避免把上一工序预览的文件误入库到当前工序
    if st.session_state.get('_sub_active_type') != inspect_type:
        st.session_state._sub_active_type = inspect_type
        for k in ['_sub_fid', '_sub_type', '_sub_df', '_sub_preview', '_sub_import_result']:
            st.session_state[k] = None
        st.session_state._sub_imported = False

    st.session_state.setdefault('_sub_fid', None)
    st.session_state.setdefault('_sub_parse_err', None)
    st.session_state.setdefault('_sub_df', None)
    st.session_state.setdefault('_sub_preview', None)
    st.session_state.setdefault('_sub_imported', False)

    st.caption(f'🧪 当前工序：**{inspect_type}**（顶部按钮切换工序）')
    records = _load_sub_records(inspect_type) or []
    total = _load_sub_total(inspect_type) or 0
    suppliers = len({r.get('supplier') for r in records})
    codes = len({r.get('material_code') for r in records})
    c1, c2, c3 = st.columns(3)
    c1.metric('📦 累计送检记录', total)
    c2.metric('🏭 供应商数', suppliers)
    c3.metric('🔢 物料编码数', codes)

    st.divider()

    uploaded = st.file_uploader(
        f'📤 上传「{inspect_type}」送检清单 Excel（列：供应商 / 物料编码 / 规格型号 / 物料名称 / 收料日期 / 实收数量）',
        type=['xlsx', 'xls'], key=f'sub_upload_{inspect_type}',
        help='收料日期按 Excel 原样保存，留空则保持为空；重复记录（工序+供应商+物料编码+规格型号+物料名称+实收数量一致）不会重复入库')
    if uploaded is not None:
        # 用文件 ID 判断是否新文件：避免每次 rerun 重新解析/查库
        fid = getattr(uploaded, 'file_id', None) or (uploaded.name, uploaded.size)
        if st.session_state.get('_sub_fid') != fid or st.session_state.get('_sub_type') != inspect_type:
            st.session_state._sub_fid = fid
            st.session_state._sub_type = inspect_type
            st.session_state._sub_parse_err = None
            st.session_state._sub_df = None
            st.session_state._sub_preview = None
            st.session_state._sub_import_result = None
            st.session_state._sub_imported = False
            try:
                df = inspection_match.parse_sheet(uploaded, 'submission')
            except Exception as e:
                st.session_state._sub_parse_err = str(e)
            else:
                with st.spinner('正在检查重复记录...'):
                    t0 = time.monotonic()
                    new_df, dup_df = inspection_match.preview_import(df, inspect_type=inspect_type)
                    preview_dt = time.monotonic() - t0
                st.session_state._sub_df = df
                st.session_state._sub_preview = (new_df, dup_df, preview_dt)

    if st.session_state.get('_sub_parse_err'):
        st.error(f'❌ 文件解析失败: {st.session_state._sub_parse_err}')
    elif st.session_state.get('_sub_imported'):
        st.success('✅ 本次上传的文件已处理完成，如需再次上传请选择新文件。')
        res = st.session_state.get('_sub_import_result')
        if res:
            inserted, skipped, total = res
            if inserted > 0:
                st.success(f'已入库 {inserted} 条，跳过重复 {skipped} 条（共解析 {total} 条）')
            elif skipped > 0:
                st.info(f'没有新增记录，{skipped} 条均为重复（共解析 {total} 条）')
            else:
                st.error('入库失败：未写入任何记录，请检查数据库权限或联系管理员')
    elif st.session_state.get('_sub_preview') is not None:
        new_df, dup_df, preview_dt = st.session_state._sub_preview
        df = st.session_state._sub_df
        st.success(f'✅ 解析成功：共 {len(df)} 行 → 将新增 {len(new_df)} 条，重复跳过 {len(dup_df)} 条（预览耗时 {preview_dt:.2f}s）')
        st.dataframe(new_df, use_container_width=True, hide_index=True)
        if st.button('🚀 确认入库', type='primary', key=f'sub_import_btn_{inspect_type}'):
            progress_bar = st.progress(0)
            st.info('准备入库...')
            t0 = time.monotonic()

            def _update(done, total):
                pct = min(1.0, done / total) if total else 0.0
                progress_bar.progress(pct)
                st.info(f'正在写入数据库... {done}/{total}')

            inserted, skipped, _ = inspection_match.import_submissions(
                df, progress=_update, batch_size=1000,
                inspect_type=inspect_type)
            dt = time.monotonic() - t0
            progress_bar.empty()
            if inserted > 0:
                st.success(f'✅ 已入库 {inserted} 条记录（耗时 {dt:.2f}s）'
                           + (f'，跳过重复 {skipped} 条' if skipped else ''))
                _clear_sub_caches()
                st.session_state._sub_preview = None
                st.session_state._sub_import_result = (inserted, skipped, len(df))
                st.session_state._sub_imported = True
                st.rerun()
            elif skipped > 0:
                st.info(f'没有新增记录，{skipped} 条均为重复。')
                _clear_sub_caches()
                st.session_state._sub_preview = None
                st.session_state._sub_import_result = (inserted, skipped, len(df))
                st.session_state._sub_imported = True
                st.rerun()
            else:
                st.error('❌ 入库失败：未写入任何记录。可能是未获取到登录身份，请重新登录后再试。')

    st.divider()
    st.subheader(f'📂「{inspect_type}」已入库送检记录')
    if not records:
        st.info('暂无送检记录，请先上传送检清单。')
        return

    if total and total > len(records):
        st.caption(f'💡 共 {total} 条，仅显示最近 {len(records)} 条')
    df = inspection_match.submissions_to_df(records)
    cols = [c for c in df.columns if c != 'id']
    st.dataframe(df[cols], use_container_width=True, hide_index=True)

    clear_ck_key = f'sub_clear_ck_{inspect_type}'
    clear_btn_key = f'sub_clear_btn_{inspect_type}'
    if st.checkbox(f'⚠️ 确认清空「{inspect_type}」的全部送检记录', key=clear_ck_key):
        if st.button(f'🗑️ 清空「{inspect_type}」送检记录', type='primary', key=clear_btn_key, use_container_width=True):
            if supabase_helper.clear_inspection_submissions(inspect_type=inspect_type):
                _clear_sub_caches()
                st.rerun()


def _render_compare_tab(inspect_type):
    # 工序切换后重置本 tab 的临时状态，避免不同工序文件/结果串扰
    if st.session_state.get('_ins_active_type') != inspect_type:
        st.session_state._ins_active_type = inspect_type
        for k in ['_ins_fid', '_ins_parse_err', '_ins_df', 'inspection_match_result']:
            st.session_state[k] = None

    st.session_state.setdefault('_ins_fid', None)
    st.session_state.setdefault('_ins_parse_err', None)
    st.session_state.setdefault('_ins_df', None)
    st.session_state.setdefault('_ins_db_ok', None)

    records = _load_compare_records()
    if not records:
        st.info('📌 送检清单为空。请先在「📤 送检清单管理」中上传送检清单。')
        return

    # 送检记录转 DataFrame 后缓存，避免每次 rerun 重复转换
    sub_df = st.session_state.get('_sub_df_cache')
    if sub_df is None:
        sub_df = inspection_match.submissions_to_df(records)
        st.session_state._sub_df_cache = sub_df

    # 检验记录库是否可用（惰性检测一次）
    ins_db_ok = st.session_state.get('_ins_db_ok')
    if ins_db_ok is None:
        ins_db_ok = supabase_helper.ensure_inspection_records_table()
        st.session_state._ins_db_ok = ins_db_ok

    st.caption(f'🧪 当前工序：**{inspect_type}**（只会用「{inspect_type}」的送检单和检验单做比对）')

    uploaded = st.file_uploader(
        f'📤 上传「{inspect_type}」检验清单 Excel（列：供应商 / 物料编码 / 规格型号 / 物料名称 / 质检日期 / 检验数量）',
        type=['xlsx', 'xls'], key=f'ins_upload_{inspect_type}',
        help='支持用户 ERP 导出的完整表头（含单据编号、批号、检验结果等），自动抽取核心列并过滤「合计」行；勾选入库后写入检验记录库，可用于跨窗口自动对账')
    if uploaded is not None:
        # 文件 ID 判断是否新文件：避免每次 rerun 重复解析
        fid = getattr(uploaded, 'file_id', None) or (uploaded.name, uploaded.size)
        if st.session_state.get('_ins_fid') != fid or st.session_state.get('_ins_type') != inspect_type:
            st.session_state._ins_type = inspect_type
            st.session_state._ins_fid = fid
            st.session_state._ins_parse_err = None
            st.session_state._ins_df = None
            st.session_state.inspection_match_result = None  # 新文件 → 旧结果作废
            try:
                ins_df = inspection_match.parse_inspection_full(uploaded)
            except Exception as e:
                st.session_state._ins_parse_err = str(e)
            else:
                ins_df = ins_df.copy()
                ins_df['检验类型'] = inspect_type
                st.session_state._ins_df = ins_df

    if st.session_state.get('_ins_parse_err'):
        st.error(f'❌ 文件解析失败: {st.session_state._ins_parse_err}')
    elif st.session_state.get('_ins_df') is not None:
        ins_df = st.session_state._ins_df
        st.success(f'✅ 检验清单解析成功：共 {len(ins_df)} 行（送检库 {len(sub_df)} 条）。')

        store_cb = st.checkbox(
            '📥 同时写入检验记录库（持久化，供后续跨窗口对账）',
            value=True, key='ins_store_cb',
            help='入库后检验单会保存下来（单据编号+供应商+编码+日期+数量 去重，重复上传不叠加），下次对账可自动关联')
        if store_cb and not ins_db_ok:
            st.warning('⚠️ 检验记录库表 `inspection_records` 尚未创建，无法入库。请先在 Supabase SQL Editor 执行下方 SQL：')
            st.code(supabase_helper.get_create_inspection_records_table_sql(), language='sql')
            if st.button('🔄 已执行 SQL，重新检测', key='ins_db_recheck'):
                st.session_state._ins_db_ok = supabase_helper.ensure_inspection_records_table()
                st.rerun()
        reconcile_cb = st.checkbox(
            '🔄 使用累计检验记录对账（跨窗口，包含历史已入库的检验单）',
            value=True, key='ins_reconcile_cb',
            help='开启后会自动关联历史入库的检验单，解决「检验单质检日期早于/晚于本次文件范围」导致的误判')
        if st.button('🚀 开始比对', type='primary', key='ins_compare_btn'):
            progress_bar = st.progress(0)
            st.info('准备对比...')
            t0 = time.monotonic()

            def _update(done, total):
                pct = min(1.0, done / total) if total else 0.0
                progress_bar.progress(pct)
                st.info(f'正在比对... {done}/{total} 条送检记录')

            # 1) 检验记录入库
            if store_cb and ins_db_ok:
                inserted, skipped, _total = inspection_match.import_inspection_records(
                    ins_df, source_file=getattr(uploaded, 'name', ''), inspect_type=inspect_type)
                st.info(f'📥 检验记录入库：新增 {inserted} 条，跳过重复 {skipped} 条')

            # 2) 组装对账数据源
            ins_src = ins_df
            if reconcile_cb:
                db_recs = supabase_helper.fetch_inspection_records()
                if db_recs:
                    db_df = inspection_match.inspection_records_to_df(db_recs)
                    if store_cb and ins_db_ok:
                        ins_src = db_df  # 已含本次入库记录
                    else:
                        ins_src = pd.concat([ins_df, db_df], ignore_index=True).drop_duplicates()
                    st.info(f'📚 跨窗口对账：共使用 {len(ins_src)} 条检验记录（含历史入库）')

            result = inspection_match.compare(sub_df, ins_src, progress=_update,
                                              inspect_type=inspect_type)
            dt = time.monotonic() - t0
            progress_bar.empty()
            st.session_state.inspection_match_result = result
            st.session_state.inspection_match_dt = dt
            st.rerun()

    result = st.session_state.get('inspection_match_result')
    if result is None:
        st.info('👆 上传检验清单后点击「🚀 开始比对」')
        return
    if not isinstance(result, dict) or 'summary' not in result:
        # 旧版本残留的脏数据 → 清掉，避免渲染崩溃
        st.session_state.inspection_match_result = None
        st.info('检测到旧会话残留的比对结果，已清除。请重新上传检验清单并比对。')
        return

    s = result['summary']
    dt = st.session_state.get('inspection_match_dt', 0.0)
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric('🧾 送检总数', s['total_sub'])
    col2.metric('✅ 已检验', s['checked'])
    col3.metric('⚠️ 未检验', s['unchecked'])
    col4.metric('📋 额外检验', s['extra'])
    col5.metric('🆔 名称不一致', s['name_mismatch'])
    st.caption(f'⏱️ 本次比对耗时 {dt:.2f}s')

    c1, c2 = st.columns(2)
    with c1:
        if s['unchecked']:
            st.download_button('⚠️ 下载未检验清单',
                               inspection_match.export_unchecked(result),
                               f"未检验清单_{date.today()}.xlsx",
                               'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                               key='dl_unchecked', use_container_width=True)
    with c2:
        st.download_button('📊 下载全部对比结果',
                           inspection_match.export_all(result),
                           f"检验对比_{date.today()}.xlsx",
                           'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           key='dl_all', use_container_width=True)

    st.divider()

    st.subheader('⚠️ 未检验物料')
    if s['unchecked']:
        st.dataframe(_style_unchecked(result['unchecked']), use_container_width=True)
    else:
        st.success('🎉 所有送检物料均已检验！')

    st.subheader('✅ 已检验物料')
    if s['checked']:
        st.dataframe(result['checked'], use_container_width=True, hide_index=True)
    else:
        st.info('无已检验记录')

    st.subheader('🆔 名称不一致（需人工判断）')
    if s['name_mismatch']:
        st.warning('以下物料编码在送检与检验清单中的「物料名称」不一致，请人工核对确认归属：')
        c1, c2 = st.columns(2)
        with c1:
            st.markdown('**送检侧**')
            st.dataframe(result['name_mismatch']['sub'], use_container_width=True, hide_index=True)
        with c2:
            st.markdown('**检验侧**')
            st.dataframe(result['name_mismatch']['ins'], use_container_width=True, hide_index=True)
    else:
        st.info('无')

    st.subheader('📋 额外检验（送检清单中不存在）')
    if s['extra']:
        st.dataframe(result['extra'], use_container_width=True, hide_index=True)
    else:
        st.info('无')

    if st.button('🗑️ 清除对比结果', key='clear_compare'):
        st.session_state.inspection_match_result = None
        st.rerun()


def _render_inspection_records_tab(inspect_type):
    """📋 检验记录库：查看/清空已持久化的检验记录"""
    st.subheader(f'📋「{inspect_type}」检验记录库（持久化）')
    if not supabase_helper.ensure_inspection_records_table():
        st.warning('⚠️ 检验记录库表 `inspection_records` 尚未创建，请先在 Supabase SQL Editor 中执行：')
        st.code(supabase_helper.get_create_inspection_records_table_sql(), language='sql')
        return

    st.caption(f'🧪 当前工序：**{inspect_type}**')
    total = supabase_helper.count_inspection_records(inspect_type=inspect_type)
    st.caption(f'已入库检验记录：**{total}** 条。比对时勾选「🔄 跨窗口对账」即可自动关联这些累计检验单。')

    if total == 0:
        st.info('暂无检验记录。请到「⚖️ 检验对比」上传检验清单并勾选「📥 写入检验记录库」。')
        return

    records = supabase_helper.list_inspection_records(limit=1000, inspect_type=inspect_type)
    if not records:
        st.info('暂无检验记录')
        return
    df = inspection_match.inspection_records_to_df(records)
    show_cols = ['检验类型', '单据编号', '供应商', '物料编码', '物料名称', '质检日期', '检验数量',
                 '合格数', '不合格数', '检验结果', '质检员', '批号', '类别', '入库时间']
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button(f'🗑️ 清空「{inspect_type}」检验记录', key=f'clear_ins_db_{inspect_type}'):
            if supabase_helper.clear_inspection_records(inspect_type=inspect_type):
                st.success(f'已清空「{inspect_type}」检验记录')
                st.rerun()
    with c2:
        st.caption('⚠️ 该按钮只清空当前工序的检验记录，清空后无法恢复，历史跨窗口对账将失效，请谨慎操作。')


def _render_supplier_alias_tab():
    """🏷️ 供应商别名：维护归一化自定义映射（团队共享）"""
    st.subheader('🏷️ 供应商别名管理')
    st.caption('比对/对账时自动识别同一公司的不同写法（如「塑邦模型」=「常州塑邦模型有限公司」=「常州塑邦」）。')
    st.caption('内置规则会自动剥离省/市/区前缀与「有限公司/厂」等后缀，并对核心名做包含匹配；'
               '下方别名用于规则覆盖不到的特殊写法，所有登录用户共享。')

    if not supabase_helper.ensure_supplier_aliases_table():
        st.warning('⚠️ 供应商别名表 `supplier_aliases` 尚未创建，请先在 Supabase SQL Editor 中执行：')
        st.code(supabase_helper.get_create_inspection_records_table_sql(), language='sql')
        return

    with st.form('alias_form', clear_on_submit=True):
        c1, c2, c3 = st.columns([3, 3, 1])
        with c1:
            alias = st.text_input('别名（如「塑邦」或「常州塑邦」）', key='alias_input')
        with c2:
            canonical = st.text_input('规范名（如「塑邦模型」，即归一化后的核心名）', key='canonical_input')
        with c3:
            submitted = st.form_submit_button('➕ 添加', use_container_width=True)
        if submitted:
            if alias.strip() and canonical.strip():
                if supabase_helper.add_supplier_alias(alias, canonical):
                    supplier_normalize.reload_aliases()
                    st.success(f'已添加别名「{alias.strip()}」→「{canonical.strip()}」')
                    st.rerun()
            else:
                st.warning('请同时填写别名与规范名')

    aliases = supabase_helper.list_supplier_aliases()
    if aliases:
        st.divider()
        st.markdown(f'**现有别名（{len(aliases)} 条）**')
        for a in aliases:
            c1, c2, c3 = st.columns([3, 3, 1])
            with c1:
                st.text(str(a.get('alias', '')))
            with c2:
                st.text(str(a.get('canonical', '')))
            with c3:
                if st.button('🗑️', key=f"del_alias_{a.get('alias', '')}", help='删除该别名'):
                    if supabase_helper.delete_supplier_alias(a.get('alias', '')):
                        supplier_normalize.reload_aliases()
                        st.rerun()
    else:
        st.info('暂无自定义别名，当前全部依赖内置归一化规则。')


def _render_report_recipients_tab(inspect_type):
    """📮 未检验清单邮件收件人：团队共享，页面增删，按工序分别发送"""
    st.subheader(f'📮「{inspect_type}」未检验清单邮件收件人')
    st.caption(f'工作日凌晨自动发送「{inspect_type}」未检验清单的收件人。可在页面直接增删，无需修改代码或 GitHub 配置。所有登录用户共享。')

    if not supabase_helper.ensure_report_recipients_table():
        st.warning('⚠️ 收件人表 `report_recipients` 尚未创建，请先在 Supabase SQL Editor 中执行：')
        st.code(supabase_helper.get_create_inspection_records_table_sql(), language='sql')
        return
    if not supabase_helper.ensure_report_recipients_columns():
        st.warning('⚠️ 收件人表缺少「抄送人 / 工序」支持字段，请先在 Supabase SQL Editor 中执行迁移 SQL：')
        st.code(supabase_helper.get_report_recipients_migration_sql(), language='sql')

    import re as _re
    st.caption(f'🧪 当前工序：**{inspect_type}**')
    with st.form(f'recipient_form_{inspect_type}', clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
        with c1:
            email = st.text_input('邮箱地址（多个请逐个添加）', key=f'recipient_input_{inspect_type}',
                                  placeholder='例如：someone@qq.com')
        with c2:
            rtype = st.radio('类型', ['收件人', '抄送人'], horizontal=True, key=f'recipient_type_input_{inspect_type}')
        with c3:
            insp_type = st.selectbox('适用工序', ['全部', inspect_type], index=1,
                                     key=f'recipient_inspect_type_input_{inspect_type}',
                                     help='「全部」= 所有工序的未检验清单都发给他；当前工序则只发本工序')
        with c4:
            submitted = st.form_submit_button('➕ 添加', use_container_width=True)
        if submitted:
            e = email.strip()
            if not e:
                st.warning('请输入邮箱地址')
            elif not _re.match(r'^[\w.+-]+@[\w-]+(\.[\w-]+)+$', e):
                st.warning('邮箱格式不正确，请检查后重试')
            else:
                t = 'cc' if rtype == '抄送人' else 'to'
                if supabase_helper.add_report_recipient(e, t, insp_type):
                    st.success(f'已添加{rtype}「{e}」（{insp_type}）')
                    st.rerun()

    recipients = supabase_helper.list_report_recipients()
    recipients = [r for r in recipients if str(r.get('inspect_type', '全部')) in ('全部', inspect_type)]
    if recipients:
        st.divider()
        to_n = sum(1 for r in recipients if str(r.get('recipient_type', 'to')) != 'cc')
        cc_n = len(recipients) - to_n
        st.markdown(f'**当前配置（收件人 {to_n} 个 · 抄送人 {cc_n} 个）**')
        for r in recipients:
            is_cc = str(r.get('recipient_type', 'to')) == 'cc'
            insp = str(r.get('inspect_type', '全部'))
            c1, c2, c3, c4 = st.columns([1, 2, 4, 1])
            with c1:
                st.markdown('📧 抄送' if is_cc else '📨 收件')
            with c2:
                st.markdown(f'`{insp}`')
            with c3:
                st.text(str(r.get('email', '')))
            with c4:
                if st.button('🗑️', key=f"del_recipient_{inspect_type}_{r.get('id')}", help='删除该邮箱'):
                    if supabase_helper.delete_report_recipient(str(r.get('id'))):
                        st.rerun()
        st.caption('说明：「全部」收件人会收到所有工序邮件，在当前工序页面也可看到。'
                   '至少保留 1 个收件人，否则对应工序的邮件不会发送。')
    else:
        st.info('暂无收件人，添加后每天凌晨的未检验清单才会发送。')


def page_inspection_match():
    st.header('🔍 质量管理')
    st.caption('按工序管理送检、检验对比、记录库及相关配置。当前只启用已配置的工序，以后新增工序会自动扩展。')

    if not supabase_helper.ensure_inspect_type_columns():
        col_err = supabase_helper.get_last_db_check_error()
        st.warning('⚠️ 检测到数据库缺少「检验类型」字段（老库升级）。请先在 Supabase SQL Editor 执行下方迁移 SQL，'
                   '否则上传 / 比对 / 自动邮件会报错：')
        if col_err:
            st.error(f'❌ 检测失败原因：`{col_err}`')
        st.code(supabase_helper.get_inspect_type_migration_sql(), language='sql')
        st.divider()

    # 本页面所有 session_state key 统一初始化，避免分支遗漏
    st.session_state.setdefault('inspection_match_result', None)
    st.session_state.setdefault('inspection_match_dt', 0.0)
    st.session_state.setdefault('_sub_compare', None)
    st.session_state.setdefault('_sub_df_cache', None)
    st.session_state.setdefault('_sub_fid', None)
    st.session_state.setdefault('_sub_parse_err', None)
    st.session_state.setdefault('_sub_df', None)
    st.session_state.setdefault('_sub_preview', None)
    st.session_state.setdefault('_sub_import_result', None)
    st.session_state.setdefault('_sub_imported', False)
    st.session_state.setdefault('_ins_fid', None)
    st.session_state.setdefault('_ins_parse_err', None)
    st.session_state.setdefault('_ins_df', None)
    st.session_state.setdefault('_rpc_ok', None)

    # 顶层工序导航：从配置中心读取已配置工序，以后新增工序 = 加一段配置即可
    inspect_types = list(inspection_match.INSPECT_TYPE_CONFIGS.keys())
    if not inspect_types:
        inspect_types = ['来料检']
    selected_type = st.segmented_control(
        '选择工序',
        options=inspect_types,
        default=inspect_types[0],
        key='match_inspect_type',
        label_visibility='collapsed'
    )
    if not selected_type:
        selected_type = inspect_types[0]
    st.caption(f'当前工序：**{selected_type}**')

    tab_manage, tab_compare, tab_ins_db, tab_alias, tab_recipients = st.tabs(
        ['📤 送检清单管理', '⚖️ 检验对比', '📋 检验记录库', '🏷️ 供应商别名', '📮 邮件收件人'])

    with tab_manage:
        table_ok = supabase_helper.ensure_inspection_table()
        if not table_ok:
            db_err = supabase_helper.get_last_db_check_error()
            st.warning('⚠️ 数据库表 `inspection_submissions` 检测未通过，请先在 Supabase SQL Editor 中执行：')
            if db_err:
                st.error(f'❌ 检测失败原因：`{db_err}`')
            st.code(supabase_helper.get_create_inspection_table_sql(), language='sql')
            if st.button('🔄 已执行 SQL，重新检测', key='db_recheck'):
                st.rerun()
        else:
            if st.session_state._rpc_ok is None:
                st.session_state._rpc_ok = supabase_helper.ensure_inspection_rpc()
            if st.session_state._rpc_ok:
                st.caption('⚡ 高速批量入库已启用（RPC 原子去重，单请求完成）')
            else:
                rpc_err = supabase_helper.get_last_db_check_error()
                st.warning('⚠️ 批量入库函数 `bulk_insert_inspections` 检测未通过，当前使用普通写入模式（含重复时较慢）。建议执行下方完整 SQL：')
                if rpc_err:
                    st.error(f'❌ 检测失败原因：`{rpc_err}`')
                st.code(supabase_helper.get_create_inspection_table_sql(), language='sql')
                if st.button('🔄 已执行 SQL，重新检测', key='rpc_recheck'):
                    st.session_state._rpc_ok = supabase_helper.ensure_inspection_rpc()
                    st.rerun()
            _render_submission_tab(selected_type)

    with tab_compare:
        _render_compare_tab(selected_type)

    with tab_ins_db:
        _render_inspection_records_tab(selected_type)

    with tab_alias:
        _render_supplier_alias_tab()

    with tab_recipients:
        _render_report_recipients_tab(selected_type)


# ==================== 主路由 ====================
def main():
    try:
        # ==================== 登录守卫 ====================
        # 这是解决"额外注意项①"的关键：
        #   Streamlit 没有内置路由/中间件，必须在每个页面渲染前检查登录状态。
        #   未登录时，auth.login_required() 会渲染登录页并返回 False，
        #   调用方直接 return 阻止后续页面渲染。
        if not auth.login_required():
            st.stop()

        # ---- 以下代码仅在已登录状态下执行 ----

        st.set_page_config(
            page_title='质量管理系统 QMS',
            page_icon='📊',
            layout='wide',
            initial_sidebar_state='expanded',
        )

        # 隐藏右上角工具栏 (Share / 编辑代码等) + 隐藏右下角 Made with Streamlit
        st.markdown(textwrap.dedent("""
        <style>
        [data-testid="stToolbar"] { display: none !important; }
        footer { visibility: hidden; }
        [data-testid="manage-app-button"] { display: none !important; }
        </style>
        """), unsafe_allow_html=True)

        # ===== 弹窗居中样式 + 遮罩透明 =====
        st.markdown(textwrap.dedent("""
        <style>
            [data-testid="stDialog"] {
                position: fixed !important;
                top: 50% !important;
                left: 50% !important;
                transform: translate(-50%, -50%) !important;
            }
            [data-testid="stDialog"] > div:first-child {
                max-height: 85vh !important;
                overflow-y: auto !important;
            }
            /* 遮罩层完全透明：用 :has() 命中包含 stDialog 的 overlay 父容器 */
            div:has(> [data-testid="stDialog"]) {
                background: transparent !important;
                backdrop-filter: none !important;
            }
            div:has(div:has(> [data-testid="stDialog"])) {
                background: transparent !important;
            }
        </style>
        """), unsafe_allow_html=True)

        # ==================== 侧边栏 ====================
        st.sidebar.title('📊 质量管理系统')
        st.sidebar.caption('Quality Management System v2.0')

        menu = st.sidebar.radio(
            '选择页面',
            ['🔍 检验对比',
             '📊 分析模块'],
        )
        st.sidebar.divider()
        st.sidebar.caption('支持 CSV / Excel · 支持 Supabase 云存储')

        # 用户信息栏 + 登出按钮
        auth.render_user_bar()

        _route_main(menu)
    except Exception as e:
        # 捕获所有未处理异常并在页面显示，便于用户复制反馈，避免白屏崩溃
        import traceback
        tb = traceback.format_exc()
        st.error('❌ 页面渲染发生异常，请把下方错误信息复制给开发人员：')
        st.code(tb, language='text')


def page_analysis_hub():
    """分析模块总入口：胶囊按钮切换，只渲染当前选中的模块，
    避免 Streamlit 的 tabs 一次性渲染全部子页面导致卡顿。"""
    st.header('📊 分析模块')
    st.caption('点击下方胶囊按钮切换具体分析工具')

    modules = ['📁 数据导入', '📋 批量分析报告', '📈 SPC 控制图', '🎯 过程能力分析',
               '📊 质量图形工具', '🔬 测量系统分析 MSA', '🔢 统计推断', '🧪 高级分析']
    sel = st.segmented_control(
        '选择分析模块', modules, default='📁 数据导入',
        key='analysis_hub_sel', label_visibility='collapsed')
    if sel is None:
        sel = '📁 数据导入'

    if sel == '📁 数据导入':
        page_data_import()
    elif sel == '📋 批量分析报告':
        page_batch_analysis()
    elif sel == '📈 SPC 控制图':
        page_spc()
    elif sel == '🎯 过程能力分析':
        page_capability()
    elif sel == '📊 质量图形工具':
        page_quality_tools()
    elif sel == '🔬 测量系统分析 MSA':
        page_msa()
    elif sel == '🔢 统计推断':
        page_stats()
    elif sel == '🧪 高级分析':
        page_advanced()


def _route_main(menu):
    if menu == '🔍 检验对比':
        page_inspection_match()
    elif menu == '📊 分析模块':
        page_analysis_hub()


if __name__ == '__main__':
    main()
