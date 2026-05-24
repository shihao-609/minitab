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
from dotenv import load_dotenv

from modules import spc_charts, capability, pareto_histogram, gage_rr, supabase_helper
from modules import spc_advanced, msa_advanced, stats_tools, quality_tools, advanced_analysis
from modules import auth

load_dotenv()

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
st.markdown("""
<style>
[data-testid="stToolbar"] { display: none !important; }
footer { visibility: hidden; }
[data-testid="manage-app-button"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ===== 弹窗居中样式 + 遮罩透明 =====
st.markdown("""
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
""", unsafe_allow_html=True)

# ==================== 侧边栏 ====================
st.sidebar.title('📊 质量管理系统')
st.sidebar.caption('Quality Management System v2.0')

menu = st.sidebar.radio(
    '选择分析模块',
    ['📁 数据导入',
     '📈 SPC 控制图',
     '🎯 过程能力分析',
     '📊 质量图形工具',
     '🔬 测量系统分析 MSA',
     '🔢 统计推断',
     '🧪 高级分析'],
)
st.sidebar.divider()
st.sidebar.caption('支持 CSV / Excel · 支持 Supabase 云存储')

# 用户信息栏 + 登出按钮
auth.render_user_bar()


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
    tab1, tab2, tab3, tab4 = st.tabs(['上传文件', '示例数据', '手动输入', '☁️ Supabase'])

    with tab1:
        uploaded_file = st.file_uploader('选择 CSV 或 Excel 文件', type=['csv', 'xlsx', 'xls'])
        if uploaded_file:
            try:
                df = parse_uploaded_file(uploaded_file)
                if df is not None:
                    set_new_data(df)
                    st.dataframe(df.head(10), use_container_width=True)
            except Exception as e:
                st.error(f'加载失败: {e}')

    with tab2:
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
                from numpy.random import weibull
                times = 1000 * weibull(2, 50)
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

    with tab3:
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

    with tab4:
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

        if ct in ['X-bar R (均值-极差)', 'X-bar S (均值-标准差)']:
            c1, c2 = st.columns(2)
            with c1:
                dc = st.selectbox('数据列', numeric_cols, key='xbar_col')
            with c2:
                ss = st.number_input('子组大小', 2, 10, 5)
            data = df[dc].dropna().values
            if len(data) < ss * 2:
                st.error(f'数据不足，需至少 {ss*2} 个点')
            else:
                r = spc_charts.xbar_r_chart(data, ss) if ct == 'X-bar R (均值-极差)' else spc_charts.xbar_s_chart(data, ss)
                st.plotly_chart(r['chart'], use_container_width=True)
                with st.expander('📊 参数'):
                    for k, v in r['stats'].items():
                        st.metric(k, f'{v:.4f}')

        elif ct == 'I-MR (单值-移动极差)':
            dc = st.selectbox('数据列', numeric_cols, key='imr_col')
            data = df[dc].dropna().values
            if len(data) < 2:
                st.error('需至少2个数据点')
            else:
                r = spc_charts.imr_chart(data)
                st.plotly_chart(r['chart'], use_container_width=True)

        elif ct == 'P 图 (不合格品率)':
            c1, c2 = st.columns(2)
            with c1:
                dcol = st.selectbox('不合格品数列', numeric_cols, key='p_col')
            with c2:
                scol = st.selectbox('样本量列', numeric_cols, key='p_size')
            d, s = df[dcol].dropna().values, df[scol].dropna().values
            ml = min(len(d), len(s))
            if ml >= 2:
                r = spc_charts.p_chart(d[:ml], s[:ml])
                st.plotly_chart(r['chart'], use_container_width=True)

        elif ct == 'NP 图 (不合格品数)':
            c1, c2 = st.columns(2)
            with c1:
                dcol = st.selectbox('不合格品数列', numeric_cols, key='np_col')
            with c2:
                sz = st.number_input('固定样本量', 1, 10000, 100)
            d = df[dcol].dropna().values
            if len(d) >= 2:
                r = spc_charts.np_chart(d, sz)
                st.plotly_chart(r['chart'], use_container_width=True)

        elif ct in ['C 图 (缺陷数)', 'U 图 (单位缺陷数)']:
            dcol = st.selectbox('缺陷数列', numeric_cols, key='cu_col')
            d = df[dcol].dropna().values
            if ct == 'C 图 (缺陷数)':
                r = spc_charts.c_chart(d)
            else:
                scol = st.selectbox('单位数列', numeric_cols, key='u_size')
                s = df[scol].dropna().values
                ml = min(len(d), len(s))
                r = spc_charts.u_chart(d[:ml], s[:ml])
            st.plotly_chart(r['chart'], use_container_width=True)

        st.info('🔴 判异准则：超 UCL/LCL 为异常；连续7点同侧、连续趋势也视为异常')

    # --- Tab2: EWMA ---
    with tab2:
        dc = st.selectbox('数据列', numeric_cols, key='ewma_col')
        c1, c2 = st.columns(2)
        with c1:
            lam = st.slider('平滑系数 λ', 0.05, 1.0, 0.2, 0.05)
        with c2:
            L = st.slider('控制限倍数 L', 2.0, 4.0, 2.7, 0.1)
        data = df[dc].dropna().values
        if len(data) >= 2:
            r = spc_advanced.ewma_chart(data, lam, L)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.plotly_chart(r['chart'], use_container_width=True)
                with st.expander('📊 参数'):
                    for k, v in r['stats'].items():
                        st.metric(k, v)
        else:
            st.error('至少需要 2 个数据点')

    # --- Tab3: CUSUM ---
    with tab3:
        dc = st.selectbox('数据列', numeric_cols, key='cusum_col')
        c1, c2 = st.columns(2)
        with c1:
            k_val = st.slider('参考值 k (σ倍数)', 0.1, 2.0, 0.5, 0.1)
        with c2:
            h_val = st.slider('决策区间 h (σ倍数)', 2.0, 8.0, 4.0, 0.5)
        data = df[dc].dropna().values
        if len(data) >= 2:
            r = spc_advanced.cusum_chart(data, k=k_val, h=h_val)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.plotly_chart(r['chart'], use_container_width=True)
                with st.expander('📊 参数'):
                    for k, v in r['stats'].items():
                        st.metric(k, v)
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
            with st.expander('📊 参数'):
                for k, v in r['stats'].items():
                    st.metric(k, v)

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

    tab1, tab2 = st.tabs(['Cp/Cpk/Pp/Ppk', 'Cg/Cgk 检具能力'])

    # --- Tab1: Cp/Cpk ---
    with tab1:
        dc = st.selectbox('数据列', numeric_cols, key='cpk_col')
        data = df[dc].dropna().values
        if len(data) < 2:
            st.error('至少需要 2 个数据点')
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                usl = st.text_input('规格上限 USL', placeholder='留空=不设')
            with c2:
                lsl = st.text_input('规格下限 LSL', placeholder='留空=不设')
            with c3:
                tgt = st.text_input('目标值', placeholder='留空=不设')

            usl = float(usl) if usl else None
            lsl = float(lsl) if lsl else None
            tgt = float(tgt) if tgt else None

            if usl is None and lsl is None:
                st.info('请至少输入一个规格限')
            elif usl is not None and lsl is not None and usl <= lsl:
                st.error('USL 必须大于 LSL')
            else:
                r = capability.process_capability(data, usl, lsl, tgt)
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
                    with cs[5]: st.metric('预计 PPM', f'{r["ppm_total"]:,.0f}')

                    cs2 = st.columns(4)
                    with cs2[0]: st.metric('均值', f'{r["mean"]:.4f}')
                    with cs2[1]: st.metric('整体 σ', f'{r["std_overall"]:.4f}')
                    with cs2[2]: st.metric('组内 σ', f'{r["std_within"]:.4f}')
                    with cs2[3]: st.metric('样本量', r['n'])

                    st.plotly_chart(r['chart'], use_container_width=True)

                    with st.expander('📋 评级标准'):
                        st.table(pd.DataFrame({
                            'Cpk': ['≥ 1.67', '1.33~1.67', '1.00~1.33', '0.67~1.00', '< 0.67'],
                            '评级': ['优秀', '良好', '尚可', '不足', '差'],
                            '建议': ['可放宽抽检', '维持现状', '加强控制', '需改进', '急需改进'],
                        }))

    # --- Tab2: Cg/Cgk ---
    with tab2:
        st.caption('MSA Type 1 — 检具能力指数评估')
        dc = st.selectbox('重复测量列', numeric_cols, key='cg_col')
        data = df[dc].dropna().values
        c1, c2 = st.columns(2)
        with c1:
            tol = st.text_input('公差 T = USL - LSL', value='', key='cg_tol',
                                placeholder='例: 0.1')
        with c2:
            ref_val = st.text_input('参考值 (标准值)', value='', key='cg_ref',
                                    placeholder='留空=用数据均值')

        if tol:
            tolerance = float(tol)
            ref = float(ref_val) if ref_val else None
            r = msa_advanced.cg_cgk(data, tolerance, ref)
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
        st.caption('交叉型 (Crossed) 平均值-极差法')
        c1, c2, c3 = st.columns(3)
        with c1:
            pc = st.selectbox('部件列', all_cols, key='grr_part')
        with c2:
            oc = st.selectbox('操作员列', all_cols, key='grr_op')
        with c3:
            mc = st.selectbox('测量值列', numeric_cols, key='grr_meas')

        parts = df[pc].values
        ops = df[oc].values
        meas = df[mc].values
        n_parts = len(np.unique(parts))
        n_ops = len(np.unique(ops))
        st.info(f'📋 {n_parts} 部件 × {n_ops} 操作员 × {len(meas)} 次测量')

        if n_parts < 2 or n_ops < 2:
            st.error('需至少 2 部件 2 操作员')
        else:
            r = gage_rr.gage_rr_crossed(parts, ops, meas)
            if 'error' in r:
                st.error(r['error'])
            else:
                st.subheader('📊 方差分量')
                cs = st.columns(5)
                with cs[0]: st.metric('EV 重复性 σ', r['stddev_contributions']['重复性 (EV)'])
                with cs[1]: st.metric('AV 再现性 σ', r['stddev_contributions']['再现性 (AV)'])
                with cs[2]: st.metric('GRR σ', r['stddev_contributions']['GRR'])
                with cs[3]: st.metric('PV 部件 σ', r['stddev_contributions']['部件间 (PV)'])
                with cs[4]: st.metric('ndc', r['ndc'])
                cs2 = st.columns(2)
                grr_pct = float(r['percent_contributions']['GRR占比 %GRR'].replace('%', ''))
                with cs2[0]: st.metric('%GRR', f'{grr_pct:.1f}%')
                with cs2[1]: st.metric('评级', r['evaluation'])
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

            elif test_type in ['双样本 t 检验 (独立)', '双样本 t 检验 (配对)']:
                c1, c2 = st.columns(2)
                with c1: dc1 = st.selectbox('样本 1 列', numeric_cols, key='t2a')
                with c2: dc2 = st.selectbox('样本 2 列', numeric_cols, key='t2b')
                paired = '配对' in test_type
                if st.button('执行检验', use_container_width=True):
                    r = stats_tools.t_test_two_sample(df[dc1].dropna().values, df[dc2].dropna().values, paired)
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


# ==================== 主路由 ====================
def main():
    if menu == '📁 数据导入':
        page_data_import()
    elif menu == '📈 SPC 控制图':
        page_spc()
    elif menu == '🎯 过程能力分析':
        page_capability()
    elif menu == '📊 质量图形工具':
        page_quality_tools()
    elif menu == '🔬 测量系统分析 MSA':
        page_msa()
    elif menu == '🔢 统计推断':
        page_stats()
    elif menu == '🧪 高级分析':
        page_advanced()


if __name__ == '__main__':
    main()
