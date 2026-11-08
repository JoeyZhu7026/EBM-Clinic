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
from a_layer.domain_analyzer import analyze_domain
from a_layer.validator import validate_workspace
from a_layer.exporter import export_workspace, clear_outputs
from a_layer.rule_editor import (
    create_new_rule,
    load_tree,
    new_condition_node,
    new_logic_node,
    serialize_tree,
    validate_and_save_tree,
)
from b_layer.variable_parser import parse_patient_variables, load_config
from c_layer.condition_matcher import match_all_rules
from d_layer.report_generator import generate_report, format_constraints

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
    report = load_json_file(WORKSPACE_DIR / "validation_report.json")
    validation_ok = (WORKSPACE_DIR / "validation_report.json").exists() and not any(
        isinstance(r, dict) and r.get("severity") == "error" for r in report
    )
    return {
        "source_exists": (WORKSPACE_DIR / "source.txt").exists(),
        "rules_exists": (WORKSPACE_DIR / "rules.json").exists(),
        "dictionary_exists": (WORKSPACE_DIR / "variable_dictionary.json").exists(),
        "suggestions_exists": (WORKSPACE_DIR / "suggestions.json").exists(),
        "validation_exists": (WORKSPACE_DIR / "validation_report.json").exists(),
        "domain_errors_exists": (WORKSPACE_DIR / "domain_errors.json").exists(),
        "has_domain_errors": bool(load_json_file(WORKSPACE_DIR / "domain_errors.json")),
        "validation_ok": validation_ok,
        "output_rules_exists": (OUTPUT_DIR / "rules.json").exists(),
        "output_dict_exists": (OUTPUT_DIR / "variable_dictionary.json").exists(),
    }


def has_domain_errors() -> bool:
    return bool(load_json_file(WORKSPACE_DIR / "domain_errors.json"))


def set_flash(kind: str, msg: str):
    st.session_state["editor_flash"] = {"kind": kind, "msg": msg}


def show_flash():
    flash = st.session_state.pop("editor_flash", None)
    if not flash:
        return
    if flash["kind"] == "success":
        st.success(flash["msg"])
    elif flash["kind"] == "error":
        st.error(flash["msg"])
    else:
        st.info(flash["msg"])


DERIVED_FILES = [
    "variable_dictionary.json",
    "suggestions.json",
    "validation_report.json",
    "domain_errors.json",
]


def clear_derived_outputs():
    """清空值域分析与校验的产物文件（保留 rules.json）"""
    for name in DERIVED_FILES:
        p = WORKSPACE_DIR / name
        if p.exists():
            p.unlink()


def reset_view_flags():
    """重置视图显示标记"""
    st.session_state["show_dict_view"] = False
    st.session_state["show_validation_view"] = False
    st.session_state["active_view"] = "rules"


