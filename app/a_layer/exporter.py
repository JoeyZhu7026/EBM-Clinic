"""
A 层 - 导出器
将 workspace 中的 rules.json 和 variable_dictionary.json 复制到 output/
"""

import shutil
from pathlib import Path


def export_workspace(workspace_dir: Path, output_dir: Path) -> list[Path]:
    """
    导出当前工作台状态到 output 目录

    Returns:
        导出的文件路径列表
    """
    workspace_dir = Path(workspace_dir)
    output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    rules_src = workspace_dir / "rules.json"
    dict_src = workspace_dir / "variable_dictionary.json"

    if not rules_src.exists():
        raise FileNotFoundError(f"找不到规则文件: {rules_src}")
    if not dict_src.exists():
        raise FileNotFoundError(f"找不到词典文件: {dict_src}")

    exported = []

    rules_dst = output_dir / "rules.json"
    shutil.copy2(rules_src, rules_dst)
    exported.append(rules_dst)

    dict_dst = output_dir / "variable_dictionary.json"
    shutil.copy2(dict_src, dict_dst)
    exported.append(dict_dst)

    return exported


def clear_outputs(output_dir: Path) -> None:
    """清空 output 目录中的导出文件"""
    output_dir = Path(output_dir)
    for name in ("rules.json", "variable_dictionary.json"):
        p = output_dir / name
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    import sys
    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace")
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output")
    exported = export_workspace(workspace, output)
    for p in exported:
        print(f"已导出: {p}")
