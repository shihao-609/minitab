"""
质量管理系统 (Quality Management System) v1.0
=============================================
一个类 Minitab 的质量管理 Web 应用
功能模块：
  1. SPC 控制图 (X-bar R, X-bar S, I-MR, P, NP, C, U)
  2. 过程能力分析 (Cp, Cpk, Pp, Ppk)
  3. 帕累托图 & 直方图
  4. 量具 R&R 分析
  5. 正态性检验 & 散点图
"""

import streamlit as st
import pandas as pd
import numpy as np
import io
import os
from dotenv import load_dotenv
from modules import spc_charts, capability, pareto_histogram, gage_rr, supabase_helper

# 加载 .env 环境变量
load_dotenv()

st.set_page_config(
    page_title='质量管理系统 QMS',
    page_icon='📊',
    layout='wide',
    initial_sidebar_state='expanded',
)

# ==================== 侧边栏导航 ====================
st.sidebar.title('📊 质量管理系统')
st.sidebar.caption('Quality Management System v1.0')

menu = st.sidebar.radio(
    '选择分析模块',
    ['📁 数据导入',
     '📈 SPC 控制图',
     '🎯 过程能力分析',
     '📊 帕累托图 & 直方图',
     '🔬 量具 R&R 分析',
     '🔢 正态性检验',
     '📉 散点图 & 回归'],
)

st.sidebar.divider()
st.sidebar.caption('支持格式: CSV, Excel (.xlsx/.xls)')

