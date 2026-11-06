"""
B 层 - 患者变量解析模块
B1: 确定性推理（na_rules）
B2: LLM 变量解析（输出约束列表格式）
B3: 约束交集计算 + 置信度门槛裁决
B4: 合并 structured_fields
"""

import json
import re
from pathlib import Path
from typing import Optional
import math

from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")


def load_config(config_path: Path) -> dict:
    """加载 B 层配置"""
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def evaluate_na_condition(condition: str, variables: dict) -> bool:
    """
    评估 na_rule 条件

    支持的条件格式：
    - "field == 'value'"
    - "field != 'value'"
    - "field < number"
    - "field > number"
    - "field <= number"
    - "field >= number"
    """
    match = re.match(r"(\w+)\s*(==|!=|<|>|<=|>=)\s*['\"]?([^'\"]+)['\"]?", condition.strip())
    if not match:
        return False

    field, op, value = match.groups()

    actual = variables.get(field)
    if actual is None:
        return False

    if isinstance(actual, dict) and "value" in actual:
        actual = actual["value"]

    try:
        if op == "==":
            return str(actual) == value
        elif op == "!=":
            return str(actual) != value
        elif op == "<":
            return float(actual) < float(value)
        elif op == ">":
            return float(actual) > float(value)
        elif op == "<=":
            return float(actual) <= float(value)
        elif op == ">=":
            return float(actual) >= float(value)
    except (ValueError, TypeError):
        return False

    return False


def apply_na_rules(na_rules: list[dict], variables: dict) -> dict:
    """
    B1: 应用确定性推理规则

    返回需要设置为 not_applicable 的字段列表
    """
    na_fields = {}

    for rule in na_rules:
        field = rule["field"]
        condition = rule["condition"]
        value = rule["value"]

        if evaluate_na_condition(condition, variables):
            na_fields[field] = value

    return na_fields


def build_llm_prompt(dictionary: list[dict], na_fields: dict, free_text: str) -> str:
    """构建 LLM 变量解析的 prompt - 输出约束列表格式"""
    field_defs = []
    for entry in dictionary:
        field = entry["field"]
        ftype = entry["type"]
        values = entry.get("values", [])

        if ftype == "enum" and values:
            value_list = ", ".join(f'"{v["value"]}"' for v in values)
            field_defs.append(f"- {field} (enum): 可选值 [{value_list}]")
        elif ftype == "numeric":
            field_defs.append(f"- {field} (numeric): 数值类型，支持约束列表")

    na_list = "\n".join(f"- {field}: {reason}" for field, reason in na_fields.items()) if na_fields else "无"

    prompt = f"""你是一名眼科临床信息抽取助手。请从以下患者描述中提取结构化变量。

变量定义（来自指南规则库）：
{chr(10).join(field_defs)}

已知不适用变量（无需提取）：
{na_list}

患者描述：
{free_text}

【重要】输出格式说明：
所有字段的 value 统一使用约束列表格式：
- enum 类型：[{{"operator": "=", "operand": "值"}}]
- numeric 类型：支持多个约束组合
  - 精确值：[{{"operator": "=", "operand": 10}}]
  - 单侧约束：[{{"operator": "<", "operand": 12}}] 表示 < 12
  - 区间约束：[{{"operator": ">", "operand": 10}}, {{"operator": "<", "operand": 12}}] 表示 (10, 12)
  - 双侧约束：[{{"operator": ">=", "operand": 10}}, {{"operator": "<=", "operand": 12}}] 表示 [10, 12]

示例：
- "1型糖尿病" → diabetes_type: [{{"operator": "=", "operand": "1型"}}]
- "12岁前发病" → diabetes_onset_age: [{{"operator": "<", "operand": 12}}]
- "视力≤0.7" → visual_acuity: [{{"operator": "<=", "operand": 0.7}}]
- "病程8年" → diabetes_duration: [{{"operator": "=", "operand": 8}}]

输出格式（JSON）：
{{
  "field_name": {{
    "value": [{{"operator": "...", "operand": ...}}],
    "status": "provided | inferred | missing",
    "source_text": "原文中的依据片段",
    "confidence": "high | medium | low"
  }}
}}

约束：
- 只能使用变量定义中列出的 field 和 enum 值。
- 如果原文没有提及某个 field，status 填 "missing"，value 填 []。
- confidence 根据原文明确程度判断：原文直接陈述为 high，需要推断为 medium，不确定为 low。
- 不要编造原文中没有的信息。
- 只输出 JSON，不要输出其他内容。"""

    return prompt


