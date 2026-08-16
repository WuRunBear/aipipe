"""步骤 1/3：按开关把 /input/images 图片拷入 /work/dataset/{natural,tags}/（图+标注同目录）+ index.json。"""
import json
import os
from pathlib import Path

from PIL import Image

work = Path("/work")
input_root = Path("/input/images")
dataset = work / "dataset"

do_natural = os.environ.get("PIPE_PARAM_NATURAL", "false").strip().lower() in ("1", "true", "yes")
do_tags = os.environ.get("PIPE_PARAM_TAGS", "false").strip().lower() in ("1", "true", "yes")
styles = [s for s, on in (("natural", do_natural), ("tags", do_tags)) if on]

EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

entries = []
stems: set[tuple[str, str]] = set()
for p in sorted(input_root.rglob("*")):
    if not p.is_file() or p.suffix.lower() not in EXT:
        continue
    rel = p.relative_to(input_root).as_posix()
    key = (str(Path(rel).parent), Path(rel).stem)
    if key in stems:
        raise SystemExit(f"去后缀后重名的图片（标注 txt 会互相覆盖）：{rel}")
    stems.add(key)
    for style in styles:
        dst = dataset / style / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(p.read_bytes())
    try:
        with Image.open(p) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001  损坏/异常图只记尺寸 0，不中断
        w, h = 0, 0
    entries.append({"rel": rel, "w": w, "h": h, "size": p.stat().st_size})
    print(f"[01] + {rel} ({w}x{h})")

if not entries:
    raise SystemExit("未在 /input/images 下找到图片（支持 jpg/jpeg/png/webp/bmp）")

(work / "index.json").write_text(
    json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"[01] 共 {len(entries)} 张图片 → dataset/{styles or '（未启用风格）'}/，index.json 已写")
