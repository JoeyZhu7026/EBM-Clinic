"""
A 层 - 规则提取器
使用 LangExtract + Qwen 从指南文本中提取结构化规则
"""

import contextlib
import io
import json
import re
import textwrap
from pathlib import Path
from typing import Callable, Optional

import langextract as lx
from langextract import factory
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")


def get_model():
    """创建 Qwen 模型实例"""
    config = factory.ModelConfig(
        model_id=os.getenv("DR_EBM_MODEL", "qwen-plus"),
        provider="OpenAILanguageModel",
        provider_kwargs={
            "api_key": os.getenv("QWEN_API_KEY"),
            "base_url": os.getenv("QWEN_API_BASE"),
        },
    )
    return factory.create_model(config)


PROMPT_DESCRIPTION = textwrap.dedent("""\
    从糖尿病视网膜病变临床指南文本中提取筛查规则。
    每一条规则对应一种患者分型/病情条件下的筛查建议。
    按文本中出现的顺序提取。

    【条件拆分规则——最重要】
    每个条件必须拆成原子化的 field / operator / value 三元组。
    - field: 英文标识符（如 diabetes_type, diabetes_onset_age, dr_severity,
      dme_type, visual_acuity, vision_symptom, pregnancy_with_diabetes_status）
    - operator: = / != / < / <= / > / >=
    - value: 字符串用引号，数字直接写数值

    多个条件之间的逻辑关系：
    - "$and": 表示"且"，所有条件必须同时满足
    - "$or": 表示"或"，满足任一条件即可
    - 可以嵌套，例如 {"$and": [A, {"$or": [B, C]}]}

    示例：
    "1型糖尿病，12岁之前发病" →
      {"$and": [
        {"field": "diabetes_type", "operator": "=", "value": "1型"},
        {"field": "diabetes_onset_age", "operator": "<", "value": 12}
      ]}

    "视力≤0.7 或者 突发视力下降及视物模糊" →
      {"$or": [
        {"field": "visual_acuity", "operator": "<=", "value": 0.7},
        {"field": "vision_symptom", "operator": "=", "value": "突发视力下降"},
        {"field": "vision_symptom", "operator": "=", "value": "视物模糊"}
      ]}

    【属性说明】
    - conditions: 上述 MongoDB 风格的 JSON 字符串
    - rule_prompt: 用自然语言概括该规则的建议内容（包含频率等），保留原文关键表述
    - uncertainty_level: 不确定性等级 none / moderate / high
    - uncertainty_reason: 若存在不确定性，用原文说明原因；否则留空

    注意：extraction_text 必须是原文中的逐字摘录，不要改写或概括。""")


FEW_SHOT_EXAMPLES = [
    lx.data.ExampleData(
        text=(
            "1型糖尿病：12岁之前发病者，自12岁起每年筛查；"
            "12岁之后发病者，起病5年内筛查，之后应每年随诊1次"
        ),
        extractions=[
            lx.data.Extraction(
                extraction_class="screening_rule",
                extraction_text="1型糖尿病：12岁之前发病者，自12岁起每年筛查",
                attributes={
                    "conditions": (
                        '{"$and": ['
                        '{"field": "diabetes_type", "operator": "=", "value": "1型"}, '
                        '{"field": "diabetes_onset_age", "operator": "<", "value": 12}'
                        ']}'
                    ),
                    "rule_prompt": "自12岁起每年筛查",
                    "uncertainty_level": "none",
                    "uncertainty_reason": "",
                },
            ),
            lx.data.Extraction(
                extraction_class="screening_rule",
                extraction_text="12岁之后发病者，起病5年内筛查，之后应每年随诊1次",
                attributes={
                    "conditions": (
                        '{"$and": ['
                        '{"field": "diabetes_type", "operator": "=", "value": "1型"}, '
                        '{"field": "diabetes_onset_age", "operator": ">=", "value": 12}'
                        ']}'
                    ),
                    "rule_prompt": "起病5年内筛查，之后应每年随诊1次",
                    "uncertainty_level": "none",
                    "uncertainty_reason": "",
                },
            ),
        ],
    ),
    lx.data.ExampleData(
        text=(
            "合并有黄斑水肿的患者随访频率应增加："
            "CI-DME患者每月随访1次；NCI-DME患者每3个月随访1次"
        ),
        extractions=[
            lx.data.Extraction(
                extraction_class="screening_rule",
                extraction_text="CI-DME患者每月随访1次",
                attributes={
                    "conditions": '{"field": "dme_type", "operator": "=", "value": "CI-DME"}',
                    "rule_prompt": "CI-DME患者每月随访1次",
                    "uncertainty_level": "none",
                    "uncertainty_reason": "",
                },
            ),
            lx.data.Extraction(
                extraction_class="screening_rule",
                extraction_text="NCI-DME患者每3个月随访1次",
                attributes={
                    "conditions": '{"field": "dme_type", "operator": "=", "value": "NCI-DME"}',
                    "rule_prompt": "NCI-DME患者每3个月随访1次",
                    "uncertainty_level": "none",
                    "uncertainty_reason": "",
                },
            ),
        ],
    ),
    lx.data.ExampleData(
        text=(
            "一旦视力≤0.7（20/30或4.8）或者患者出现突发的视力下降"
            "以及视物模糊应及时到有眼底病医疗资源的医院就诊"
        ),
        extractions=[
            lx.data.Extraction(
                extraction_class="screening_rule",
                extraction_text=(
                    "一旦视力≤0.7（20/30或4.8）或者患者出现突发的视力下降"
                    "以及视物模糊应及时到有眼底病医疗资源的医院就诊"
                ),
                attributes={
                    "conditions": (
                        '{"$or": ['
                        '{"field": "visual_acuity", "operator": "<=", "value": 0.7}, '
                        '{"field": "vision_symptom", "operator": "=", "value": "突发视力下降"}, '
                        '{"field": "vision_symptom", "operator": "=", "value": "视物模糊"}'
                        ']}'
                    ),
                    "rule_prompt": "及时到有眼底病医疗资源的医院就诊",
                    "uncertainty_level": "none",
                    "uncertainty_reason": "",
                },
            ),
        ],
    ),
]


