"""
A 层 - 图形化规则编辑器数据层
编辑器树结构、校验、归一化、序列化

编辑器树结构（存于 session_state）：
[
    {
        "type": "screening",
        "rules": [
            {
                "uid": "内部标识，用于 widget key",
                "id": "SCREEN-001",
                "prompt": "建议文本",
                "source": "溯源文本",
                "uncertainty": {"level": "none|low|moderate|high", "reason": ""},
                "conditions": 节点,
            }
        ]
    }
]

节点（conditions）：
- 逻辑框：{"uid", "logic": "and"|"or", "children": [节点...]}
- 条件条：{"uid", "field": str, "operator": str, "value": str}（value 统一存字符串，保存时智能转类型）
"""

import json
import re
import uuid
from pathlib import Path
from typing import Optional

OPERATORS = ["=", "!=", "<", "<=", ">", ">="]


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def new_logic_node(logic: str = "and") -> dict:
    return {"uid": _uid(), "logic": logic, "children": []}


def new_condition_node() -> dict:
    return {"uid": _uid(), "field": "", "operator": "=", "value": ""}


def next_rule_id(tree: list[dict]) -> str:
    """扫描 rule-XXX 通用格式的 id，返回最大号 + 1"""
    max_n = 0
    for type_info in tree:
        for rule in type_info["rules"]:
            m = re.fullmatch(r"rule-(\d+)", (rule.get("id") or "").strip(), re.IGNORECASE)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"rule-{max_n + 1:03d}"


def create_new_rule(tree: list[dict], type_name: str) -> dict:
    return {
        "uid": _uid(),
        "id": next_rule_id(tree),
        "prompt": "",
        "source": "",
        "uncertainty": {"level": "none", "reason": ""},
        "conditions": new_logic_node(),
    }


def conditions_to_editor(condition: dict) -> Optional[dict]:
    """rules.json 条件树 → 编辑器节点树（value 统一转字符串）"""
    if not condition:
        return None
    if "$and" in condition:
        children = [c for c in (conditions_to_editor(x) for x in condition["$and"]) if c]
        return {"uid": _uid(), "logic": "and", "children": children}
    if "$or" in condition:
        children = [c for c in (conditions_to_editor(x) for x in condition["$or"]) if c]
        return {"uid": _uid(), "logic": "or", "children": children}
    if "field" in condition:
        return {
            "uid": _uid(),
            "field": condition.get("field", ""),
            "operator": condition.get("operator", "="),
            "value": str(condition.get("value", "")),
        }
    return None


def load_tree(rules: list[dict]) -> list[dict]:
    """rules.json → 编辑器树（按 rule_type 分组，保持原顺序）"""
    type_order = []
    for rule in rules:
        rt = rule.get("rule_type", "unknown")
        if rt not in type_order:
            type_order.append(rt)

    type_map = {t: {"type": t, "rules": []} for t in type_order}

    for rule in rules:
        rt = rule.get("rule_type", "unknown")
        node = conditions_to_editor(rule.get("rule_conditions", {}))
        if node is None:
            node = new_logic_node()
        elif "field" in node:
            # 根节点是裸条件时包一层逻辑框，保存时归一化会还原
            node = {"uid": _uid(), "logic": "and", "children": [node]}
        unc = rule.get("uncertainty") or {}
        type_map[rt]["rules"].append({
            "uid": _uid(),
            "id": rule.get("rule_id", ""),
            "prompt": rule.get("rule_prompt", ""),
            "source": rule.get("source_reference", ""),
            "uncertainty": {
                "level": unc.get("level", "none"),
                "reason": unc.get("reason", ""),
            },
            "conditions": node,
        })

    return [type_map[t] for t in type_order]


def parse_value(raw: str):
    """字符串取值 → 智能转类型：能转 int → int，能转 float → float，否则保持字符串"""
    try:
        return int(raw)
    except (ValueError, TypeError):
        pass
    try:
        return float(raw)
    except (ValueError, TypeError):
        pass
    return raw


