#!/usr/bin/env python3
"""用 waifuc 清洗图片并打标，产出一份可直接炼丹的「图 + 同名 txt」数据集。

独立脚本，不是 aipipe 流水线步骤。waifuc 的三段式：Source（LocalSource 读本地目录）
→ Action（按顺序过滤/处理）→ Exporter（TextualInversionExporter 写图 + txt）。

安装（waifuc 未上 PyPI，需从 git 装）：

    python3 -m venv .venv-waifuc
    .venv-waifuc/bin/pip install -r scripts/requirements.txt

    # 边炼丹边处理、不想抢显存时：
    ONNX_MODE=cpu .venv-waifuc/bin/python scripts/clean_tag.py ...
    # 下不到 HuggingFace 模型时：
    HF_ENDPOINT=https://hf-mirror.com .venv-waifuc/bin/python scripts/clean_tag.py ...

典型用法（通用的风格 / 概念 / 角色数据集，按需加减）：

    # 默认：清洗（尺寸下限 + 相似去重）+ 打标
    python scripts/clean_tag.py ~/raw_images ~/dataset

    # 加个触发词、统一长边、去掉单色图
    python scripts/clean_tag.py ~/raw_images ~/dataset \
        --trigger-word mystyle --max-size 2048 --no-monochrome

    # 只想打标、不动图：
    python scripts/clean_tag.py ~/raw_images ~/dataset --only-tags

    # 单角色数据集才需要人脸/拆分/CCIP（风格、概念、多角色集别开）：
    python scripts/clean_tag.py ~/raw_images ~/dataset \
        --face-count 1 --split-persons --character-dir ~/ref_images

清洗链顺序（都是可选，按需开关）：
    RGB 转换（透明→白底） → 唯一文件名 → 最小尺寸 → 最大边长缩放 → LPIPS 相似去重
    → 单色图 → 插画/番剧类型 → 分级 → 人脸数 → 人物拆分 → CCIP 角色筛选
    → 打标（wd14 v3）→ tag 清理（黑名单/重叠/去下划线/自定删除）→ 触发词 → 导出

注意：
  * 默认只做尺寸下限（短边 512）和相似去重，其它过滤都要显式开；风格/概念训练别开
    --face-count / --split-persons / --character-dir / --anime-only（会丢掉多人、漫画、3D 等有用样本）。
  * CCIP 的参考图（--character-dir）只用于提取角色特征，不会写进输出；至少给 15 张。
  * 过滤动作会静默丢图，导出的图数通常小于输入数；末尾会打印统计。
  * 导出的 txt 用逗号分隔、不含置信度；同目录同 stem 的重复文件已自动加后缀。
  * 图会重新编码（waifuc 的行为），JPEG/WebP 按 --quality 保存（默认 95），PNG 无损。
  * --no-tags 且没给触发词时只输出图片（不写空 txt）；--resume 只避免重写图片，不省计算。
"""

import argparse
import posixpath
import re
import sys
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

TAGGER_CHOICES = [
    "wd14_v3_swinv2", "wd14_v3_convnext", "wd14_v3_vit",
    "wd14_swinv2", "wd14_convnext", "wd14_convnextv2", "wd14_vit", "wd14_moat",
    "deepdanbooru", "mldanbooru",
]


def _path(text):
    return Path(text).expanduser()