def parse_conditions(conditions_str: str) -> dict:
    """将 JSON 字符串解析为 MongoDB 风格条件对象"""
    try:
        return json.loads(conditions_str)
    except (json.JSONDecodeError, TypeError):
        return {"$and": [{"field": "raw_text", "operator": "=", "value": conditions_str}]}


def build_uncertainty(level: str, reason: str) -> Optional[dict]:
    """构建不确定性对象"""
    if not level or level == "none":
        return None
    result = {"level": level}
    if reason:
        result["reason"] = reason
    return result


def convert_extractions_to_rules(extractions) -> list[dict]:
    """将 LangExtract 的 Extraction 列表转换为结构化规则 JSON"""
    rules = []
    for i, ext in enumerate(extractions, start=1):
        attrs = ext.attributes or {}
        rule = {
            "rule_id": f"SCREEN-{i:03d}",
            "rule_type": "screening",
            "rule_conditions": parse_conditions(attrs.get("conditions", "")),
            "rule_prompt": attrs.get("rule_prompt", ext.extraction_text),
            "uncertainty": build_uncertainty(
                attrs.get("uncertainty_level", "none"),
                attrs.get("uncertainty_reason", ""),
            ),
            "source_reference": ext.extraction_text,
        }
        rules.append(rule)
    return rules


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_PROCESSED_RE = re.compile(r"processed=([\d,]+)")


class _ProgressCapture(io.TextIOBase):
    """捕获 langextract 通过 stderr(tqdm) 输出的进度行，转发给回调"""

    def __init__(self, callback: Callable, total_chars: int):
        self._callback = callback
        self._total_chars = total_chars
        self._buf = ""

    def writable(self):
        return True

    def write(self, s):
        if not isinstance(s, str):
            s = s.decode("utf-8", errors="replace")
        self._buf += s
        while True:
            m = re.search(r"[\r\n]", self._buf)
            if not m:
                break
            line, self._buf = self._buf[: m.start()], self._buf[m.end():]
            self._emit(line)
        return len(s)

    def flush(self):
        pass

    def _emit(self, line: str):
        line = _ANSI_RE.sub("", line).strip()
        if not line:
            return
        m = _PROCESSED_RE.search(line)
        processed = int(m.group(1).replace(",", "")) if m else None
        self._callback(processed, self._total_chars, line)


def extract_rules(source_text: str, progress_callback: Optional[Callable] = None) -> list[dict]:
    """
    从指南文本中提取规则

    Args:
        source_text: 指南原文文本
        progress_callback: 可选进度回调，签名 (processed_chars, total_chars, raw_line)

    Returns:
        规则列表
    """
    model = get_model()

    kwargs = dict(
        text_or_documents=source_text,
        prompt_description=PROMPT_DESCRIPTION,
        examples=FEW_SHOT_EXAMPLES,
        model=model,
    )

    if progress_callback is None:
        result = lx.extract(**kwargs)
    else:
        capture = _ProgressCapture(progress_callback, len(source_text))
        with contextlib.redirect_stderr(capture):
            result = lx.extract(**kwargs)

    return convert_extractions_to_rules(result.extractions)


def extract_from_workspace(workspace_dir: Path, progress_callback: Optional[Callable] = None) -> Path:
    """
    从 workspace 目录读取 source.txt 并提取规则，写入 rules.json

    Returns:
        rules.json 的路径
    """
    workspace_dir = Path(workspace_dir)
    source_path = workspace_dir / "source.txt"
    output_path = workspace_dir / "rules.json"

    if not source_path.exists():
        raise FileNotFoundError(f"找不到源文件: {source_path}")

    source_text = source_path.read_text(encoding="utf-8")
    rules = extract_rules(source_text, progress_callback=progress_callback)

    output_path.write_text(
        json.dumps(rules, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output_path


if __name__ == "__main__":
    import sys

    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace")
    output = extract_from_workspace(workspace)
    print(f"已提取规则到: {output}")
