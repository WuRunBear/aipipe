"""步骤 1/3：读取参数，写第一个产物。"""
import os
from pathlib import Path

greeting = os.environ.get("PIPE_PARAM_GREETING", "world")
work = Path("/work")
(work / "hello.txt").write_text(f"hello, {greeting}\n", encoding="utf-8")
print(f"[01] wrote hello.txt with greeting={greeting}")