def build_parser():
    p = argparse.ArgumentParser(
        description="waifuc 图片清洗 + 打标（输出 图 + 同名 txt）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("清洗链顺序")[0],
    )
    p.add_argument("input_dir", type=_path, help="原始图片目录（递归）")
    p.add_argument("output_dir", type=_path, help="输出数据集目录")

    # 输出 / 命名
    g = p.add_argument_group("输出")
    g.add_argument("--trigger-word", default="", help="触发词；非空时作为第一个 tag 写进每份 txt")
    g.add_argument("--flat", action="store_true", help="扁平输出（子目录名做前缀），默认保留目录结构")
    g.add_argument("--ext", default="", help="统一输出扩展名，如 png / jpg；空=沿用原扩展名")
    g.add_argument("--quality", type=int, default=95, help="重新编码画质（JPEG/WebP，默认 95；PNG 忽略）")
    g.add_argument("--resume", action="store_true", help="输出目录已有同名图片则跳过写图（txt 仍会重写）")
    g.add_argument("--limit", type=int, default=0, help="只保留前 N 张（0=不限）")
    g.add_argument("--dry-run", action="store_true", help="只打印将要执行的清洗链，不跑模型")

    # 通用清洗
    g = p.add_argument_group("清洗")
    g.add_argument("--only-tags", action="store_true",
                   help="跳过全部清洗（尺寸/去重/分类/人脸/CCIP），只转换格式并打标")
    g.add_argument("--min-size", type=int, default=512, help="短边下限，低于则丢弃；0=不筛（默认 512）")
    g.add_argument("--max-size", type=int, default=0, help="长边上限，超出等比例缩小；0=不缩（默认）")
    g.add_argument("--dedupe", choices=["all", "group", "off"], default="all",
                   help="LPIPS 相似去重范围：all=整个数据集 / group=同一子目录 / off（默认 all）")
    g.add_argument("--dedupe-threshold", type=float, default=0.45, help="去重阈值，越小越严格（默认 0.45）")
    g.add_argument("--no-monochrome", action="store_true", help="丢弃单色/灰度/线稿图")
    g.add_argument("--anime-only", action="store_true",
                   help="只保留 illustration/bangumi（丢漫画与 3D；真人照片务必别开）")
    g.add_argument("--safe-only", action="store_true", help="只保留 safe/r15 分级")
    g.add_argument("--face-count", default="", help="保留的人脸数量，如 1 或 1,2；空=不筛")
    g.add_argument("--split-persons", action="store_true", help="多人图按人物裁成单人图（人脸检测器）")
    g.add_argument("--character-dir", type=_path, default=None,
                   help="角色参考图目录：用 CCIP 滤掉非该角色的图（至少 15 张，参考图不进输出）")

    # 打标
    g = p.add_argument_group("打标")
    g.add_argument("--no-tags", action="store_true", help="只清洗不打标（仍可写触发词）")
    g.add_argument("--tagger", choices=TAGGER_CHOICES, default="wd14_v3_swinv2", help="打标模型（默认 wd14 v3 swinv2）")
    g.add_argument("--general-threshold", type=float, default=0.35, help="general tag 阈值（默认 0.35）")
    g.add_argument("--character-threshold", type=float, default=0.85,
                   help="character tag 阈值；不想要角色 tag 就设 1.01（默认 0.85）")
    g.add_argument("--no-tag-clean", action="store_true", help="不做 tag 清理（黑名单/重叠/下划线）")
    g.add_argument("--drop-tags", default="", help="额外要删的 tag，逗号分隔，如 watermark,text")
    g.add_argument("--space-tags", action="store_true", help="把 tag 里的下划线换成空格")
    return p


