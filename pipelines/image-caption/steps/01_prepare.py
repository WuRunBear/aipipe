"""步骤 1/3：收集 /input/images 图片 → /work/dataset/images/ + index.json。"""
import json
from pathlib import Path

from PIL import Image

work = Path("/work")
input_root = Path("/input/images")
images_out = work / "dataset" / "images"

EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

entries = []
for p in sorted(input_root.rglob("*")):
    if not p.is_file() or p.suffix.lower() not in EXT:
        continue
    rel = p.relative_to(input_root).as_posix()
    dst = images_out / rel
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
print(f"[01] 共 {len(entries)} 张图片 → dataset/images/，index.json 已写")
