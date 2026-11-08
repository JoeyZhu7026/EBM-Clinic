"""
A 层 - 值域分析器
子步骤 A：统计值域 + 类型兼容性检查（纯程序）
子步骤 B：同义词检测（LLM）

类型推断规则：
- 范围运算符（< <= > >=）→ numeric
- 等值运算符（= !=）→ 优先 enum
- 范围运算符 + 字符串取值 → 错误
- 同字段数值/字符/空值混用 → 建议
- numeric 同时含范围运算符与等值运算符 → 建议
"""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

RANGE_OPS = {"<", "<=", ">", ">="}
EQ_OPS = {"=", "!="}


def collect_occurrences(condition, rule_id: str, out: dict) -> None:
    """递归收集条件中所有 (field, operator, value) 出现"""
    if not isinstance(condition, dict):
        return
    if "$and" in condition or "$or" in condition:
        key = "$and" if "$and" in condition else "$or"
        for sub in condition[key]:
            collect_occurrences(sub, rule_id, out)
    elif "field" in condition:
        field = condition["field"]
        info = out.setdefault(field, {"ops": set(), "rule_ids": set(), "items": []})
        info["ops"].add(condition.get("operator", "="))
        info["rule_ids"].add(rule_id)
        info["items"].append({
            "operator": condition.get("operator", "="),
            "value": condition.get("value"),
            "rule_id": rule_id,
        })


def build_dictionary(rules: list[dict]):
    """
    统计值域 + 类型兼容性检查

    Returns:
        (fields, errors, suggestions)

    fields（无误字段，新词典格式）:
    - enum: {"field", "type", "values": [{"value", "rule_ids"}]}
    - numeric: {"field", "type", "operators": [{"operator", "rule_ids"}]}
    errors: [{"field", "affected_rule_ids", "detail"}]
    suggestions: [{"field", "affected_rule_ids", "detail"}]
    """
    occurrences = {}
    for rule in rules:
        collect_occurrences(
            rule.get("rule_conditions", {}),
            rule.get("rule_id", ""),
            occurrences,
        )

    fields = []
    errors = []
    suggestions = []

    for field in sorted(occurrences):
        info = occurrences[field]
        ops = info["ops"]
        rule_ids = sorted(info["rule_ids"])
        items = info["items"]

        has_range = bool(ops & RANGE_OPS)
        has_eq = bool(ops & EQ_OPS)
        has_str = any(isinstance(it["value"], str) for it in items)
        has_num = any(
            isinstance(it["value"], (int, float)) and not isinstance(it["value"], bool)
            for it in items
        )
        has_empty = any(it["value"] is None or it["value"] == "" for it in items)

        if has_range and has_str:
            errors.append({
                "field": field,
                "affected_rule_ids": rule_ids,
                "detail": "范围运算符（< <= > >=）与字符串取值不兼容",
            })
            continue

        if has_empty and not has_str and not has_num:
            errors.append({
                "field": field,
                "affected_rule_ids": rule_ids,
                "detail": "取值全部为空，无法推断类型",
            })
            continue

        reasons = []
        if has_str and has_num:
            reasons.append("取值同时包含数值与字符")
        if has_empty and (has_str or has_num):
            reasons.append("存在空取值")
        if has_range and has_eq:
            reasons.append("同时使用范围运算符与等值运算符")

        if has_range:
            entry = {
                "field": field,
                "type": "numeric",
                "operators": [
                    {
                        "operator": op,
                        "rule_ids": sorted({
                            it["rule_id"]
                            for it in items
                            if it["operator"] == op
                        }),
                    }
                    for op in sorted(ops)
                ],
            }
        else:
            val_map = defaultdict(set)
            for it in items:
                if it["value"] is None or it["value"] == "":
                    continue
                val_map[str(it["value"])].add(it["rule_id"])
            entry = {
                "field": field,
                "type": "enum",
                "values": [
                    {"value": value, "rule_ids": sorted(rids)}
                    for value, rids in sorted(val_map.items())
                ],
            }
        fields.append(entry)

        if reasons:
            suggestions.append({
                "field": field,
                "affected_rule_ids": rule_ids,
                "detail": "；".join(reasons),
            })

    return fields, errors, suggestions