def build_export_zip(workspace_dir: Path) -> bytes:
    """将 workspace 中的提取与分析文件打包为 zip"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ["source.txt", *["rules.json", "variable_dictionary.json", "suggestions.json", "validation_report.json"]]:
            p = workspace_dir / name
            if p.exists():
                zf.write(p, arcname=name)
    return buf.getvalue()


# ==================== 规则编辑器 ====================

OPERATORS = ["=", "!=", "<", "<=", ">", ">="]


def operator_options(current: str) -> list[str]:
    options = list(OPERATORS)
    if current and current not in options:
        options.insert(0, current)
    return options


def render_condition_node(node: dict, parent, index: int, depth: int):
    """递归渲染条件节点：逻辑框 / 条件条"""
    if depth > 0:
        _, body = st.columns([0.12 * min(depth, 5), 1])
        target = body
    else:
        target = st.container()

    with target:
        if "logic" in node:
            render_logic_box(node, parent, index, depth)
        else:
            render_condition_bar(node, parent, index)


def render_logic_box(node: dict, parent, index: int, depth: int):
    with st.container(border=True):
        h1, h2 = st.columns([1, 3], vertical_alignment="center")
        with h1:
            st.markdown("**逻辑框**")
        with h2:
            logic_options = ["and", "or"]
            current_logic = node.get("logic", "and")
            if current_logic not in logic_options:
                logic_options = [current_logic] + logic_options
            node["logic"] = st.selectbox(
                "逻辑关系",
                logic_options,
                index=logic_options.index(current_logic),
                key=f"n_{node['uid']}_logic",
                label_visibility="collapsed",
            )

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("＋ 新建子逻辑框", key=f"n_{node['uid']}_addbox", use_container_width=True):
                node["children"].append(new_logic_node())
                set_flash("info", "已新建子逻辑框")
                st.rerun()
        with b2:
            if st.button("＋ 新建子条件条", key=f"n_{node['uid']}_addcond", use_container_width=True):
                node["children"].append(new_condition_node())
                set_flash("info", "已新建子条件条")
                st.rerun()
        with b3:
            if parent is not None:
                if st.button("🗑 删除本逻辑框", key=f"n_{node['uid']}_del", use_container_width=True):
                    parent["children"].pop(index)
                    set_flash("info", "已删除逻辑框")
                    st.rerun()

        for i, child in enumerate(node["children"]):
            render_condition_node(child, node, i, depth + 1)


def render_condition_bar(node: dict, parent, index: int):
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 1, 2], vertical_alignment="center")
        with c1:
            node["field"] = st.text_input(
                "变量名",
                value=node.get("field", ""),
                key=f"n_{node['uid']}_field",
                label_visibility="collapsed",
            )
        with c2:
            ops = operator_options(node.get("operator", "="))
            node["operator"] = st.selectbox(
                "运算符",
                ops,
                index=ops.index(node.get("operator", "=")) if node.get("operator", "=") in ops else 0,
                key=f"n_{node['uid']}_op",
                label_visibility="collapsed",
            )
        with c3:
            node["value"] = st.text_input(
                "取值",
                value=node.get("value", ""),
                key=f"n_{node['uid']}_value",
                label_visibility="collapsed",
            )

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button(
                "⬆ 上移本条件条",
                key=f"n_{node['uid']}_up",
                use_container_width=True,
                disabled=index == 0,
            ):
                parent["children"][index], parent["children"][index - 1] = (
                    parent["children"][index - 1],
                    parent["children"][index],
                )
                set_flash("info", "已上移条件条")
                st.rerun()
        with b2:
            if st.button(
                "⬇ 下移本条件条",
                key=f"n_{node['uid']}_down",
                use_container_width=True,
                disabled=index == len(parent["children"]) - 1,
            ):
                parent["children"][index], parent["children"][index + 1] = (
                    parent["children"][index + 1],
                    parent["children"][index],
                )
                set_flash("info", "已下移条件条")
                st.rerun()
        with b3:
            if st.button("🗑 删除本条件条", key=f"n_{node['uid']}_del", use_container_width=True):
                parent["children"].pop(index)
                set_flash("info", "已删除条件条")
                st.rerun()


def render_rule_card(tree: list[dict], type_info: dict, rule: dict, rule_index: int):
    rule_id = rule.get("id", "").strip() or "(无 id)"
    prompt_text = rule.get("prompt", "").strip() or "(无建议)"
    if len(prompt_text) > 40:
        prompt_text = prompt_text[:40] + "…"

    with st.expander(f"📋 {rule_id}｜{prompt_text}"):
        rule["id"] = st.text_input(
            "ID",
            value=rule.get("id", ""),
            key=f"r_{rule['uid']}_id",
        )
        rule["prompt"] = st.text_input(
            "建议",
            value=rule.get("prompt", ""),
            key=f"r_{rule['uid']}_prompt",
        )

        rule["source"] = st.text_input(
            "溯源",
            value=rule.get("source", ""),
            key=f"r_{rule['uid']}_source",
        )

        unc = rule["uncertainty"]
        u1, u2 = st.columns(2)
        with u1:
            unc_levels = ["none", "low", "moderate", "high"]
            level = unc.get("level", "none")
            if level not in unc_levels:
                unc_levels = [level] + unc_levels
            unc["level"] = st.selectbox(
                "不确定性",
                unc_levels,
                index=unc_levels.index(level),
                key=f"r_{rule['uid']}_unc_level",
            )
        with u2:
            if unc["level"] != "none":
                unc["reason"] = st.text_input(
                    "不确定原因",
                    value=unc.get("reason", ""),
                    key=f"r_{rule['uid']}_unc_reason",
                )

        st.markdown("**条件**")
        render_condition_node(rule["conditions"], None, -1, 0)

        st.divider()

        b1, b2, b3, b4 = st.columns([1.6, 1, 1, 1], vertical_alignment="center")
        with b1:
            type_names = [t["type"] for t in tree]
            current_type = type_info["type"]
            target = st.selectbox(
                "重归类",
                type_names,
                index=type_names.index(current_type),
                key=f"r_{rule['uid']}_recat",
                label_visibility="collapsed",
            )
            if target != current_type:
                type_info["rules"].pop(rule_index)
                for t in tree:
                    if t["type"] == target:
                        t["rules"].append(rule)
                        break
                set_flash("info", f"已将规则「{rule.get('id', '(空)')}」重归类到「{target}」")
                st.rerun()
        with b2:
            if st.button(
                "⬆ 上移本规则条",
                key=f"r_{rule['uid']}_up",
                use_container_width=True,
                disabled=rule_index == 0,
            ):
                type_info["rules"][rule_index], type_info["rules"][rule_index - 1] = (
                    type_info["rules"][rule_index - 1],
                    type_info["rules"][rule_index],
                )
                set_flash("info", "已上移规则条")
                st.rerun()
        with b3:
            if st.button(
                "⬇ 下移本规则条",
                key=f"r_{rule['uid']}_down",
                use_container_width=True,
                disabled=rule_index == len(type_info["rules"]) - 1,
            ):
                type_info["rules"][rule_index], type_info["rules"][rule_index + 1] = (
                    type_info["rules"][rule_index + 1],
                    type_info["rules"][rule_index],
                )
                set_flash("info", "已下移规则条")
                st.rerun()
        with b4:
            if st.button("🗑 删除本规则条", key=f"r_{rule['uid']}_del", use_container_width=True):
                type_info["rules"].pop(rule_index)
                set_flash("info", f"已删除规则条「{rule.get('id', '(空)')}」")
                st.rerun()


def render_editor():
    tree = st.session_state["editor_tree"]

    # 增加规则类型
    with st.expander("➕ 增加规则类型"):
        new_type_name = st.text_input(
            "类型名",
            key="new_type_name",
            label_visibility="collapsed",
        )
        if st.button("创建规则类型", key="add_type_btn", use_container_width=True):
            name = new_type_name.strip()
            if not name:
                set_flash("error", "❌ 创建失败：类型名不能为空")
            elif any(t["type"] == name for t in tree):
                set_flash("error", f"❌ 创建失败：规则类型「{name}」已存在")
            else:
                tree.append({"type": name, "rules": []})
                set_flash("success", f"✅ 已创建规则类型「{name}」")
            st.rerun()

    if not tree:
        st.info("暂无规则类型，请先点击上方「➕ 增加规则类型」创建，或先「📥 提取规则」")
        return

    for type_info in tree:
        type_name = type_info["type"]
        with st.expander(f"📂 {type_name}（{len(type_info['rules'])} 条规则）"):
            b1, b2 = st.columns(2)
            with b1:
                if st.button("➕ 新建子规则条", key=f"t_{type_name}_add_rule", use_container_width=True):
                    type_info["rules"].append(create_new_rule(tree, type_name))
                    set_flash("info", "已新建规则条（默认 id 可自行修改）")
                    st.rerun()
            with b2:
                if st.button("🗑 删除本规则类型", key=f"t_{type_name}_del_type", use_container_width=True):
                    tree.remove(type_info)
                    set_flash("info", f"已删除规则类型「{type_name}」及其下所有规则条")
                    st.rerun()

            for rule_index, rule in enumerate(type_info["rules"]):
                render_rule_card(tree, type_info, rule, rule_index)

    st.markdown("**当前规则 JSON（只读）**")
    st.code(
        json.dumps(serialize_tree(tree), ensure_ascii=False, indent=2),
        language="json",
    )


# ==================== 视图渲染 ====================

def render_dict_view():
    if not st.session_state.get("show_dict_view", False):
        st.info('点击上方「📊 统计变量」按钮执行值域分析')
        return

    dictionary = load_json_file(WORKSPACE_DIR / "variable_dictionary.json")
    suggestions = load_json_file(WORKSPACE_DIR / "suggestions.json")
    errors = load_json_file(WORKSPACE_DIR / "domain_errors.json")

    if not dictionary and not errors:
        st.info('词典为空，请先点击「📊 统计变量」执行值域分析')
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("字段总数", len(dictionary))
    with col2:
        st.metric("建议", len(suggestions))
    with col3:
        st.metric("错误", len(errors))

    st.divider()

    if errors:
        with st.expander(f"🔴 错误（{len(errors)} 个，解决后才能校验和导出）", expanded=True):
            for err in errors:
                st.markdown(f"**{err['field']}** - {err.get('detail', '')}")
                if err.get("affected_rule_ids"):
                    st.markdown(f"  影响规则: {', '.join(err['affected_rule_ids'])}")

    if suggestions:
        with st.expander(f"💡 建议（{len(suggestions)} 条）", expanded=False):
            for s in suggestions:
                action_label = "合并同义项" if s.get("action") == "suggest_merge" else "检查"
                st.markdown(
                    f"**[{s.get('suggestion_id', '')}] {action_label}** `{s.get('field', '')}` "
                    f"({s.get('confidence', '')}): {s.get('reason', '')}"
                )
                if s.get("affected_rule_ids"):
                    st.markdown(f"  影响规则: {', '.join(s['affected_rule_ids'])}")

    st.divider()

    for entry in dictionary:
        with st.expander(f"**{entry['field']}** ({entry['type']})"):
            if entry["type"] == "enum":
                st.markdown("**值域:**")
                for v in entry.get("values", []):
                    st.markdown(f"- `{v['value']}` → {', '.join(v.get('rule_ids', []))}")
            else:
                st.markdown("**运算符:**")
                for op_entry in entry.get("operators", []):
                    st.markdown(f"- `{op_entry['operator']}` → {', '.join(op_entry.get('rule_ids', []))}")


def render_validation_view():
    if not st.session_state.get("show_validation_view", False):
        st.info('点击上方「✅ 校验规则」按钮执行校验')
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

    # 初始时：清空值域/校验产物与导出文件，自动载入编辑器
    if "app_initialized" not in st.session_state:
        st.session_state["app_initialized"] = True
        clear_derived_outputs()
        clear_outputs(OUTPUT_DIR)
        reset_view_flags()
        rules = load_json_file(WORKSPACE_DIR / "rules.json")
        st.session_state["editor_tree"] = load_tree(rules)

    # 按下「导出规则」「下载打包文件」以外的按钮时，清除旧的下载包
    if any(
        st.session_state.get(k) is True
        for k in st.session_state
        if (k.startswith(("r_", "n_", "t_", "btn_")) or k == "add_type_btn")
        and k not in ("btn_export", "btn_download")
    ):
        st.session_state.pop("export_package", None)

    with st.sidebar:
        st.header("📊 工作台状态")
        status = get_workspace_status()

        status_items = [
            ("提取规则", status["rules_exists"]),
            ("统计变量", status["dictionary_exists"] and not status["has_domain_errors"]),
            ("校验规则", status["validation_ok"]),
            ("导出规则", status["output_rules_exists"] and status["output_dict_exists"]),
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
            extract_btn = st.button("📥 提取规则", key="btn_extract", use_container_width=True, type="primary")

        with col2:
            save_btn = st.button("📋 保存规则", key="btn_save", use_container_width=True)

        with col3:
            dict_view_btn = st.button("📊 统计变量", key="btn_dict", use_container_width=True)

        with col4:
            validation_view_btn = st.button("✅ 校验规则", key="btn_validate", use_container_width=True)

        with col5:
            export_btn = st.button("📤 导出规则", key="btn_export", use_container_width=True)

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

                # 提取成功：自动载入编辑器 + 清空导出文件
                st.session_state["editor_tree"] = load_tree(rules)
                clear_outputs(OUTPUT_DIR)
                st.info("💡 规则已载入下方编辑器，可编辑后点击「📋 保存规则」")
            except Exception as e:
                # 提取失败：保留旧编辑器树、值域/校验产物与导出文件
                status_box.update(label="提取失败", state="error", expanded=True)
                st.error(f"❌ 提取失败: {e}")

        # 保存规则 - 校验并保存编辑器内容
        if save_btn:
            tree = st.session_state["editor_tree"]
            errors, saved_rules = validate_and_save_tree(tree, WORKSPACE_DIR / "rules.json")

            if errors:
                for e in errors:
                    ids = "、".join(e["rule_ids"])
                    if e["kind"] == "empty_id":
                        st.error(f"❌ 保存失败：类型「{e['rule_type']}」中存在 id 为空的规则")
                    elif e["kind"] == "duplicate_id":
                        st.error(f"❌ 保存失败：{e['detail']}")
                    elif e["kind"] == "incomplete_condition":
                        st.error(f"❌ 保存失败：规则 {ids} {e['detail']}")
                    elif e["kind"] == "no_conditions":
                        st.error(f"❌ 保存失败：规则 {ids} 没有任何条件条")
            else:
                clear_derived_outputs()
                clear_outputs(OUTPUT_DIR)
                reset_view_flags()
                st.session_state["editor_tree"] = load_tree(saved_rules)
                set_flash("success", f"✅ 规则已保存（共 {len(saved_rules)} 条），已按保存结果重新载入编辑器")
                st.rerun()

        # 统计变量 - 执行值域分析并填充变量视图板块
        if dict_view_btn:
            if not status["rules_exists"]:
                st.error("❌ 请先点击「📥 提取规则」")
                st.stop()

            with st.spinner("正在执行值域分析..."):
                try:
                    dict_path, sug_path, err_path, counts = analyze_domain(WORKSPACE_DIR)
                    if counts["errors"] > 0:
                        set_flash(
                            "error",
                            f"❌ 值域分析完成：{counts['fields']} 个字段，{counts['suggestions']} 条建议，"
                            f"{counts['errors']} 个错误，请先解决错误后再校验和导出",
                        )
                    else:
                        set_flash(
                            "success",
                            f"✅ 值域分析完成：{counts['fields']} 个字段，{counts['suggestions']} 条建议",
                        )
                    st.session_state["show_dict_view"] = True
                    st.session_state["active_view"] = "dict"
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 值域分析失败: {e}")

        # 校验规则 - 执行校验并填充校验报告板块
        if validation_view_btn:
            if not status["rules_exists"]:
                st.error("❌ 请先点击「📥 提取规则」")
                st.stop()

            if has_domain_errors():
                st.error("❌ 校验失败：请先解决「📊 统计变量」中的错误")
                st.stop()

            with st.spinner("正在执行校验..."):
                try:
                    report_path = validate_workspace(WORKSPACE_DIR)
                    report = load_json_file(report_path)
                    errors = sum(1 for r in report if r.get("severity") == "error")
                    warnings = sum(1 for r in report if r.get("severity") == "warning")
                    set_flash("success", f"✅ 校验完成: {errors} 个错误, {warnings} 个警告")
                    st.session_state["show_validation_view"] = True
                    st.session_state["active_view"] = "validation"
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 校验失败: {e}")

        # 导出规则 - 前置检查后同步 output/ 并打包下载
        if export_btn:
            if has_domain_errors():
                st.error("❌ 导出失败：请先解决「📊 统计变量」中的错误")
                st.stop()
            if not status["dictionary_exists"]:
                st.error("❌ 导出失败：请先完成「📊 统计变量」")
                st.stop()
            if not status["validation_exists"]:
                st.error("❌ 导出失败：请先完成「✅ 校验规则」")
                st.stop()

            try:
                export_workspace(WORKSPACE_DIR, OUTPUT_DIR)
                zip_bytes = build_export_zip(WORKSPACE_DIR)
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                st.session_state["export_package"] = (f"ebm_clinic_export_{timestamp}.zip", zip_bytes)
                set_flash("success", "✅ 导出完成，已同步到 output/ 目录")
                st.rerun()
            except Exception as e:
                st.error(f"❌ 导出失败: {e}")

        if st.session_state.get("export_package"):
            fname, data = st.session_state["export_package"]
            st.download_button(
                "⬇️ 下载打包文件",
                key="btn_download",
                data=data,
                file_name=fname,
                mime="application/zip",
                use_container_width=True,
            )

        # 点击提取后，当前状态视图区域由上方进度显示占据，不显示内容
        if not extract_btn:
            st.subheader("当前状态视图")
            show_flash()

            # 上方：源文本
            st.markdown("#### 📄 源文本")
            if status["source_exists"]:
                source_text = (WORKSPACE_DIR / "source.txt").read_text(encoding="utf-8")
                new_source = st.text_area("指南原文", value=source_text, height=300, label_visibility="collapsed")
                if st.button("💾 保存源文本", key="btn_save_source", use_container_width=True):
                    (WORKSPACE_DIR / "source.txt").write_text(new_source, encoding="utf-8")
                    st.success("已保存")
                    st.rerun()
            else:
                new_source = st.text_area("指南原文", height=300, placeholder="请输入指南原文...", label_visibility="collapsed")
                if st.button("💾 保存源文本", key="btn_save_source", use_container_width=True) and new_source.strip():
                    (WORKSPACE_DIR / "source.txt").write_text(new_source, encoding="utf-8")
                    st.success("已保存")
                    st.rerun()

            # 下方：三个视图切换
            tab_specs = [
                ("📋 规则编辑器", "rules"),
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
                        render_editor()
                    elif view_key == "dict":
                        render_dict_view()
                    else:
                        render_validation_view()

    # ==================== 患者核查 ====================
    with tab2:
        st.header("患者核查流程")

        if not status["output_rules_exists"] or not status["output_dict_exists"]:
            st.error("❌ 请先在「规则库工作台」完成「📤 导出规则」")
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

            st.button("加载示例", key="btn_load_example", on_click=load_example_callback, args=(selected_example,))

        structured_fields_text = st.text_area(
            "结构化字段（JSON 格式，可选）",
            key="structured_fields_text",
            height=100,
        )

        analyze_btn = st.button("🔍 开始分析", key="btn_analyze", type="primary", use_container_width=True)

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
