"""D 层输出生成模块"""

from .report_generator import (
    build_patient_summary,
    build_structured_report,
    generate_report,
    format_constraints,
    build_condition_summary,
)

__all__ = [
    "build_patient_summary",
    "build_structured_report",
    "generate_report",
    "format_constraints",
    "build_condition_summary",
]
