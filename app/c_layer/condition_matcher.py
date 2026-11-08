"""
C 层 - 规则匹配引擎
三态逻辑求值：true / false / unknown
规则命中判定：matched / not_matched / not_applicable / undetermined
优先级计算：priority_rank 1-6

支持约束列表格式的区间重叠判定
"""

import json
from pathlib import Path
from typing import Any, Optional


def constraints_to_interval(constraints: list[dict]) -> Optional[tuple]:
    """
    将约束列表转换为区间表示

    返回 (lower, upper, lower_inclusive, upper_inclusive) 或 None
    - lower/upper 为 -inf/+inf 表示无界
    """
    if not constraints:
        return None

    lower = float('-inf')
    upper = float('inf')
    lower_inclusive = True
    upper_inclusive = True

    for c in constraints:
        op = c.get("operator", "=")
        operand = c.get("operand")

        if operand is None:
            continue

        if op == "=":
            lower = operand
            upper = operand
            lower_inclusive = True
            upper_inclusive = True
        elif op == "<":
            if operand < upper:
                upper = operand
                upper_inclusive = False
        elif op == "<=":
            if operand < upper or (operand == upper and not upper_inclusive):
                upper = operand
                upper_inclusive = True
        elif op == ">":
            if operand > lower:
                lower = operand
                lower_inclusive = False
        elif op == ">=":
            if operand > lower or (operand == lower and not lower_inclusive):
                lower = operand
                lower_inclusive = True

    if lower > upper:
        return None
    if lower == upper and not (lower_inclusive and upper_inclusive):
        return None

    return (lower, upper, lower_inclusive, upper_inclusive)


def condition_to_interval(operator: str, value: Any) -> tuple:
    """
    将规则条件转换为区间表示

    返回 (lower, upper, lower_inclusive, upper_inclusive)
    """
    if operator == "=":
        if isinstance(value, str):
            return (value, value, True, True)
        return (value, value, True, True)
    elif operator == "!=":
        return (float('-inf'), float('inf'), False, False)
    elif operator == "<":
        return (float('-inf'), value, False, False)
    elif operator == "<=":
        return (float('-inf'), value, False, True)
    elif operator == ">":
        return (value, float('inf'), False, False)
    elif operator == ">=":
        return (value, float('inf'), True, False)
    elif operator == "in":
        if isinstance(value, list) and value:
            return (min(value), max(value), True, True)
        return (float('-inf'), float('inf'), True, True)
    elif operator == "not_in":
        return (float('-inf'), float('inf'), False, False)
    else:
        return (float('-inf'), float('inf'), True, True)


def intervals_overlap(interval1: Optional[tuple], interval2: Optional[tuple]) -> bool:
    """
    判断两个区间是否有交集

    区间格式: (lower, upper, lower_inclusive, upper_inclusive)
    """
    if interval1 is None or interval2 is None:
        return False

    lower1, upper1, lower1_inc, upper1_inc = interval1
    lower2, upper2, lower2_inc, upper2_inc = interval2

    if isinstance(lower1, str) or isinstance(lower2, str):
        return str(lower1) == str(lower2) if lower1_inc and lower2_inc else False

    if lower1 > upper2 or lower2 > upper1:
        return False

    if lower1 == upper2:
        return lower1_inc and upper2_inc

    if lower2 == upper1:
        return lower2_inc and upper1_inc

    return True


def evaluate_atom(field: str, operator: str, expected: Any, patient_vars: dict) -> dict:
    """
    求值单个原子条件

    支持约束列表格式的区间重叠判定

    Returns:
        {
            "field": str,
            "operator": str,
            "expected": Any,
            "actual": list (约束列表),
            "variable_status": str,
            "condition_status": "true" | "false" | "unknown"
        }
    """
    var_info = patient_vars.get(field, {})
    actual_constraints = var_info.get("value", [])
    var_status = var_info.get("status", "missing")

    if var_status == "not_applicable":
        return {
            "field": field,
            "operator": operator,
            "expected": expected,
            "actual": [],
            "variable_status": "not_applicable",
            "condition_status": "false",
        }

    if var_status == "missing" or not actual_constraints:
        return {
            "field": field,
            "operator": operator,
            "expected": expected,
            "actual": [],
            "variable_status": "missing",
            "condition_status": "unknown",
        }

    first_operand = actual_constraints[0].get("operand") if actual_constraints else None

    if isinstance(first_operand, str):
        if operator == "=":
            result = any(c.get("operator") == "=" and c.get("operand") == expected for c in actual_constraints)
        elif operator == "!=":
            result = any(c.get("operator") == "=" and c.get("operand") != expected for c in actual_constraints)
        elif operator == "in":
            result = any(c.get("operator") == "=" and c.get("operand") in expected for c in actual_constraints)
        elif operator == "not_in":
            result = all(c.get("operator") == "=" and c.get("operand") not in expected for c in actual_constraints)
        else:
            result = False
    else:
        patient_interval = constraints_to_interval(actual_constraints)
        rule_interval = condition_to_interval(operator, expected)
        result = intervals_overlap(patient_interval, rule_interval)

    return {
        "field": field,
        "operator": operator,
        "expected": expected,
        "actual": actual_constraints,
        "variable_status": var_status,
        "condition_status": "true" if result else "false",
    }