def detect_synonyms_llm(dictionary: list[dict]) -> list[dict]:
    """
    子步骤 B：使用 LLM 检测同义词

    对每个 enum 类型且 values 数量 >= 2 的 field，让 LLM 判断是否存在同义项
    """
    enum_fields = [
        entry for entry in dictionary
        if entry["type"] == "enum" and len(entry.get("values", [])) >= 2
    ]

    if not enum_fields:
        return []

    try:
        client = OpenAI(
            api_key=os.getenv("QWEN_API_KEY"),
            base_url=os.getenv("QWEN_API_BASE"),
        )
    except Exception:
        return []

    all_suggestions = []

    for entry in enum_fields:
        field = entry["field"]
        values = entry["values"]

        values_desc = "\n".join(
            f'- "{v["value"]}"（出现在：{", ".join(v["rule_ids"])}）'
            for v in values
        )

        prompt = (
            f"以下是指南中同一个变量（{field}）下的所有取值，以及每个取值出现在哪些规则中。\n"
            f"请判断是否存在语义等价、应当合并的取值对。\n\n"
            f"取值列表：\n{values_desc}\n\n"
            f'输出格式（JSON 数组）：\n'
            f'[\n'
            f'  {{\n'
            f'    "values": [\n'
            f'      {{"value": "...", "rule_ids": [...]}},\n'
            f'      {{"value": "...", "rule_ids": [...]}}\n'
            f'    ],\n'
            f'    "reason": "为什么认为它们等价（引用原文依据）",\n'
            f'    "confidence": "high" | "moderate" | "low"\n'
            f'  }}\n'
            f']\n\n'
            f"约束：\n"
            f"- 只判断语义等价，不要提出合并建议以外的操作。\n"
            f"- 如果拿不准，confidence 标 low 并把理由写清楚。\n"
            f"- 如果没有任何同义项，返回空数组 []。\n"
            f"- 只输出 JSON，不要输出其他内容。"
        )

        try:
            response = client.chat.completions.create(
                model=os.getenv("DR_EBM_MODEL", "qwen-plus"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            content = response.choices[0].message.content.strip()

            content = re.sub(r'^```json\s*', '', content)
            content = re.sub(r'\s*```$', '', content)

            suggestions = json.loads(content)
            if isinstance(suggestions, list):
                for s in suggestions:
                    s["field"] = field
                all_suggestions.extend(suggestions)
        except (json.JSONDecodeError, Exception) as e:
            print(f"[同义词检测] field={field} 解析失败: {e}")
            continue

    for i, s in enumerate(all_suggestions, start=1):
        s["suggestion_id"] = f"MERGE-{i:03d}"
        s["action"] = "suggest_merge"

    return all_suggestions


def analyze_domain(workspace_dir: Path):
    """
    执行值域分析，生成 variable_dictionary.json / suggestions.json / domain_errors.json

    Returns:
        (dict_path, suggestions_path, errors_path, counts)
        counts: {"fields": int, "suggestions": int, "errors": int}
    """
    workspace_dir = Path(workspace_dir)
    rules_path = workspace_dir / "rules.json"
    dict_path = workspace_dir / "variable_dictionary.json"
    suggestions_path = workspace_dir / "suggestions.json"
    errors_path = workspace_dir / "domain_errors.json"

    if not rules_path.exists():
        raise FileNotFoundError(f"找不到规则文件: {rules_path}")

    rules = json.loads(rules_path.read_text(encoding="utf-8"))

    fields, errors, structural_suggestions = build_dictionary(rules)

    dict_path.write_text(
        json.dumps(fields, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    llm_suggestions = detect_synonyms_llm(fields)

    suggestions = []
    for i, s in enumerate(structural_suggestions, start=1):
        suggestions.append({
            "suggestion_id": f"SUG-{i:03d}",
            "field": s["field"],
            "action": "suggest_review",
            "confidence": "high",
            "reason": s["detail"],
            "affected_rule_ids": s["affected_rule_ids"],
        })
    suggestions.extend(llm_suggestions)

    suggestions_path.write_text(
        json.dumps(suggestions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    errors_path.write_text(
        json.dumps(errors, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counts = {
        "fields": len(fields),
        "suggestions": len(suggestions),
        "errors": len(errors),
    }
    return dict_path, suggestions_path, errors_path, counts


if __name__ == "__main__":
    import sys

    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace")
    dict_path, sug_path, err_path, counts = analyze_domain(workspace)
    print(f"词典已生成: {dict_path}")
    print(f"建议已生成: {sug_path}")
    print(f"错误已生成: {err_path}")
    print(f"统计: {counts}")
