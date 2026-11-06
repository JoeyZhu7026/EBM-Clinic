"""
EBM-Clinic Demo3 - Streamlit 版本
更易调试的单页应用
"""

import datetime
import io
import json
import zipfile
import streamlit as st
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "app"))

from a_layer.extractor import extract_from_workspace
from a_layer.domain_analyzer import analyze_domain, build_dictionary
from a_layer.validator import validate_workspace
from a_layer.exporter import export_workspace
from b_layer.variable_parser import parse_patient_variables, load_config
from c_layer.condition_matcher import match_all_rules
from d_layer.report_generator import generate_report, build_structured_report, format_constraints

WORKSPACE_DIR = PROJECT_ROOT / "workspace"
OUTPUT_DIR = PROJECT_ROOT / "output"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
CONFIG_DIR = PROJECT_ROOT / "config"

for d in [WORKSPACE_DIR, OUTPUT_DIR, RUNTIME_DIR, CONFIG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

st.set_page_config(
    page_title="EBM-Clinic 指南核查助手",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main .block-container {
        max-width: 1400px;
        padding-top: 2rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 20px;
        border-radius: 8px 8px 0 0;
    }
    .rule-card {
        background: #f8f9fa;
        padding: 15px;
        border-radius: 8px;
        margin: 10px 0;
        border-left: 4px solid #667eea;
    }
    .rule-matched {
        border-left-color: #4caf50;
        background: #f1f8e9;
    }
    .rule-undetermined {
        border-left-color: #ff9800;
        background: #fff8e1;
    }
    .rule-not-matched {
        border-left-color: #9e9e9e;
        background: #f5f5f5;
    }
    .rule-not-applicable {
        border-left-color: #2196f3;
        background: #e3f2fd;
    }
    .condition-item {
        padding: 5px 10px;
        margin: 3px 0;
        border-radius: 4px;
        font-size: 14px;
    }
    .condition-true {
        background: #c8e6c9;
    }
    .condition-false {
        background: #ffcdd2;
    }
    .condition-unknown {
        background: #fff9c4;
    }
</style>
""", unsafe_allow_html=True)


def load_json_file(path: Path, default=None):
    if default is None:
        default = []
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            st.error(f"加载 {path.name} 失败: {e}")
            return default
    return default


def save_json_file(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_workspace_status():
    return {
        "source_exists": (WORKSPACE_DIR / "source.txt").exists(),
        "rules_exists": (WORKSPACE_DIR / "rules.json").exists(),
        "dictionary_exists": (WORKSPACE_DIR / "variable_dictionary.json").exists(),
        "suggestions_exists": (WORKSPACE_DIR / "suggestions.json").exists(),
        "validation_exists": (WORKSPACE_DIR / "validation_report.json").exists(),
        "output_rules_exists": (OUTPUT_DIR / "rules.json").exists(),
        "output_dict_exists": (OUTPUT_DIR / "variable_dictionary.json").exists(),
    }


def render_rule_conditions_tree(conditions, indent=0):
    """递归渲染规则条件树"""
    prefix = "&nbsp;&nbsp;" * indent
    if "$and" in conditions:
        st.markdown(f"{prefix}📦 **全部满足 (AND):**")
        for sub in conditions["$and"]:
            render_rule_conditions_tree(sub, indent + 1)
    elif "$or" in conditions:
        st.markdown(f"{prefix}📦 **任一满足 (OR):**")
        for sub in conditions["$or"]:
            render_rule_conditions_tree(sub, indent + 1)
    elif "field" in conditions:
        st.code(f"{conditions['field']} {conditions['operator']} {json.dumps(conditions['value'], ensure_ascii=False)}", language=None)


def render_condition_trace(trace: list[dict]):
    """渲染条件追踪，带颜色标记"""
    for t in trace:
        field = t["field"]
        op = t["operator"]
        expected = t["expected"]
        actual = t["actual"]
        var_status = t["variable_status"]
        cond_status = t["condition_status"]

        if cond_status == "true":
            icon = "✅"
            css_class = "condition-true"
        elif cond_status == "false":
            icon = "❌"
            css_class = "condition-false"
        else:
            icon = "❓"
            css_class = "condition-unknown"

        actual_str = format_constraints(actual) if isinstance(actual, list) else str(actual)

        st.markdown(
            f'<div class="condition-item {css_class}">'
            f'{icon} <strong>{field}</strong> {op} {expected} '
            f'(实际: {actual_str}, 状态: {var_status})'
            f'</div>',
            unsafe_allow_html=True
        )


VIEW_FILES = [
    "rules.json",
    "variable_dictionary.json",
    "suggestions.json",
    "validation_report.json",
]

DERIVED_FILES = [
    "variable_dictionary.json",
    "suggestions.json",
    "validation_report.json",
]


def clear_derived_outputs():
    """清空值域分析与校验的产物文件（保留 rules.json）"""
    for name in DERIVED_FILES:
        p = WORKSPACE_DIR / name
        if p.exists():
            p.unlink()


def reset_view_flags():
    """清空三个视图的显示标记"""
    st.session_state["show_rules_view"] = False
    st.session_state["show_dict_view"] = False
    st.session_state["show_validation_view"] = False
    st.session_state["active_view"] = "rules"


def build_export_zip(workspace_dir: Path) -> bytes:
    """将 workspace 中的提取与分析文件打包为 zip"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ["source.txt", *VIEW_FILES]:
            p = workspace_dir / name
            if p.exists():
                zf.write(p, arcname=name)
    return buf.getvalue()


def render_rules_view():
    if not st.session_state.get("show_rules_view", False):
        st.info('点击上方「📋 规则视图」按钮查看规则')
        return

    rules = load_json_file(WORKSPACE_DIR / "rules.json")
    if not rules:
        st.info('暂无规则，请先点击「📥 提取规则」')
        return

    groups = {}
    for rule in rules:
        rtype = rule.get("rule_type", "unknown")
        if rtype not in groups:
            groups[rtype] = []
        groups[rtype].append(rule)

    for rtype, type_rules in groups.items():
        with st.expander(f"**{rtype}** ({len(type_rules)} 条)", expanded=True):
            for rule in type_rules:
                with st.container():
                    col1, col2 = st.columns([3, 1])

                    with col1:
                        st.markdown(f"#### {rule['rule_id']}")
                        st.markdown(f"**建议:** {rule['rule_prompt']}")

                        if rule.get('uncertainty'):
                            unc = rule['uncertainty']
                            level_colors = {'high': 'red', 'moderate': 'orange', 'low': 'gray'}
                            color = level_colors.get(unc.get('level', ''), 'gray')
                            st.markdown(f":{color}[**不确定性:** {unc.get('level', 'unknown')} - {unc.get('reason', '')}]")

                    with col2:
                        if st.button("查看详情", key=f"detail_{rule['rule_id']}"):
                            st.session_state[f"show_detail_{rule['rule_id']}"] = True

                    if st.session_state.get(f"show_detail_{rule['rule_id']}", False):
                        st.markdown("**条件:**")
                        render_rule_conditions_tree(rule['rule_conditions'])
                        st.markdown(f"**溯源:** *{rule.get('source_reference', '')}*")
                        st.json(rule)
                        if st.button("收起", key=f"hide_{rule['rule_id']}"):
                            st.session_state[f"show_detail_{rule['rule_id']}"] = False
                            st.rerun()

                    st.divider()


def render_dict_view():
    if not st.session_state.get("show_dict_view", False):
        st.info('点击上方「📊 变量视图」按钮执行值域分析')
        return

    dictionary = load_json_file(WORKSPACE_DIR / "variable_dictionary.json")
    if not dictionary:
        st.info('词典为空，请先点击「📊 变量视图」执行值域分析')
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("字段总数", len(dictionary))
    with col2:
        enum_count = sum(1 for d in dictionary if d.get("type") == "enum")
        st.metric("枚举类型", enum_count)
    with col3:
        numeric_count = sum(1 for d in dictionary if d.get("type") == "numeric")
        st.metric("数值类型", numeric_count)

    st.divider()

    for entry in dictionary:
        with st.expander(f"**{entry['field']}** ({entry['type']})"):
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**值域:**")
                if entry.get("values"):
                    for v in entry["values"]:
                        st.markdown(f"- `{v['value']}` → {', '.join(v.get('rule_ids', []))}")
                else:
                    st.markdown("*无数值型值域*")

            with col2:
                st.markdown("**引用规则:**")
                st.markdown(", ".join(entry.get("rule_ids", [])))


def render_validation_view():
    if not st.session_state.get("show_validation_view", False):
        st.info('点击上方「✅ 校验报告」按钮执行校验')
        return

    report = load_json_file(WORKSPACE_DIR / "validation_report.json")
    if not report:
        st.success("✅ 无校验问题")
        return

    for r in report:
        severity = r.get("severity", "info")
        icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
        st.markdown(f"{icon} **{r.get('check_id', '')}** [{severity}] - {r.get('check_type', '')}")
        st.markdown(f"  {r.get('detail', '')}")
        if r.get("affected_rule_ids"):
            st.markdown(f"  影响规则: {', '.join(r['affected_rule_ids'])}")


PATIENT_EXAMPLES = {
    "T1DM 重度NPDR": {
        "text": "患者男性，45岁，1型糖尿病，12岁前发病，目前无明显视力下降，眼底检查示重度非增生期DR，无黄斑水肿。",
        "fields": {"sex": "男", "age": 45},
    },
    "T2DM 轻度NPDR": {
        "text": "患者，男，58岁，2型糖尿病史8年，近半年视力下降明显。空腹血糖8.2mmol/L，HbA1c 7.8%，血压145/90mmHg。眼底检查示微动脉瘤伴硬性渗出，BCVA 0.5（右眼）。无DR或轻度NPDR。",
        "fields": {"sex": "男", "age": 58},
    },
    "PDR 合并 DME": {
        "text": "女性，45岁，1型糖尿病12年，双眼视力进行性下降3个月。HbA1c 9.2%。眼底示新生血管形成，玻璃体少量积血。OCT示黄斑水肿，中心凹受累，CRT 420μm。BCVA 0.3（双眼）。PDR合并CI-DME。",
        "fields": {"sex": "女", "age": 45},
    },
    "信息不完整": {
        "text": "患者主诉视物模糊2周，有糖尿病史。",
        "fields": {},
    },
}


def load_example_callback(selected: str):
    """加载示例的 on_click 回调：在 widget 实例化前写入 session_state"""
    ex = PATIENT_EXAMPLES[selected]
    st.session_state.patient_text = ex["text"]
    st.session_state.structured_fields_text = json.dumps(ex["fields"], ensure_ascii=False, indent=2)


def main():
    st.title("🏥 EBM-Clinic 指南核查助手")
    st.caption("demo 版")

    # 初始时清空值域/校验产物与视图，各板块需按下相应按钮才填充（规则文件保留）
    if "app_initialized" not in st.session_state:
        st.session_state["app_initialized"] = True
        clear_derived_outputs()
        reset_view_flags()

    with st.sidebar:
        st.header("📊 工作台状态")
        status = get_workspace_status()

        status_items = [
            ("源文本", status["source_exists"]),
            ("规则库", status["rules_exists"]),
            ("变量词典", status["dictionary_exists"]),
            ("已导出", status["output_rules_exists"] and status["output_dict_exists"]),
        ]

        for label, exists in status_items:
            icon = "✅" if exists else "❌"
            st.markdown(f"{icon} {label}")

        st.divider()

        if status["rules_exists"]:
            rules = load_json_file(WORKSPACE_DIR / "rules.json")
            st.metric("规则数量", len(rules))

        if status["dictionary_exists"]:
            dictionary = load_json_file(WORKSPACE_DIR / "variable_dictionary.json")
            st.metric("变量字段", len(dictionary))

    tab1, tab2 = st.tabs(["🔧 规则库工作台", "🔍 患者核查"])

    # ==================== 规则库工作台 ====================
    with tab1:
        st.header("规则库构建工作台")

        col1, col2, col3, col4, col5 = st.columns(5)

        with col1:
            extract_btn = st.button("📥 提取规则", use_container_width=True, type="primary")

        with col2:
            rules_view_btn = st.button("📋 规则视图", use_container_width=True)

        with col3:
            dict_view_btn = st.button("📊 变量视图", use_container_width=True)

        with col4:
            validation_view_btn = st.button("✅ 校验报告", use_container_width=True)

        with col5:
            export_btn = st.button("📤 导出结果", use_container_width=True)

        st.divider()

        # 提取规则 - 清空旧的值域/校验产物与视图，显示进度
        if extract_btn:
            clear_derived_outputs()
            reset_view_flags()

            st.subheader("提取进度")

            source_text = (WORKSPACE_DIR / "source.txt").read_text(encoding="utf-8")
            total_chars = len(source_text)

            status_box = st.status("正在提取规则...", expanded=True)
            bar = status_box.progress(0.0, text="⏳ 等待 langextract 输出...")
            log_placeholder = status_box.empty()

            def on_progress(processed, total, line):
                if processed is not None and total > 0:
                    bar.progress(min(processed / total, 1.0), text=f"已处理 {processed:,} / {total:,} 字符")
                log_placeholder.code(line or "...", language=None)

            try:
                output_path = extract_from_workspace(WORKSPACE_DIR, progress_callback=on_progress)
                rules = load_json_file(output_path)

                bar.progress(1.0, text=f"✅ 提取完成，共 {len(rules)} 条规则")
                status_box.update(label="提取完成", state="complete", expanded=False)
                st.info("💡 点击「📋 规则视图」按钮查看提取结果")
            except Exception as e:
                status_box.update(label="提取失败", state="error", expanded=True)
                st.error(f"❌ 提取失败: {e}")

        # 规则视图按钮 - 填充规则视图板块
        if rules_view_btn:
            if not status["rules_exists"]:
                st.error("❌ 请先点击「📥 提取规则」")
                st.stop()
            st.session_state["show_rules_view"] = True
            st.session_state["active_view"] = "rules"

        # 变量视图按钮 - 执行值域分析并填充变量视图板块
        if dict_view_btn:
            if not status["rules_exists"]:
                st.error("❌ 请先点击「📥 提取规则」")
                st.stop()

            with st.spinner("正在执行值域分析..."):
                try:
                    dict_path, sug_path = analyze_domain(WORKSPACE_DIR)
                    dictionary = load_json_file(dict_path)
                    suggestions = load_json_file(sug_path)
                    st.success(f"✅ 值域分析完成: {len(dictionary)} 个字段, {len(suggestions)} 条建议")
                    st.session_state["show_dict_view"] = True
                    st.session_state["active_view"] = "dict"
                except Exception as e:
                    st.error(f"❌ 值域分析失败: {e}")

        # 校验报告按钮 - 执行校验并填充校验报告板块
        if validation_view_btn:
            if not status["rules_exists"]:
                st.error("❌ 请先点击「📥 提取规则」")
                st.stop()

            with st.spinner("正在执行校验..."):
                try:
                    report_path = validate_workspace(WORKSPACE_DIR)
                    report = load_json_file(report_path)
                    errors = sum(1 for r in report if r.get("severity") == "error")
                    warnings = sum(1 for r in report if r.get("severity") == "warning")
                    st.success(f"✅ 校验完成: {errors} 个错误, {warnings} 个警告")
                    st.session_state["show_validation_view"] = True
                    st.session_state["active_view"] = "validation"
                except Exception as e:
                    st.error(f"❌ 校验失败: {e}")

        # 导出结果按钮 - 同步 output/ 并打包下载
        if export_btn:
            if not (status["rules_exists"] and status["dictionary_exists"]):
                st.error("❌ 请先完成「📥 提取规则」和「📊 变量视图」")
                st.stop()

            try:
                export_workspace(WORKSPACE_DIR, OUTPUT_DIR)
                zip_bytes = build_export_zip(WORKSPACE_DIR)
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                st.session_state["export_package"] = (f"ebm_clinic_export_{timestamp}.zip", zip_bytes)
            except Exception as e:
                st.error(f"❌ 导出失败: {e}")

        if st.session_state.get("export_package"):
            fname, data = st.session_state["export_package"]
            st.download_button(
                "⬇️ 下载打包文件",
                data=data,
                file_name=fname,
                mime="application/zip",
                use_container_width=True,
            )

        # 点击提取后，当前状态视图区域由上方进度显示占据，不显示内容
        if not extract_btn:
            st.subheader("当前状态视图")

            # 左右布局：左边源文本，右边三个视图切换
            left_col, right_col = st.columns([1, 2])

            with left_col:
                st.markdown("#### 📄 源文本")
                if status["source_exists"]:
                    source_text = (WORKSPACE_DIR / "source.txt").read_text(encoding="utf-8")
                    new_source = st.text_area("指南原文", value=source_text, height=400, label_visibility="collapsed")
                    if st.button("💾 保存源文本", use_container_width=True):
                        (WORKSPACE_DIR / "source.txt").write_text(new_source, encoding="utf-8")
                        st.success("已保存")
                        st.rerun()
                else:
                    new_source = st.text_area("指南原文", height=400, placeholder="请输入指南原文...", label_visibility="collapsed")
                    if st.button("💾 保存源文本", use_container_width=True) and new_source.strip():
                        (WORKSPACE_DIR / "source.txt").write_text(new_source, encoding="utf-8")
                        st.success("已保存")
                        st.rerun()

            with right_col:
                # 三个视图切换：激活的视图排在最前
                tab_specs = [
                    ("📋 规则视图", "rules"),
                    ("📊 变量视图", "dict"),
                    ("✅ 校验报告", "validation"),
                ]
                active_view = st.session_state.get("active_view", "rules")
                order_idx = {"rules": 0, "dict": 1, "validation": 2}[active_view]
                ordered_specs = tab_specs[order_idx:] + tab_specs[:order_idx]

                view_tabs = st.tabs([name for name, _ in ordered_specs])

                for view_tab, (_, view_key) in zip(view_tabs, ordered_specs):
                    with view_tab:
                        if view_key == "rules":
                            render_rules_view()
                        elif view_key == "dict":
                            render_dict_view()
                        else:
                            render_validation_view()

    # ==================== 患者核查 ====================
    with tab2:
        st.header("患者核查流程")

        if not status["output_rules_exists"] or not status["output_dict_exists"]:
            st.error("❌ 请先在「规则库工作台」完成导出")
            st.stop()

        st.subheader("患者信息输入")

        if "patient_text" not in st.session_state:
            st.session_state.patient_text = (
                "患者男性，45岁，1型糖尿病，12岁前发病，目前无明显视力下降，"
                "眼底检查示重度非增生期DR，无黄斑水肿。"
            )
        if "structured_fields_text" not in st.session_state:
            st.session_state.structured_fields_text = '{"sex": "男", "age": 45}'

        col1, col2 = st.columns([2, 1])

        with col1:
            patient_text = st.text_area("自由文本描述", key="patient_text", height=150)

        with col2:
            st.markdown("**快捷示例**")
            example_options = list(PATIENT_EXAMPLES.keys())
            selected_example = st.selectbox("选择示例", example_options)

            st.button("加载示例", on_click=load_example_callback, args=(selected_example,))

        structured_fields_text = st.text_area(
            "结构化字段（JSON 格式，可选）",
            key="structured_fields_text",
            height=100,
        )

        analyze_btn = st.button("🔍 开始分析", type="primary", use_container_width=True)

        st.divider()

        if analyze_btn and patient_text.strip():
            structured_fields = {}
            if structured_fields_text.strip():
                try:
                    structured_fields = json.loads(structured_fields_text)
                except json.JSONDecodeError as e:
                    st.error(f"❌ 结构化字段 JSON 格式错误: {e}")
                    st.stop()

            patient_input = {
                "free_text": patient_text,
                "structured_fields": structured_fields,
            }

            rules = load_json_file(OUTPUT_DIR / "rules.json")
            dictionary = load_json_file(OUTPUT_DIR / "variable_dictionary.json")
            config = load_json_file(CONFIG_DIR / "variable_parsing_config.json", default={})

            with st.spinner("正在解析患者变量..."):
                try:
                    patient_vars = parse_patient_variables(patient_input, dictionary, config)
                    save_json_file(RUNTIME_DIR / "patient_variables.json", patient_vars)
                except Exception as e:
                    st.error(f"❌ 变量解析失败: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.stop()

            with st.spinner("正在执行规则匹配..."):
                try:
                    match_results = match_all_rules(rules, patient_vars)
                    save_json_file(RUNTIME_DIR / "match_results.json", match_results)
                except Exception as e:
                    st.error(f"❌ 规则匹配失败: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.stop()

            with st.spinner("正在生成报告..."):
                try:
                    structured_report, _ = generate_report(patient_vars, match_results, rules, RUNTIME_DIR)
                except Exception as e:
                    st.error(f"❌ 报告生成失败: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.stop()

            st.subheader("核查结果")

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                matched_count = len(structured_report.get("matched_rules", []))
                st.metric("✅ 命中", matched_count)

            with col2:
                undetermined_count = len(structured_report.get("undetermined_rules", []))
                st.metric("❓ 无法判定", undetermined_count)

            with col3:
                not_applicable_count = len(structured_report.get("not_applicable_rules", []))
                st.metric("🚫 不适用", not_applicable_count)

            with col4:
                not_matched_count = len(structured_report.get("not_matched_rules", []))
                st.metric("❌ 未命中", not_matched_count)

            st.divider()

            result_tab1, result_tab2, result_tab3 = st.tabs([
                "📋 命中规则",
                "❓ 无法判定",
                "📁 其他规则",
            ])

            with result_tab1:
                if structured_report.get("matched_rules"):
                    for rule in structured_report["matched_rules"]:
                        with st.expander(f"✅ **{rule['rule_id']}** - {rule['rule_prompt']} (priority: {rule['priority_rank']})", expanded=True):
                            st.markdown("**条件追踪:**")
                            render_condition_trace(rule.get("condition_trace", []))

                            st.markdown(f"**溯源:** *{rule['source_reference']}*")

                            if rule.get("uncertainty"):
                                unc = rule["uncertainty"]
                                st.warning(f"⚠️ 不确定性: {unc.get('level', 'unknown')} - {unc.get('reason', '')}")

                            if rule["priority_rank"] == 2:
                                st.info("💡 基于推断值，建议复核")
                else:
                    st.info("无命中规则")

            with result_tab2:
                if structured_report.get("undetermined_rules"):
                    for rule in structured_report["undetermined_rules"]:
                        with st.expander(f"❓ **{rule['rule_id']}** - {rule['rule_prompt']}", expanded=True):
                            st.markdown("**缺失变量:**")
                            for mv in rule.get("missing_variables", []):
                                st.markdown(f"- `{mv['field']}`: {mv.get('reason', '缺失')}")

                            st.markdown("**条件追踪:**")
                            render_condition_trace(rule.get("condition_trace", []))

                            st.markdown(f"**溯源:** *{rule['source_reference']}*")
                else:
                    st.info("无无法判定规则")

            with result_tab3:
                other_rules = structured_report.get("not_applicable_rules", []) + structured_report.get("not_matched_rules", [])
                if other_rules:
                    for rule in other_rules:
                        status_label = "🚫" if rule in structured_report.get("not_applicable_rules", []) else "❌"
                        with st.expander(f"{status_label} **{rule['rule_id']}** - {rule['rule_prompt']}"):
                            if "condition_summary" in rule:
                                st.markdown(f"**条件:** {rule['condition_summary']}")
                            if "reason" in rule:
                                st.markdown(f"**原因:** {rule['reason']}")

                            st.markdown("**条件追踪:**")
                            render_condition_trace(rule.get("condition_trace", []))

                            st.markdown(f"**溯源:** *{rule['source_reference']}*")
                else:
                    st.info("无其他规则")

            with st.expander("🔧 查看原始数据"):
                data_tab1, data_tab2, data_tab3 = st.tabs([
                    "patient_variables.json",
                    "match_results.json",
                    "final_report.json",
                ])

                with data_tab1:
                    st.json(patient_vars)

                with data_tab2:
                    st.json(match_results)

                with data_tab3:
                    st.json(structured_report)

        elif analyze_btn:
            st.warning("请输入患者描述")

        else:
            st.info('👈 请输入患者信息并点击「开始分析」')


if __name__ == "__main__":
    main()
