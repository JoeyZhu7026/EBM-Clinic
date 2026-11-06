"""
A 层 - 校验器
三项检查：字段词典覆盖 / 数值一致性 / 运算符一致性
"""

import json
import re
from collections import defaultdict
from pathlib import Path


NUM_PATTERN = re.compile(r'\d+(?:\.\d+)?')


def collect_fields(condition) -> list[str]:
    """递归收集条件中所有 field 名"""
    fields = []
    if isinstance(condition, dict):
        if "$and" in condition or "$or" in condition:
            key = "$and" if "$and" in condition else "$or"
            for sub in condition[key]:
                fields.extend(collect_fields(sub))
        elif "field" in condition:
            fields.append(condition["field"])
    return fields


def collect_field_operators(condition) -> list[tuple[str, str]]:
    """递归收集条件中所有 (field, operator) 对"""
    result = []
    if isinstance(condition, dict):
        if "$and" in condition or "$or" in condition:
            key = "$and" if "$and" in condition else "$or"
            for sub in condition[key]:
                result.extend(collect_field_operators(sub))
        elif "field" in condition and "operator" in condition:
            result.append((condition["field"], condition["operator"]))
    return result


def extract_numeric_tokens(text: str) -> list[str]:
    """从文本中提取所有数值"""
    return NUM_PATTERN.findall(text)


def check_field_coverage(rules: list[dict], dictionary: list[dict]) -> list[dict]:
    """检查一：字段词典覆盖"""
    report = []
    check_counter = 0

    if not dictionary:
        return report

    defined_fields = {entry["field"] for entry in dictionary}

    for rule in rules:
        fields = collect_fields(rule["rule_conditions"])
        for field in fields:
            if field not in defined_fields:
                check_counter += 1
                report.append({
                    "check_id": f"CHK-{check_counter:03d}",
                    "check_type": "field_coverage",
                    "severity": "warning",
                    "affected_rule_ids": [rule["rule_id"]],
                    "detail": f"field '{field}' 未在 variable_dictionary.json 中定义",
                })

    return report


def check_numeric_consistency(rules: list[dict]) -> list[dict]:
    """检查二：rule_prompt 与 source_reference 数值一致性"""
    report = []
    check_counter = 0

    for rule in rules:
        prompt_nums = extract_numeric_tokens(rule.get("rule_prompt", ""))
        source_nums = extract_numeric_tokens(rule.get("source_reference", ""))

        for n in prompt_nums:
            if n not in source_nums:
                check_counter += 1
                report.append({
                    "check_id": f"CHK-{check_counter:03d}",
                    "check_type": "numeric_consistency",
                    "severity": "error",
                    "affected_rule_ids": [rule["rule_id"]],
                    "detail": f"rule_prompt 中的数值 '{n}' 在 source_reference 原文中未找到对应",
                })

    return report


def check_operator_consistency(rules: list[dict]) -> list[dict]:
    """检查三：operator 一致性"""
    report = []
    check_counter = 0

    field_ops = defaultdict(lambda: {"ops": set(), "rule_ids": set()})

    for rule in rules:
        for field, op in collect_field_operators(rule["rule_conditions"]):
            field_ops[field]["ops"].add(op)
            field_ops[field]["rule_ids"].add(rule["rule_id"])

    for field, info in field_ops.items():
        ops = info["ops"]
        if len(ops) > 1:
            check_counter += 1
            report.append({
                "check_id": f"CHK-{check_counter:03d}",
                "check_type": "operator_consistency",
                "severity": "info",
                "affected_rule_ids": sorted(info["rule_ids"]),
                "detail": f"field '{field}' 使用了多种 operator：{sorted(ops)}，请确认是否有意",
            })

    return report


def validate_workspace(workspace_dir: Path) -> Path:
    """
    执行完整校验，生成 validation_report.json

    Returns:
        validation_report.json 的路径
    """
    workspace_dir = Path(workspace_dir)
    rules_path = workspace_dir / "rules.json"
    dict_path = workspace_dir / "variable_dictionary.json"
    report_path = workspace_dir / "validation_report.json"

    if not rules_path.exists():
        raise FileNotFoundError(f"找不到规则文件: {rules_path}")

    rules = json.loads(rules_path.read_text(encoding="utf-8"))

    dictionary = []
    if dict_path.exists():
        dictionary = json.loads(dict_path.read_text(encoding="utf-8"))

    full_report = []

    if dictionary:
        full_report.extend(check_field_coverage(rules, dictionary))
    else:
        full_report.append({
            "check_id": "CHK-000",
            "check_type": "field_coverage",
            "severity": "info",
            "affected_rule_ids": [],
            "detail": "variable_dictionary.json 不存在，请先执行值域分析",
        })

    full_report.extend(check_numeric_consistency(rules))
    full_report.extend(check_operator_consistency(rules))

    report_path.write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return report_path


if __name__ == "__main__":
    import sys
    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace")
    report_path = validate_workspace(workspace)
    print(f"校验报告已生成: {report_path}")