def count_images(root):
    return sum(1 for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def main():
    args = build_parser().parse_args()

    inp = args.input_dir.resolve()
    out = args.output_dir.resolve()
    if not inp.is_dir():
        sys.exit(f"输入目录不存在：{inp}")
    if out == inp or inp in out.parents:
        sys.exit(f"输出目录不能放在输入目录里（重跑会把产物当原图）：{out}")
    anchor = args.character_dir.resolve() if args.character_dir else None
    if anchor is not None and not anchor.is_dir():
        sys.exit(f"参考图目录不存在：{anchor}")
    if anchor is not None and count_images(anchor) < 15:
        print(f"[warn] 参考图目录只有 {count_images(anchor)} 张，CCIP 建议至少 15 张", file=sys.stderr)

    n_input = count_images(inp)
    if n_input == 0:
        sys.exit(f"输入目录下没有图片：{inp}")

    try:
        from waifuc.action import (
            AlignMaxSizeAction, ClassFilterAction, FaceCountAction, FilterSimilarAction,
            FirstNSelectAction, MinSizeFilterAction, ModeConvertAction, NoMonochromeAction,
            PersonSplitAction, ProcessAction, FilterAction, RatingFilterAction,
            TagDropAction, TagOverlapDropAction, TagRemoveUnderlineAction, BlacklistedTagDropAction,
            TaggingAction, CCIPAction,
        )
        from waifuc.export import TextualInversionExporter
        from waifuc.model import ImageItem
        from waifuc.source import LocalSource
    except ImportError as e:  # noqa: BLE001
        sys.exit(f"未安装 waifuc 或依赖不全：{e}\n"
                 f"安装：python3 -m venv .venv-waifuc && "
                 f".venv-waifuc/bin/pip install 'git+https://github.com/deepghs/waifuc.git@main' onnxruntime")

    class RelativeFilenameAction(ProcessAction):
        """把 LocalSource 的 basename 换成相对路径，避免不同子目录的同名图在导出时互相覆盖。"""

        def __init__(self, root, flat=False):
            self.root = Path(root).resolve()
            self.flat = flat
            self._used = set()

        def _rel(self, item):
            origin = item.meta.get("path")
            if origin:
                try:
                    return Path(origin).resolve().relative_to(self.root)
                except ValueError:
                    pass
            return Path(str(item.meta.get("filename") or "image.png"))

        def process(self, item):
            rel = self._rel(item)
            if self.flat:
                prefix = "_".join(rel.parent.parts)
                prefix = re.sub(r"[^\w.-]+", "_", prefix).strip("_")
                name = f"{prefix}_{rel.name}" if prefix else rel.name
            else:
                name = rel.as_posix()

            directory, base = posixpath.split(name)
            body, ext = posixpath.splitext(base)
            final, i = body, 2
            while (directory, final) in self._used:  # 同目录同 stem（1.jpg + 1.png）会让 txt 互相覆盖
                final = f"{body}_{i}"
                i += 1
            self._used.add((directory, final))
            return ImageItem(item.image, {**item.meta, "filename": posixpath.join(directory, final + ext)})

    class MarkAnchorAction(ProcessAction):
        """给 CCIP 参考图打标记并放到独立子目录，便于随后剔除。"""

        def process(self, item):
            name = str(item.meta.get("filename") or "anchor.png")
            return ImageItem(item.image, {**item.meta, "_anchor": True, "filename": f"_anchor/{name}"})

    class DropAnchorAction(FilterAction):
        """CCIP 会把参考图一并 yield 到导出流程，这里丢掉（参考图只做特征锚点）。"""

        def check(self, item):
            return not item.meta.get("_anchor")

    class TriggerWordAction(ProcessAction):
        """触发词作为最高分 tag 写入（导出按分数降序，因此排在第一位）。"""

        def __init__(self, word, score=1.0):
            self.word = word
            self.score = score

        def process(self, item):
            tags = dict(item.meta.get("tags") or {})
            tags[self.word] = max(tags.get(self.word, 0.0), self.score)
            return ImageItem(item.image, {**item.meta, "tags": tags})

    actions = [ModeConvertAction("RGB", "white"), RelativeFilenameAction(inp, flat=args.flat)]
    cleaning = not args.only_tags
    if args.only_tags:
        leftovers = [name for name, on in (
            ("--no-monochrome", args.no_monochrome),
            ("--anime-only", args.anime_only),
            ("--safe-only", args.safe_only),
            ("--face-count", bool(args.face_count)),
            ("--split-persons", args.split_persons),
            ("--character-dir", anchor is not None),
        ) if on]
        if leftovers:
            print(f"[warn] --only-tags 会忽略：{', '.join(leftovers)}", file=sys.stderr)
    if cleaning and args.min_size > 0:
        actions.append(MinSizeFilterAction(args.min_size))
    if cleaning and args.max_size > 0:
        actions.append(AlignMaxSizeAction(args.max_size))
    if cleaning and args.dedupe != "off":
        actions.append(FilterSimilarAction(args.dedupe, threshold=args.dedupe_threshold))
    if cleaning and args.no_monochrome:
        actions.append(NoMonochromeAction())
    if cleaning and args.anime_only:
        actions.append(ClassFilterAction(["illustration", "bangumi"]))
    if cleaning and args.safe_only:
        actions.append(RatingFilterAction(["safe", "r15"]))
    if cleaning and args.face_count:
        counts = [int(x) for x in args.face_count.replace("，", ",").split(",") if x.strip()]
        actions.append(FaceCountAction(*counts))
    if cleaning and args.split_persons:
        actions.append(PersonSplitAction())
        if args.face_count:
            counts = [int(x) for x in args.face_count.replace("，", ",").split(",") if x.strip()]
            actions.append(FaceCountAction(*counts))
    if cleaning and anchor is not None:
        anchors = LocalSource(str(anchor), recursive=True).attach(
            MarkAnchorAction(), RelativeFilenameAction(anchor, flat=True))
        actions.append(CCIPAction(init_source=anchors))
        actions.append(DropAnchorAction())
    if args.limit > 0:
        actions.append(FirstNSelectAction(args.limit))
    if not args.no_tags:
        actions.append(TaggingAction(
            method=args.tagger, force=True,
            general_threshold=args.general_threshold,
            character_threshold=args.character_threshold,
        ))
        if not args.no_tag_clean:
            actions.append(BlacklistedTagDropAction())
            actions.append(TagOverlapDropAction())
            if args.space_tags:
                actions.append(TagRemoveUnderlineAction())
            if args.drop_tags:
                drop = [t.strip() for t in args.drop_tags.replace("，", ",").split(",") if t.strip()]
                actions.append(TagDropAction(drop))
    if args.trigger_word.strip():
        actions.append(TriggerWordAction(args.trigger_word.strip()))
    if args.ext:
        ext = args.ext if args.ext.startswith(".") else f".{args.ext}"
        from waifuc.action import FileExtAction
        actions.append(FileExtAction(ext))

    print(f"[plan] 输入 {n_input} 张：{inp}")
    print(f"[plan] 输出：{out}")
    if args.no_tags:
        mode = "只清洗"
    elif args.only_tags:
        mode = f"只打标 {args.tagger}"
    else:
        mode = f"清洗 + 打标 {args.tagger}"
    print(f"[plan] {mode}，触发词={args.trigger_word or '(无)'}，"
          f"CCIP={'开' if (cleaning and anchor) else '关'}，{len(actions)} 个动作")
    for i, a in enumerate(actions, 1):
        print(f"  {i:2d}. {type(a).__name__}")
    if args.dry_run:
        print("[plan] --dry-run，未执行")
        return

    out.mkdir(parents=True, exist_ok=True)
    source = LocalSource(str(inp), recursive=True).attach(*actions)
    if args.no_tags and not args.trigger_word.strip():
        # 只清洗：不生成空的 txt
        from waifuc.export import SaveExporter
        exporter = SaveExporter(str(out), no_meta=True, skip_when_image_exist=args.resume,
                                save_params={"quality": args.quality})
    else:
        exporter = TextualInversionExporter(
            str(out),
            use_spaces=False,
            include_score=False,
            score_descend=True,
            skip_when_image_exist=args.resume,
            save_params={"quality": args.quality},
        )
    source.export(exporter)

    # waifuc 写 txt 不带结尾换行，补一个，行尾统一
    for txt in out.rglob("*.txt"):
        content = txt.read_text(encoding="utf-8")
        if content and not content.endswith("\n"):
            txt.write_text(content + "\n", encoding="utf-8")

    n_img = sum(1 for p in out.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    n_txt = sum(1 for _ in out.rglob("*.txt"))
    extra = f"，{n_txt} 份标注" if n_txt else ""
    print(f"[done] 导出 {n_img} 张图{extra} → {out}（输入 {n_input} 张，差值即被清洗掉的图）")


if __name__ == "__main__":
    main()
