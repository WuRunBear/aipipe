"""步骤 3/3：核对标注完整性 → report.txt（存在缺失则非零退出，提示 rerun --from 2 补缺）。

每个风格目录（dataset/natural、dataset/tags）内图片与 <stem>.txt 配对。
"""
import json
import os
from pathlib import Path

work = Path("/work")
dataset = work / "dataset"

do_natural = os.environ.get("PIPE_PARAM_NATURAL", "false").strip().lower() in ("1", "true", "yes")
do_tags = os.environ.get("PIPE_PARAM_TAGS", "false").strip().lower() in ("1", "true", "yes")

index = json.loads((work / "index.json").read_text(encoding="utf-8"))


def ok(style: str, rel: str) -> bool:
    p = dataset / style / (os.path.splitext(rel)[0] + ".txt")
    return p.is_file() and p.stat().st_size > 0


lines = [f"图片总数: {len(index)}"]
missing_total = 0
for style, enabled in (("natural", do_natural), ("tags", do_tags)):
    if not enabled:
        lines.append(f"{style}: 未启用")
        continue
    missing = [e["rel"] for e in index if not ok(style, e["rel"])]
    lines.append(f"{style}: {len(index) - len(missing)}/{len(index)} 完成，缺 {len(missing)}")
    missing_total += len(missing)
    for rel in missing:
        lines.append(f"  缺: {rel}")

report = "\n".join(lines)
(work / "report.txt").write_text(report + "\n", encoding="utf-8")
print(f"[03]\n{report}")
if missing_total:
    raise SystemExit(f"有 {missing_total} 张标注缺失，执行 rerun --from 2 补缺")
