"""
A 层 - 值域分析器
子步骤 A：统计值域（纯程序）
子步骤 B：同义词检测（LLM）
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


def build_dictionary(rules: list[dict]) -> list[dict]:
    """
    子步骤 A：从规则列表统计生成变量词典

    遍历所有规则的 rule_conditions，提取每个 field 的值域和引用规则 ID
    """
    field_map = defaultdict(lambda: {"values": defaultdict(list), "rule_ids": set(), "types": set()})

    def walk_condition(cond, rule_id):
        if isinstance(cond, dict) and ("$and" in cond or "$or" in cond):
            key = "$and" if "$and" in cond else "$or"
            for sub in cond[key]:
                walk_condition(sub, rule_id)
        elif isinstance(cond, dict) and "field" in cond:
            field = cond["field"]
            value = cond["value"]
            field_map[field]["rule_ids"].add(rule_id)
            if isinstance(value, (int, float)):
                field_map[field]["types"].add("numeric")
            else:
                field_map[field]["types"].add("enum")
                field_map[field]["values"][value].append(rule_id)

    for rule in rules:
        walk_condition(rule["rule_conditions"], rule["rule_id"])

    result = []
    for field, info in field_map.items():
        ftype = "numeric" if "numeric" in info["types"] else "enum"
        values = []
        if ftype == "enum":
            for v, rids in info["values"].items():
                values.append({"value": v, "rule_ids": sorted(set(rids))})
        result.append({
            "field": field,
            "type": ftype,
            "values": values,
            "rule_ids": sorted(info["rule_ids"]),
        })
    return sorted(result, key=lambda x: x["field"])


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


def analyze_domain(workspace_dir: Path) -> tuple[Path, Path]:
    """
    执行值域分析，生成 variable_dictionary.json 和 suggestions.json

    Returns:
        (dictionary_path, suggestions_path)
    """
    workspace_dir = Path(workspace_dir)
    rules_path = workspace_dir / "rules.json"
    dict_path = workspace_dir / "variable_dictionary.json"
    suggestions_path = workspace_dir / "suggestions.json"

    if not rules_path.exists():
        raise FileNotFoundError(f"找不到规则文件: {rules_path}")

    rules = json.loads(rules_path.read_text(encoding="utf-8"))

    dictionary = build_dictionary(rules)
    dict_path.write_text(
        json.dumps(dictionary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    suggestions = detect_synonyms_llm(dictionary)
    suggestions_path.write_text(
        json.dumps(suggestions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return dict_path, suggestions_path


if __name__ == "__main__":
    import sys
    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace")
    dict_path, sug_path = analyze_domain(workspace)
    print(f"词典已生成: {dict_path}")
    print(f"建议已生成: {sug_path}")
