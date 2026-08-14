"""步骤 2/3：消费上一产物，生成新产物。"""
from pathlib import Path

work = Path("/work")
content = (work / "hello.txt").read_text(encoding="utf-8").strip()
(work / "count.txt").write_text(f"len={len(content)}\n", encoding="utf-8")
print(f"[02] read hello.txt ({content!r}), wrote count.txt")
