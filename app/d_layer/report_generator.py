"""
D 层 - 输出生成模块
纯模板生成结构化报告，不经过 LLM
"""

import json
from pathlib import Path
from typing import Optional


def build_patient_summary(patient_vars: dict) -> dict:
    """构建患者变量摘要"""
    summary = {
        "provided": [],
        "inferred": [],
        "not_applicable": [],
        "missing": [],
    }

    for field, info in patient_vars.items():
        status = info.get("status", "missing")
        value = info.get("value")
        source_text = info.get("source_text")
        reason = info.get("reason")

        if status == "provided":
            summary["provided"].append({"field": field, "value": value})
        elif status == "inferred":
            entry = {"field": field, "value": value}
            if source_text:
                entry["source_text"] = source_text
            summary["inferred"].append(entry)
        elif status == "not_applicable":
            entry = {"field": field}
            if reason:
                entry["reason"] = reason
            summary["not_applicable"].append(entry)
        elif status == "missing":
            entry = {"field": field}
            if reason:
                entry["reason"] = reason
            if source_text:
                entry["source_text"] = source_text
            summary["missing"].append(entry)

    return summary


def format_constraints(constraints: list[dict]) -> str:
    """将约束列表格式化为可读字符串"""
    if not constraints:
        return "未知"

    parts = []
    for c in constraints:
        op = c.get("operator", "=")
        operand = c.get("operand")
        if op == "=":
            parts.append(f"= {operand}")
        elif op == "!=":
            parts.append(f"!= {operand}")
        elif op == "<":
            parts.append(f"< {operand}")
        elif op == "<=":
            parts.append(f"<= {operand}")
        elif op == ">":
            parts.append(f"> {operand}")
        elif op == ">=":
            parts.append(f">= {operand}")

    return " 且 ".join(parts) if parts else "未知"


def build_condition_summary(trace: list[dict]) -> str:
    """将 condition_trace 转为可读的条件摘要"""
    parts = []
    for t in trace:
        field = t["field"]
        op = t["operator"]
        expected = t["expected"]
        actual = t["actual"]
        var_status = t["variable_status"]

        status_text = {
            "provided": "已提供",
            "inferred": "推断",
            "missing": "缺失",
            "not_applicable": "不适用",
        }.get(var_status, var_status)

        if var_status in ("missing", "not_applicable"):
            parts.append(f"{field} {op} {expected}（{status_text}）")
        else:
            actual_str = format_constraints(actual) if isinstance(actual, list) else str(actual)
            parts.append(f"{field} {op} {expected}（{status_text} {actual_str}）")

    return " 且 ".join(parts) if parts else "无条件"


def build_structured_report(
    patient_vars: dict,
    match_results: list[dict],
    rules: list[dict],
) -> dict:
    """构建结构化报告"""
    report = {
        "patient_summary": build_patient_summary(patient_vars),
        "matched_rules": [],
        "undetermined_rules": [],
        "not_applicable_rules": [],
        "not_matched_rules": [],
    }

    rule_map = {r["rule_id"]: r for r in rules}

    for result in match_results:
        rule_id = result["rule_id"]
        rule = rule_map.get(rule_id, {})
        match_status = result["match_status"]
        priority_rank = result["priority_rank"]

        entry = {
            "rule_id": rule_id,
            "priority_rank": priority_rank,
            "rule_type": rule.get("rule_type", "unknown"),
            "rule_prompt": result["rule_prompt"],
            "uncertainty": result["uncertainty"],
            "source_reference": result["source_reference"],
            "condition_trace": result["condition_trace"],
        }

        if match_status == "matched":
            entry["condition_summary"] = build_condition_summary(result["condition_trace"])
            report["matched_rules"].append(entry)

        elif match_status == "undetermined":
            missing_vars = []
            for t in result["condition_trace"]:
                if t["variable_status"] == "missing":
                    var_info = patient_vars.get(t["field"], {})
                    missing_vars.append({
                        "field": t["field"],
                        "reason": var_info.get("reason", "missing_from_input"),
                        "source_text": var_info.get("source_text"),
                    })
            entry["missing_variables"] = missing_vars
            report["undetermined_rules"].append(entry)

        elif match_status == "not_applicable":
            na_reasons = []
            for t in result["condition_trace"]:
                if t["variable_status"] == "not_applicable":
                    var_info = patient_vars.get(t["field"], {})
                    na_reasons.append(var_info.get("reason", f"{t['field']} 不适用"))
            entry["reason"] = "; ".join(na_reasons) if na_reasons else "变量不适用"
            report["not_applicable_rules"].append(entry)

        elif match_status == "not_matched":
            entry["condition_summary"] = build_condition_summary(result["condition_trace"])
            report["not_matched_rules"].append(entry)

    report["matched_rules"].sort(key=lambda x: x["priority_rank"])
    report["undetermined_rules"].sort(key=lambda x: x["priority_rank"])

    return report


def generate_report(
    patient_vars: dict,
    match_results: list[dict],
    rules: list[dict],
    output_dir: Path,
) -> tuple[dict, None]:
    """
    D 层报告生成（纯模板，不使用 LLM）

    Returns:
        (structured_report, None)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    structured = build_structured_report(patient_vars, match_results, rules)

    structured_path = output_dir / "final_report.json"
    structured_path.write_text(
        json.dumps(structured, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return structured, None


if __name__ == "__main__":
    import sys

    rules_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/rules.json")
    vars_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("runtime/patient_variables.json")
    match_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("runtime/match_results.json")
    output_dir = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("runtime")

    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    patient_vars = json.loads(vars_path.read_text(encoding="utf-8"))
    match_results = json.loads(match_path.read_text(encoding="utf-8"))

    structured, _ = generate_report(patient_vars, match_results, rules, output_dir)

    print(json.dumps(structured, ensure_ascii=False, indent=2))