def evaluate_condition(condition: dict, patient_vars: dict) -> tuple[str, list[dict]]:
    """
    递归求值条件表达式

    Returns:
        (condition_status, trace_list)
        condition_status: "true" | "false" | "unknown"
    """
    if "$and" in condition:
        sub_results = []
        sub_traces = []
        for sub in condition["$and"]:
            status, traces = evaluate_condition(sub, patient_vars)
            sub_results.append(status)
            sub_traces.extend(traces)

        if "false" in sub_results:
            return "false", sub_traces
        if "unknown" in sub_results:
            return "unknown", sub_traces
        return "true", sub_traces

    if "$or" in condition:
        sub_results = []
        sub_traces = []
        for sub in condition["$or"]:
            status, traces = evaluate_condition(sub, patient_vars)
            sub_results.append(status)
            sub_traces.extend(traces)

        if "true" in sub_results:
            return "true", sub_traces
        if "unknown" in sub_results:
            return "unknown", sub_traces
        return "false", sub_traces

    if "field" in condition:
        result = evaluate_atom(
            condition["field"],
            condition["operator"],
            condition["value"],
            patient_vars,
        )
        return result["condition_status"], [result]

    return "unknown", []


def determine_match_status(condition_status: str, trace: list[dict]) -> str:
    """
    根据条件求值结果和 trace 判定规则匹配状态

    Returns:
        "matched" | "not_matched" | "not_applicable" | "undetermined"
    """
    if condition_status == "true":
        return "matched"

    if condition_status == "unknown":
        return "undetermined"

    has_na = any(t["variable_status"] == "not_applicable" for t in trace)
    if has_na:
        return "not_applicable"

    return "not_matched"


def calculate_priority_rank(match_status: str, trace: list[dict]) -> int:
    """
    计算推荐采纳力度档位 (1-6)

    档位 1: 所有条件变量均为 provided 且条件为 true
    档位 2: 至少一个条件变量为 inferred（高置信）且其余满足
    档位 3: 条件中存在 missing 导致 undetermined
    档位 4: 条件为 false 但 false 依赖 inferred 变量
    档位 5: 条件为 false 且依赖 provided 变量
    档位 6: not_applicable
    """
    if match_status == "not_applicable":
        return 6

    if match_status == "undetermined":
        return 3

    if match_status == "matched":
        has_inferred = any(t["variable_status"] == "inferred" for t in trace)
        all_provided = all(t["variable_status"] == "provided" for t in trace)

        if all_provided:
            return 1
        if has_inferred:
            return 2
        return 1

    if match_status == "not_matched":
        has_inferred = any(t["variable_status"] == "inferred" for t in trace)
        if has_inferred:
            return 4
        return 5

    return 5


def match_single_rule(rule: dict, patient_vars: dict) -> dict:
    """
    对单条规则执行匹配

    Returns:
        完整的匹配结果对象
    """
    condition_status, trace = evaluate_condition(rule["rule_conditions"], patient_vars)
    match_status = determine_match_status(condition_status, trace)
    priority_rank = calculate_priority_rank(match_status, trace)

    return {
        "rule_id": rule["rule_id"],
        "match_status": match_status,
        "priority_rank": priority_rank,
        "condition_trace": trace,
        "rule_prompt": rule.get("rule_prompt", ""),
        "uncertainty": rule.get("uncertainty"),
        "source_reference": rule.get("source_reference", ""),
    }


def match_all_rules(rules: list[dict], patient_vars: dict) -> list[dict]:
    """
    对所有规则执行匹配

    Returns:
        匹配结果列表
    """
    return [match_single_rule(rule, patient_vars) for rule in rules]


def run_matching(
    rules_path: Path,
    patient_vars_path: Path,
    output_path: Path,
) -> list[dict]:
    """
    执行完整的 C 层匹配流程

    Returns:
        匹配结果列表
    """
    rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    patient_vars = json.loads(Path(patient_vars_path).read_text(encoding="utf-8"))

    results = match_all_rules(rules, patient_vars)

    Path(output_path).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return results


if __name__ == "__main__":
    import sys

    rules_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output/rules.json")
    vars_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("runtime/patient_variables.json")
    output_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("runtime/match_results.json")

    results = run_matching(rules_path, vars_path, output_path)

    matched = [r for r in results if r["match_status"] == "matched"]
    undetermined = [r for r in results if r["match_status"] == "undetermined"]
    not_matched = [r for r in results if r["match_status"] == "not_matched"]
    not_applicable = [r for r in results if r["match_status"] == "not_applicable"]

    print(f"匹配完成: {len(matched)} matched, {len(undetermined)} undetermined, "
          f"{len(not_matched)} not_matched, {len(not_applicable)} not_applicable")