def parse_variables_llm(free_text: str, dictionary: list[dict], na_fields: dict) -> dict:
    """
    B2: 使用 LLM 解析患者变量
    """
    try:
        client = OpenAI(
            api_key=os.getenv("QWEN_API_KEY"),
            base_url=os.getenv("QWEN_API_BASE"),
        )
    except Exception as e:
        print(f"[B2] OpenAI 客户端初始化失败: {e}")
        return {}

    prompt = build_llm_prompt(dictionary, na_fields, free_text)

    try:
        response = client.chat.completions.create(
            model=os.getenv("DR_EBM_MODEL", "qwen-plus"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content.strip()

        content = re.sub(r'^```json\s*', '', content)
        content = re.sub(r'\s*```$', '', content)

        return json.loads(content)
    except Exception as e:
        print(f"[B2] LLM 调用失败: {e}")
        return {}


def compute_constraint_intersection(constraints: list[dict]) -> Optional[tuple]:
    """
    计算约束列表的交集

    返回 (lower, upper, lower_inclusive, upper_inclusive) 或 None（如果交集为空）
    - lower/upper 为 None 表示无界
    - lower_inclusive/upper_inclusive 表示是否包含边界
    """
    if not constraints:
        return None

    lower = None
    upper = None
    lower_inclusive = True
    upper_inclusive = True

    for c in constraints:
        op = c.get("operator", "=")
        operand = c.get("operand")

        if operand is None:
            continue

        if op == "=":
            if lower is not None and operand < lower:
                return None
            if upper is not None and operand > upper:
                return None
            lower = operand
            upper = operand
            lower_inclusive = True
            upper_inclusive = True
        elif op == "<":
            if upper is None or operand < upper:
                upper = operand
                upper_inclusive = False
            elif operand == upper:
                upper_inclusive = False
        elif op == "<=":
            if upper is None or operand < upper:
                upper = operand
                upper_inclusive = True
        elif op == ">":
            if lower is None or operand > lower:
                lower = operand
                lower_inclusive = False
            elif operand == lower:
                lower_inclusive = False
        elif op == ">=":
            if lower is None or operand > lower:
                lower = operand
                lower_inclusive = True

    if lower is not None and upper is not None:
        if lower > upper:
            return None
        if lower == upper and not (lower_inclusive and upper_inclusive):
            return None

    return (lower, upper, lower_inclusive, upper_inclusive)


def normalize_constraints(value) -> list[dict]:
    """
    将各种格式的 value 统一转换为约束列表格式

    支持的输入格式：
    - 字符串（enum）: "1型" → [{"operator": "=", "operand": "1型"}]
    - 数值: 10 → [{"operator": "=", "operand": 10}]
    - 约束列表: [{"operator": "<", "operand": 12}] → 直接返回
    - None: → []
    """
    if value is None:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, str):
        return [{"operator": "=", "operand": value}]

    if isinstance(value, (int, float)):
        return [{"operator": "=", "operand": value}]

    return []


def apply_confidence_threshold(
    parsed_vars: dict,
    impact_level_map: dict,
    confidence_threshold: dict,
) -> dict:
    """
    B3: 置信度门槛裁决 + 约束交集计算

    根据变量影响等级和置信度阈值，将低置信推断降级为 missing
    同时计算约束交集，如果为空则标记为 missing
    """
    result = {}

    for field, var_info in parsed_vars.items():
        impact = impact_level_map.get(field, "low")
        threshold = confidence_threshold.get(impact, 0.0)

        confidence_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
        var_confidence = confidence_map.get(var_info.get("confidence", "low"), 0.0)

        if var_info.get("status") == "inferred" and var_confidence < threshold:
            result[field] = {
                "value": [],
                "status": "missing",
                "source_text": var_info.get("source_text"),
                "confidence": var_info.get("confidence"),
                "reason": f"low_confidence_inference: {impact} impact requires >= {threshold} confidence",
            }
            continue

        constraints = normalize_constraints(var_info.get("value"))

        if constraints:
            first_operand_type = type(constraints[0].get("operand"))
            if first_operand_type in (int, float):
                intersection = compute_constraint_intersection(constraints)
                if intersection is None:
                    result[field] = {
                        "value": [],
                        "status": "missing",
                        "source_text": var_info.get("source_text"),
                        "confidence": var_info.get("confidence"),
                        "reason": "constraint_intersection_empty",
                    }
                    continue

        var_info["value"] = constraints
        result[field] = var_info

    return result


def merge_structured_fields(
    parsed_vars: dict,
    structured_fields: dict,
) -> dict:
    """
    B4: 合并用户直接提供的结构化字段

    结构化字段优先级高于 LLM 推断
    """
    result = parsed_vars.copy()

    for field, value in structured_fields.items():
        constraints = normalize_constraints(value)
        result[field] = {
            "value": constraints,
            "status": "provided",
            "source_text": "user_provided",
            "confidence": "high",
        }

    return result


def parse_patient_variables(
    patient_input: dict,
    dictionary: list[dict],
    config: dict,
) -> dict:
    """
    完整的 B 层处理流程

    Args:
        patient_input: {"free_text": str, "structured_fields": dict}
        dictionary: 变量词典
        config: B 层配置

    Returns:
        结构化患者变量字典（value 统一为约束列表格式）
    """
    free_text = patient_input.get("free_text", "")
    structured_fields = patient_input.get("structured_fields", {})

    na_rules = config.get("na_rules", [])
    impact_level_map = config.get("impact_level_map", {})
    confidence_threshold = config.get("confidence_threshold", {})

    temp_vars = dict(structured_fields)

    na_fields = apply_na_rules(na_rules, temp_vars)

    llm_parsed = parse_variables_llm(free_text, dictionary, na_fields)

    adjudicated = apply_confidence_threshold(llm_parsed, impact_level_map, confidence_threshold)

    for field, value in na_fields.items():
        adjudicated[field] = {
            "value": [],
            "status": "not_applicable",
            "source_text": None,
            "confidence": None,
            "reason": f"na_rule: {field} = {value}",
        }

    final = merge_structured_fields(adjudicated, structured_fields)

    all_fields = {entry["field"] for entry in dictionary}
    for field in all_fields:
        if field not in final:
            final[field] = {
                "value": [],
                "status": "missing",
                "source_text": None,
                "confidence": None,
                "reason": "missing_from_input",
            }

    return final


if __name__ == "__main__":
    sample_input = {
        "free_text": "患者男性，45岁，1型糖尿病，12岁前发病，目前无明显视力下降，眼底检查示重度非增生期DR，无黄斑水肿。",
        "structured_fields": {
            "sex": "男",
            "age": 45,
        },
    }

    dict_path = Path("output/variable_dictionary.json")
    config_path = Path("config/variable_parsing_config.json")

    if dict_path.exists() and config_path.exists():
        dictionary = json.loads(dict_path.read_text(encoding="utf-8"))
        config = load_config(config_path)

        result = parse_patient_variables(sample_input, dictionary, config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("请先完成 A 层提取和导出")
