"""步骤 3/3：汇总产物并打印最终结果。"""
import json
from pathlib import Path

work = Path("/work")
hello = (work / "hello.txt").read_text(encoding="utf-8").strip()
count = (work / "count.txt").read_text(encoding="utf-8").strip()
result = {"hello": hello, "count": count}
(work / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
print(f"[03] final: {result}")