# ==================== 数据导入(全局数据管理) ====================
def load_data():
    """数据导入组件"""
    st.header('📁 数据导入')

    tab1, tab2, tab3, tab4 = st.tabs(['上传文件', '示例数据', '手动输入', '☁️ Supabase'])

    with tab1:
        uploaded_file = st.file_uploader(
            '选择 CSV 或 Excel 文件',
            type=['csv', 'xlsx', 'xls'],
            help='支持 .csv, .xlsx, .xls 格式'
        )
        if uploaded_file:
            try:
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                else:
                    df = pd.read_excel(uploaded_file)
                st.session_state.user_data = df
                st.success(f'✅ 成功加载: {df.shape[0]} 行 × {df.shape[1]} 列')
                st.dataframe(df.head(10), use_container_width=True)
            except Exception as e:
                st.error(f'加载失败: {e}')

    with tab2:
        st.write('**使用内置示例数据**')
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button('📐 正态分布样本', use_container_width=True):
                np.random.seed(42)
                data = np.random.normal(loc=10.0, scale=0.5, size=100)
                st.session_state.user_data = pd.DataFrame({'测量值': data})
                st.success('已加载 100 个正态分布样本')
        with col2:
            if st.button('📏 SPC 多子组样本', use_container_width=True):
                np.random.seed(42)
                data = []
                for i in range(25):
                    subgroup = np.random.normal(loc=10.0 + (i % 5) * 0.1, scale=0.3, size=5)
                    data.extend(subgroup)
                st.session_state.user_data = pd.DataFrame({'测量值': data})
                st.success('已加载 125 个多子组样本 (25组 × 5)')
        with col3:
            if st.button('⚙️ 含偏移样本', use_container_width=True):
                np.random.seed(42)
                data = list(np.random.normal(loc=10.0, scale=0.5, size=50))
                data.extend(np.random.normal(loc=11.5, scale=0.5, size=30))
                st.session_state.user_data = pd.DataFrame({'测量值': data})
                st.success('已加载含过程偏移的样本')

        st.write('**计量型 Gage R&R 示例**')
        if st.button('🔬 加载 Gage R&R 示例数据', use_container_width=True):
            np.random.seed(123)
            parts = []
            operators = []
            measurements = []
            true_values = [10.0, 10.2, 10.5, 10.3, 10.8,
                          11.0, 11.2, 10.9, 11.5, 11.8]
            for p_id, true_val in enumerate(true_values, 1):
                for op in [1, 2, 3]:
                    for _ in range(2):
                        val = true_val + np.random.normal(0, 0.05) + np.random.normal(0, 0.02)
                        parts.append(p_id)
                        operators.append(op)
                        measurements.append(val)
            st.session_state.user_data = pd.DataFrame({
                'Part': parts, 'Operator': operators, 'Measurement': measurements
            })
            st.success('已加载 Gage R&R 数据: 10部件 × 3操作员 × 2次试验')

    with tab3:
        st.write('**手动输入数据** (在表格中直接输入)')

        # 初始化 session_state（空字符串，支持文字输入）
        if 'manual_df' not in st.session_state:
            st.session_state.manual_df = pd.DataFrame({'测量值': [''] * 10})

        # ===== 列名编辑区 =====
        cols = list(st.session_state.manual_df.columns)
        n_cols = len(cols)

        with st.expander('📝 编辑列名 & 管理列', expanded=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                new_col_names = []
                name_cols = st.columns(n_cols)
                for i, col_name in enumerate(cols):
                    with name_cols[i]:
                        new_name = st.text_input(
                            f'列{i+1}',
                            value=col_name,
                            key=f'col_name_{i}_{col_name}',
                            label_visibility='collapsed',
                            placeholder=f'列{i+1}'
                        )
                        new_col_names.append(new_name.strip() if new_name.strip() else f'列{i+1}')
            with c2:
                st.write('')  # 对齐用
                add_col_btn = st.button('➕ 添加列', use_container_width=True)
                if n_cols > 1:
                    del_col_btn = st.button('➖ 删除最后一列', use_container_width=True)
                else:
                    del_col_btn = False

        # 处理列名变更
        if new_col_names != cols:
            df = st.session_state.manual_df.copy()
            df.columns = new_col_names
            st.session_state.manual_df = df
            st.rerun()

        # 添加列
        if add_col_btn:
            new_col = f'列{n_cols + 1}'
            st.session_state.manual_df[new_col] = [''] * len(st.session_state.manual_df)
            st.rerun()

        # 删除列
        if del_col_btn and n_cols > 1:
            st.session_state.manual_df = st.session_state.manual_df.iloc[:, :-1]
            st.rerun()

        # ===== 数据编辑表格 =====
        st.caption('💡 单元格支持输入数字或文字，Tab 跳格；最后一行下方继续输入可自动扩展行')
        edited_df = st.data_editor(
            st.session_state.manual_df,
            use_container_width=True,
            height=350,
            num_rows='dynamic',
            key='manual_data_editor',
            hide_index=True
        )

        # ===== 操作按钮 =====
        btn_col1, btn_col2, btn_col3 = st.columns([1.5, 1, 1])
        with btn_col1:
            import_btn = st.button('✅ 导入表格数据', use_container_width=True, type='primary')
        with btn_col2:
            clear_btn = st.button('🗑️ 清空数据', use_container_width=True)
        with btn_col3:
            fill_demo_btn = st.button('📝 填充示例', use_container_width=True)

        if import_btn:
            # 过滤掉完全空白的行和列
            valid_df = edited_df.replace('', pd.NA).dropna(how='all').dropna(axis=1, how='all')
            if valid_df.empty:
                st.error('表格为空，请先输入数据')
            else:
                # 智能类型转换：尝试转数值，失败则保留文字
                for c in valid_df.columns:
                    converted = pd.to_numeric(valid_df[c], errors='coerce')
                    if converted.notna().sum() > 0:
                        valid_df[c] = converted
                st.session_state.user_data = valid_df.reset_index(drop=True)
                st.success(f'已导入 {len(valid_df)} 行 × {valid_df.shape[1]} 列数据')

        if clear_btn:
            current_cols = edited_df.columns.tolist()
            st.session_state.manual_df = pd.DataFrame(
                {c: [''] * len(edited_df) for c in current_cols}
            )
            st.rerun()

        if fill_demo_btn:
            current_cols = edited_df.columns.tolist()
            n_rows = len(edited_df) if len(edited_df) > 0 else 10
            demo_data = {
                c: np.round(np.random.normal(loc=10.0, scale=0.5, size=n_rows), 3)
                for c in current_cols
            }
            st.session_state.manual_df = pd.DataFrame(demo_data)
            st.rerun()

    with tab4:
        st.write('**☁️ Supabase 云存储** — 将数据集保存到云端或从云端加载')
        st.caption('数据持久化存储，刷新或关闭浏览器后不会丢失')

        # ---- 保存当前数据到 Supabase ----
        st.subheader('💾 保存到云端')
        col_save1, col_save2 = st.columns([3, 1])
        with col_save1:
            save_name = st.text_input('数据集名称', placeholder='例如：2024Q1_产线A_测量数据',
                                      key='supabase_save_name')
        with col_save2:
            if st.button('☁️ 保存', use_container_width=True, key='btn_save_to_supabase'):
                if 'user_data' not in st.session_state or st.session_state.user_data is None:
                    st.error('请先在「上传文件 / 示例数据 / 手动输入」中加载数据')
                elif not save_name.strip():
                    st.error('请输入数据集名称')
                else:
                    result = supabase_helper.save_dataset(
                        name=save_name.strip(),
                        df=st.session_state.user_data,
                        columns_info=list(st.session_state.user_data.columns),
                    )
                    if result:
                        st.success(f'✅ 已保存 "{save_name.strip()}" 到 Supabase ({len(st.session_state.user_data)} 行)')
                        st.rerun()

        st.divider()

        # ---- 从 Supabase 加载数据 ----
        st.subheader('📂 从云端加载')
        if st.button('🔄 刷新列表', use_container_width=False, key='btn_refresh_list'):
            st.rerun()

        datasets = supabase_helper.list_datasets()

        if not datasets:
            st.info('云端暂无已保存的数据集。加载数据后点击上方「保存」即可存储到云端。')
        else:
            st.caption(f'共 {len(datasets)} 个数据集')
            for ds in datasets:
                ds_id = ds['id']
                ds_name = ds['name']
                ds_rows = ds.get('row_count', '?')
                ds_time = ds.get('created_at', '')[:19].replace('T', ' ')

                col1, col2, col3 = st.columns([4, 2, 1])
                with col1:
                    st.write(f'**{ds_name}**')
                    st.caption(f'{ds_rows} 行 · {ds_time}')
                with col2:
                    if st.button('📥 加载', key=f'load_{ds_id}', use_container_width=True):
                        df = supabase_helper.load_dataset(ds_id)
                        if df is not None:
                            st.session_state.user_data = df
                            st.success(f'✅ 已加载 "{ds_name}" ({len(df)} 行)')
                            st.rerun()
                with col3:
                    if st.button('🗑️', key=f'del_{ds_id}', help='删除此数据集'):
                        if supabase_helper.delete_dataset(ds_id):
                            st.success(f'已删除 "{ds_name}"')
                            st.rerun()

    if 'user_data' in st.session_state and st.session_state.user_data is not None:
        with st.expander('📋 查看当前数据', expanded=False):
            st.dataframe(st.session_state.user_data, use_container_width=True)
            st.caption(f'{st.session_state.user_data.shape[0]} 行 × {st.session_state.user_data.shape[1]} 列')
            st.download_button('💾 下载当前数据 (CSV)',
                               st.session_state.user_data.to_csv(index=False).encode('utf-8-sig'),
                               'qms_data.csv', 'text/csv')


# ==================== SPC 控制图 ====================
def spc_control_charts():
    st.header('📈 SPC 控制图')
    st.caption('休哈特控制图 — 支持计量型和计数型控制图')

    if 'user_data' not in st.session_state or st.session_state.user_data is None:
        st.warning('⚠️ 请先在「数据导入」中加载数据')
        return

    df = st.session_state.user_data
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    if not numeric_cols:
        st.error('数据中没有数值列')
        return

    chart_type = st.selectbox(
        '选择控制图类型',
        ['X-bar R (均值-极差图)',
         'X-bar S (均值-标准差图)',
         'I-MR (单值-移动极差图)',
         'P 图 (不合格品率)',
         'NP 图 (不合格品数)',
         'C 图 (缺陷数)',
         'U 图 (单位缺陷数)']
    )

    col1, col2 = st.columns([1, 1])

    if chart_type in ['X-bar R (均值-极差图)', 'X-bar S (均值-标准差图)']:
        with col1:
            data_col = st.selectbox('选择数据列', numeric_cols, key='spc_data_col')
        with col2:
            subgroup_size = st.number_input('子组大小', min_value=2, max_value=10, value=5)

        data = df[data_col].dropna().values
        if len(data) < subgroup_size * 2:
            st.error(f'数据量不足，需要至少 {subgroup_size * 2} 个数据点')
            return

        if chart_type == 'X-bar R (均值-极差图)':
            result = spc_charts.xbar_r_chart(data, subgroup_size)
        else:
            result = spc_charts.xbar_s_chart(data, subgroup_size)

        st.plotly_chart(result['chart'], use_container_width=True)
        with st.expander('📊 统计参数'):
            for k, v in result['stats'].items():
                st.metric(k, f'{v:.4f}')

    elif chart_type == 'I-MR (单值-移动极差图)':
        data_col = st.selectbox('选择数据列', numeric_cols, key='imr_col')
        data = df[data_col].dropna().values

        if len(data) < 2:
            st.error('需要至少2个数据点')
            return

        result = spc_charts.imr_chart(data)
        st.plotly_chart(result['chart'], use_container_width=True)
        with st.expander('📊 统计参数'):
            for k, v in result['stats'].items():
                st.metric(k, f'{v:.4f}')

    elif chart_type == 'P 图 (不合格品率)':
        col1, col2 = st.columns(2)
        with col1:
            defect_col = st.selectbox('选择不合格品数列', numeric_cols, key='p_defect')
        with col2:
            size_col = st.selectbox('选择样本量列', numeric_cols, key='p_size')

        defectives = df[defect_col].dropna().values
        sample_sizes = df[size_col].dropna().values
        min_len = min(len(defectives), len(sample_sizes))

        if min_len < 2:
            st.error('数据不足')
            return

        result = spc_charts.p_chart(defectives[:min_len], sample_sizes[:min_len])
        st.plotly_chart(result['chart'], use_container_width=True)

    elif chart_type == 'NP 图 (不合格品数)':
        col1, col2 = st.columns(2)
        with col1:
            defect_col = st.selectbox('选择不合格品数列', numeric_cols, key='np_col')
        with col2:
            sample_size = st.number_input('固定样本量', min_value=1, value=100)

        defectives = df[defect_col].dropna().values
        result = spc_charts.np_chart(defectives, sample_size)
        st.plotly_chart(result['chart'], use_container_width=True)

    elif chart_type in ['C 图 (缺陷数)', 'U 图 (单位缺陷数)']:
        data_col = st.selectbox('选择缺陷数列', numeric_cols, key='cu_col')
        defects = df[data_col].dropna().values

        if chart_type == 'C 图 (缺陷数)':
            result = spc_charts.c_chart(defects)
        else:
            size_col = st.selectbox('选择单位数列', numeric_cols, key='u_size')
            sizes = df[size_col].dropna().values
            min_len = min(len(defects), len(sizes))
            result = spc_charts.u_chart(defects[:min_len], sizes[:min_len])

        st.plotly_chart(result['chart'], use_container_width=True)

    st.info('🔴 **判异准则**：超UCL/LCL即为异常；连续7点同侧、连续7点趋势上升/下降也视为异常')


# ==================== 过程能力分析 ====================
def process_capability_analysis():
    st.header('🎯 过程能力分析')
    st.caption('计算 Cp, Cpk, Pp, Ppk 并评估过程能力水平')

    if 'user_data' not in st.session_state or st.session_state.user_data is None:
        st.warning('⚠️ 请先在「数据导入」中加载数据')
        return

    df = st.session_state.user_data
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    if not numeric_cols:
        st.error('数据中没有数值列')
        return

    data_col = st.selectbox('选择数据列', numeric_cols, key='cpk_col')
    data = df[data_col].dropna().values

    if len(data) < 2:
        st.error('需要至少2个数据点')
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        usl_input = st.text_input('规格上限 (USL)', value='', placeholder='留空表示不设上限')
    with col2:
        lsl_input = st.text_input('规格下限 (LSL)', value='', placeholder='留空表示不设下限')
    with col3:
        target_input = st.text_input('目标值 (Target)', value='', placeholder='留空表示不设目标')

    usl = float(usl_input) if usl_input else None
    lsl = float(lsl_input) if lsl_input else None
    target = float(target_input) if target_input else None

    if usl is None and lsl is None:
        st.info('请至少输入一个规格限')
        return

    if usl is not None and lsl is not None and usl <= lsl:
        st.error('USL 必须大于 LSL')
        return

    result = capability.process_capability(data, usl, lsl, target)

    if 'error' in result:
        st.error(result['error'])
        return

    # 能力指标卡片
    st.subheader('📊 能力指标')
    cols = st.columns(6)
    with cols[0]:
        vals = [result.get('Cp'), result.get('Pp')]
        cp_val = result.get('Cp')
        st.metric('Cp (短期)', f'{cp_val:.2f}' if cp_val is not None else 'N/A',
                  delta=None, delta_color='off')
    with cols[1]:
        st.metric('Cpk (短期)', f'{result["Cpk"]:.2f}' if result['Cpk'] is not None else 'N/A')
    with cols[2]:
        st.metric('Pp (长期)', f'{result["Pp"]:.2f}' if result['Pp'] is not None else 'N/A')
    with cols[3]:
        st.metric('Ppk (长期)', f'{result["Ppk"]:.2f}' if result['Ppk'] is not None else 'N/A')
    with cols[4]:
        st.metric('Cpk评级', result.get('cpk_level', 'N/A'))
    with cols[5]:
        st.metric('预计PPM', f'{result["ppm_total"]:,.0f}')

    cols2 = st.columns(4)
    with cols2[0]:
        st.metric('均值', f'{result["mean"]:.4f}')
    with cols2[1]:
        st.metric('整体 σ', f'{result["std_overall"]:.4f}')
    with cols2[2]:
        st.metric('组内 σ', f'{result["std_within"]:.4f}')
    with cols2[3]:
        st.metric('样本量', result['n'])

    # 能力图表
    st.plotly_chart(result['chart'], use_container_width=True)

    # 能力评级说明
    with st.expander('📋 能力评级标准'):
        rating_df = pd.DataFrame({
            'Cpk 范围': ['≥ 1.67', '1.33 ~ 1.67', '1.00 ~ 1.33', '0.67 ~ 1.00', '< 0.67'],
            '评级': ['优秀', '良好', '尚可', '不足', '差'],
            '建议': ['可适当放宽抽检', '维持当前控制', '加强过程控制', '需进行过程改进', '急需根本性改进'],
        })
        st.table(rating_df)


# ==================== 帕累托图 & 直方图 ====================
def pareto_and_histogram():
    st.header('📊 帕累托图 & 直方图')

    if 'user_data' not in st.session_state or st.session_state.user_data is None:
        st.warning('⚠️ 请先在「数据导入」中加载数据')
        return

    df = st.session_state.user_data
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    text_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()

    analysis_type = st.radio('选择图表类型',
                             ['帕累托图 (Pareto)', '直方图 (Histogram)', '箱线图 (Box Plot)'],
                             horizontal=True)

    if analysis_type == '帕累托图 (Pareto)':
        col1, col2 = st.columns(2)
        with col1:
            if text_cols:
                category_col = st.selectbox('类别列', text_cols, key='pareto_cat')
            else:
                category_col = st.selectbox('类别列 (数值)', numeric_cols, key='pareto_cat_num')
        with col2:
            count_col = st.selectbox('频数列', numeric_cols, key='pareto_cnt')

        categories = df[category_col].astype(str).tolist()
        counts = df[count_col].values

        result = pareto_histogram.pareto_chart(categories, counts)

        st.plotly_chart(result['chart'], use_container_width=True)
        col1, col2 = st.columns(2)
        with col1:
            st.metric('总计', result['total'])
        with col2:
            st.dataframe(result['data'], use_container_width=True)

    elif analysis_type == '直方图 (Histogram)':
        data_col = st.selectbox('选择数据列', numeric_cols, key='hist_col')
        data = df[data_col].dropna().values

        result = pareto_histogram.histogram_with_stats(data)

        if 'error' in result:
            st.error(result['error'])
            return

        st.plotly_chart(result['chart'], use_container_width=True)

        stats = result['stats']
        cols = st.columns(len(stats))
        for i, (k, v) in enumerate(stats.items()):
            cols[i].metric(k, v)

    elif analysis_type == '箱线图 (Box Plot)':
        data_col = st.selectbox('选择数据列', numeric_cols, key='box_col')

        group_col = st.selectbox('分组列 (可选)', ['无分组'] + text_cols + numeric_cols,
                                 key='box_group')

        if group_col == '无分组':
            data = df[data_col].dropna().values
            result = pareto_histogram.box_plot(data)
        else:
            groups = df.groupby(group_col)
            data_groups = [g[data_col].dropna().values for _, g in groups]
            result = pareto_histogram.box_plot(data_groups,
                                               group_labels=[str(n) for n in df[group_col].unique()])

        st.plotly_chart(result['chart'], use_container_width=True)


# ==================== 量具 R&R 分析 ====================
def gage_rr_analysis():
    st.header('🔬 量具 R&R 分析 (Gage R&R)')
    st.caption('交叉型 (Crossed) 量具重复性和再现性 — 平均值-极差法')

    if 'user_data' not in st.session_state or st.session_state.user_data is None:
        st.warning('⚠️ 请先在「数据导入」中加载数据，或使用示例数据')
        if st.button('📥 加载内置示例数据'):
            np.random.seed(123)
            parts, operators, measurements = [], [], []
            true_values = [10.0, 10.2, 10.5, 10.3, 10.8, 11.0, 11.2, 10.9, 11.5, 11.8]
            for p_id, true_val in enumerate(true_values, 1):
                for op in [1, 2, 3]:
                    for _ in range(2):
                        val = true_val + np.random.normal(0, 0.05) + np.random.normal(0, 0.02)
                        parts.append(p_id)
                        operators.append(op)
                        measurements.append(val)
            st.session_state.user_data = pd.DataFrame({
                'Part': parts, 'Operator': operators, 'Measurement': measurements
            })
            st.rerun()
        return

    df = st.session_state.user_data

    st.write('**请映射数据列：**')
    col1, col2, col3 = st.columns(3)
    with col1:
        part_col = st.selectbox('部件编号列', df.columns.tolist(), key='rr_part')
    with col2:
        operator_col = st.selectbox('操作员列', df.columns.tolist(), key='rr_op')
    with col3:
        measure_col = st.selectbox('测量值列', df.select_dtypes(include=[np.number]).columns.tolist(), key='rr_meas')

    parts = df[part_col].values
    operators = df[operator_col].values
    measurements = df[measure_col].values

    n_parts = len(np.unique(parts))
    n_operators = len(np.unique(operators))

    st.info(f'📋 数据概要: {n_parts} 个部件, {n_operators} 名操作员, {len(measurements)} 次测量')

    if n_parts < 2 or n_operators < 2:
        st.error('需要至少2个部件和2名操作员')
        return

    result = gage_rr.gage_rr_crossed(parts, operators, measurements)

    # 方差分量卡片
    st.subheader('📊 方差分量分析')
    cols = st.columns(5)
    with cols[0]:
        st.metric('重复性 EV (σ)', result['stddev_contributions']['重复性 (EV)'])
    with cols[1]:
        st.metric('再现性 AV (σ)', result['stddev_contributions']['再现性 (AV)'])
    with cols[2]:
        st.metric('GRR (σ)', result['stddev_contributions']['GRR'])
    with cols[3]:
        st.metric('部件间 PV (σ)', result['stddev_contributions']['部件间 (PV)'])
    with cols[4]:
        ndc_val = result['ndc']
        ndc_color = 'green' if ndc_val >= 5 else ('orange' if ndc_val >= 2 else 'red')
        st.metric('区分数 ndc', ndc_val)

    cols_pct = st.columns(4)
    with cols_pct[0]:
        grr_pct = float(result['percent_contributions']['GRR占比 %GRR'].replace('%', ''))
    with cols_pct[1]:
        st.metric('%GRR', f'{grr_pct:.1f}%')
    with cols_pct[2]:
        st.metric('评级', result['evaluation'])
    with cols_pct[3]:
        ev_pct = float(result['percent_contributions']['重复性占比 %EV'].replace('%', ''))
        av_pct = float(result['percent_contributions']['再现性占比 %AV'].replace('%', ''))
        pv_pct = float(result['percent_contributions']['部件间占比 %PV'].replace('%', ''))
        if pv_pct > 80:
            st.success('✓ 测量系统能力充足')
        elif pv_pct > 50:
            st.warning('⚠ 测量系统能力临界')
        else:
            st.error('✗ 测量系统能力不足')

    st.plotly_chart(result['chart'], use_container_width=True)

    with st.expander('📋 Gage R&R 评估标准'):
        standards = pd.DataFrame({
            '%GRR范围': ['< 10%', '10% ~ 30%', '> 30%'],
            '评级': ['优秀', '临界', '不可接受'],
            '说明': [
                '测量系统能力充足，可用于过程控制',
                '可接受但可能需要改进，取决于应用重要性',
                '测量系统需要改进，查找并消除主要变异来源',
            ],
            'ndc要求': ['≥ 5', '2 ~ 4', '< 2'],
        })
        st.table(standards)


# ==================== 正态性检验 ====================
def normality_test_page():
    st.header('🔢 正态性检验')
    st.caption('检验数据是否服从正态分布 (Shapiro-Wilk, Anderson-Darling, D\'Agostino)')

    if 'user_data' not in st.session_state or st.session_state.user_data is None:
        st.warning('⚠️ 请先在「数据导入」中加载数据')
        return

    df = st.session_state.user_data
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    if not numeric_cols:
        st.error('数据中没有数值列')
        return

    data_col = st.selectbox('选择数据列', numeric_cols, key='norm_col')
    data = df[data_col].dropna().values

    if len(data) < 3:
        st.error('需要至少3个数据点')
        return

    alpha = st.slider('显著性水平 α', 0.01, 0.10, 0.05, 0.01)

    result = pareto_histogram.normality_test(data, alpha)

    if 'error' in result:
        st.error(result['error'])
        return

    # 显示检验结果
    st.subheader('检验结果')

    cols = st.columns(len(result))
    for i, (test_name, test_result) in enumerate(result.items()):
        with cols[i]:
            is_normal = test_result['normal']
            delta_color = 'normal' if is_normal else 'inverse'
            st.metric(test_name,
                      f'{"正态 ✓" if is_normal else "非正态 ✗"}',
                      delta=f'p={test_result.get("p_value", "N/A"):.4f}' if 'p_value' in test_result else '',
                      delta_color=delta_color)

    # 同时显示直方图和Q-Q图
    hist_result = pareto_histogram.histogram_with_stats(data, '数据分布与正态拟合')
    if 'chart' in hist_result:
        st.plotly_chart(hist_result['chart'], use_container_width=True)


# ==================== 散点图 & 回归 ====================
def scatter_and_regression():
    st.header('📉 散点图 & 回归分析')
    st.caption('探索两个变量之间的关系，拟合线性回归')

    if 'user_data' not in st.session_state or st.session_state.user_data is None:
        st.warning('⚠️ 请先在「数据导入」中加载数据')
        return

    df = st.session_state.user_data
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

    if len(numeric_cols) < 2:
        st.error('至少需要两个数值列')
        return

    col1, col2 = st.columns(2)
    with col1:
        x_col = st.selectbox('X 轴变量', numeric_cols, key='scatter_x')
    with col2:
        y_col = st.selectbox('Y 轴变量', numeric_cols, index=min(1, len(numeric_cols)-1), key='scatter_y')

    x = df[x_col].dropna().values
    y = df[y_col].dropna().values

    min_len = min(len(x), len(y))
    x, y = x[:min_len], y[:min_len]

    if min_len < 3:
        st.error('需要至少3对有效数据')
        return

    result = pareto_histogram.scatter_plot(x, y, x_label=x_col, y_label=y_col)

    if 'error' in result:
        st.error(result['error'])
        return

    st.plotly_chart(result['chart'], use_container_width=True)

    # 回归摘要
    cols = st.columns(5)
    with cols[0]:
        st.metric('斜率', f'{result["slope"]:.4f}')
    with cols[1]:
        st.metric('截距', f'{result["intercept"]:.4f}')
    with cols[2]:
        st.metric('R²', f'{result["r_squared"]:.4f}')
    with cols[3]:
        st.metric('相关系数 r', f'{result["r_value"]:.4f}')
    with cols[4]:
        st.metric('p 值', f'{result["p_value"]:.6f}')

    if result['p_value'] < 0.05:
        st.success('✓ 回归关系显著 (p < 0.05)')
    else:
        st.info('回归关系不显著 (p ≥ 0.05)')


# ==================== 主路由 ====================
def main():
    if menu == '📁 数据导入':
        load_data()
    elif menu == '📈 SPC 控制图':
        spc_control_charts()
    elif menu == '🎯 过程能力分析':
        process_capability_analysis()
    elif menu == '📊 帕累托图 & 直方图':
        pareto_and_histogram()
    elif menu == '🔬 量具 R&R 分析':
        gage_rr_analysis()
    elif menu == '🔢 正态性检验':
        normality_test_page()
    elif menu == '📉 散点图 & 回归':
        scatter_and_regression()


if __name__ == '__main__':
    main()