def normalize_conditions(node) -> Optional[dict]:
    """
    归一化条件树（自底向上递归）：
    1. 删除空逻辑框
    2. 仅含一个内容物的逻辑框取消嵌套
    3. 与外层逻辑框逻辑相同的逻辑框取消嵌套
    """
    if node is None:
        return None

    if "field" in node:
        return {
            "field": node["field"],
            "operator": node["operator"],
            "value": parse_value(node["value"]),
        }

    children = []
    for child in node.get("children", []):
        normalized = normalize_conditions(child)
        if normalized is not None:
            children.append(normalized)

    if not children:
        return None
    if len(children) == 1:
        return children[0]

    logic_key = "$" + node["logic"]
    merged = []
    for child in children:
        if logic_key in child:
            merged.extend(child[logic_key])
        else:
            merged.append(child)

    if len(merged) == 1:
        return merged[0]
    return {logic_key: merged}


def collect_condition_bars(node, out: list) -> None:
    """收集条件树中所有条件条"""
    if node is None:
        return
    if "field" in node:
        out.append(node)
        return
    for child in node.get("children", []):
        collect_condition_bars(child, out)


def serialize_uncertainty(unc: dict):
    level = (unc or {}).get("level", "none")
    if level == "none":
        return None
    return {"level": level, "reason": (unc or {}).get("reason", "")}


def serialize_tree(tree: list[dict]) -> list[dict]:
    """编辑器树 → rules.json 格式的规则列表"""
    rules = []
    for type_info in tree:
        for rule in type_info["rules"]:
            rules.append({
                "rule_id": (rule.get("id") or "").strip(),
                "rule_type": type_info["type"],
                "rule_conditions": normalize_conditions(rule["conditions"]) or {},
                "rule_prompt": rule.get("prompt", ""),
                "uncertainty": serialize_uncertainty(rule.get("uncertainty")),
                "source_reference": rule.get("source", ""),
            })
    return rules


def validate_and_save_tree(tree: list[dict], rules_path: Path):
    """
    校验编辑器树并保存为 rules.json

    校验项：空 id / 重复 id / 条件条三要素不齐全 / 规则没有任何条件条

    Returns:
        (errors, saved_rules): errors 非空时不写入文件，saved_rules 为 []
    """
    rules_path = Path(rules_path)
    errors = []

    located = []
    for type_info in tree:
        for rule in type_info["rules"]:
            located.append((type_info["type"], rule))

    for type_name, rule in located:
        rid = (rule.get("id") or "").strip()
        if not rid:
            errors.append({
                "kind": "empty_id",
                "rule_type": type_name,
                "rule_ids": ["(空)"],
                "detail": "规则 id 为空",
            })

    seen = {}
    for type_name, rule in located:
        rid = (rule.get("id") or "").strip()
        if not rid:
            continue
        if rid in seen:
            errors.append({
                "kind": "duplicate_id",
                "rule_type": type_name,
                "rule_ids": [rid],
                "detail": f"id「{rid}」重复（已存在于「{seen[rid]}」类型中）",
            })
        else:
            seen[rid] = type_name

    for type_name, rule in located:
        rid = (rule.get("id") or "").strip() or "(空)"
        bars = []
        collect_condition_bars(rule.get("conditions"), bars)
        if not bars:
            errors.append({
                "kind": "no_conditions",
                "rule_type": type_name,
                "rule_ids": [rid],
                "detail": "规则没有任何条件条",
            })
            continue
        incomplete = [
            b for b in bars
            if not (b.get("field") or "").strip()
            or not (b.get("operator") or "").strip()
            or not str(b.get("value", "")).strip()
        ]
        if incomplete:
            errors.append({
                "kind": "incomplete_condition",
                "rule_type": type_name,
                "rule_ids": [rid],
                "detail": f"存在 {len(incomplete)} 个未填写齐全的条件条（变量名、运算符、取值缺一不可）",
            })

    if errors:
        return errors, []

    rules = serialize_tree(tree)
    rules_path.write_text(
        json.dumps(rules, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [], rules


if __name__ == "__main__":
    import sys

    rules_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace/rules.json")
    rules = json.loads(rules_file.read_text(encoding="utf-8"))
    tree = load_tree(rules)
    errs, saved = validate_and_save_tree(tree, Path("workspace/rules.test_out.json"))
    print(f"errors: {len(errs)}, saved rules: {len(saved)}")
    for e in errs:
        print(e)
