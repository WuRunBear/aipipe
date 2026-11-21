"""步骤 8/10：抓取热门评论并翻译 → /work/comments.json。

用 yt-dlp 的评论 API（与下载同源，沙箱内可复跑），按点赞取前 N 条一级评论，
用翻译模型转成中文（可选领导人姓名泛化），供 08 渲染片尾卡片 + TTS。

任何一步失败都写空列表并退出 0：评论是正片之后的附加内容，不应让
已经完成的主视频失败。
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

work = Path("/work")
url = os.environ.get("PIPE_PARAM_VIDEO_URL", "").strip()
count = int(os.environ.get("PIPE_PARAM_COMMENTS_COUNT", "6") or 0)
target_lang = os.environ.get("PIPE_PARAM_TARGET_LANG", "zh")
neutralize = os.environ.get("PIPE_PARAM_NEUTRALIZE_LEADERS", "").strip().lower() in (
    "1", "true", "yes",
)


def write_empty(reason: str) -> None:
    print(f"[07] 跳过评论片尾：{reason}")
    (work / "comments.json").write_text(
        json.dumps({"total": 0, "comments": []}, ensure_ascii=False), encoding="utf-8"
    )


if count <= 0 or not url:
    write_empty("comments_count=0 或缺少 video_url")
    raise SystemExit(0)

try:
    import yt_dlp
except Exception as e:  # noqa: BLE001
    write_empty(f"yt-dlp 不可用（{e}）")
    raise SystemExit(0)

opts = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "getcomments": True,
    "extractor_args": {
        "youtube": {
            "comment_sort": ["top"],
            "max_comments": [str(max(count * 6, 60)), "all", "0"],
        }
    },
}

# cookies 复制到 /work：yt-dlp 退出可能写回，只读挂载会崩（同 01）
cookies = os.environ.get("YOUTUBE_COOKIES_FILE")
if cookies:
    local = work / "cookies_comments.txt"
    try:
        shutil.copy2(cookies, local)
        opts["cookiefile"] = str(local)
    except Exception as e:  # noqa: BLE001
        print(f"[07] cookies 复制失败（{e}），继续无 cookies")

try:
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
except Exception as e:  # noqa: BLE001
    write_empty(f"评论抓取失败（{e}）")
    raise SystemExit(0)

total = info.get("comment_count") or 0
raw = [
    c for c in (info.get("comments") or [])
    if c.get("text") and (not c.get("parent") or c.get("parent") == "root")
]
raw.sort(key=lambda c: c.get("like_count") or 0, reverse=True)
raw = raw[:count]
if not raw:
    write_empty("没有可用评论")
    raise SystemExit(0)

print(f"[07] 抓取到 {len(info.get('comments') or [])} 条，取点赞前 {len(raw)} 条")

from openai import OpenAI  # noqa: E402

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)
model = (
    os.environ.get("PIPE_PARAM_TRANSLATE_MODEL")
    or os.environ.get("TRANSLATE_MODEL", "deepseek/deepseek-chat")
)

system = "你是字幕翻译。只输出要求的行。"
clean_texts = [re.sub(r"\s+", " ", c["text"]).strip() for c in raw]
lines = [f"{i + 1}. {t}" for i, t in enumerate(clean_texts)]
prompt = (
    f"把下面 {len(raw)} 条 YouTube 评论翻译成{target_lang}，口语自然，保留原语气和立场。\n"
    "每条单独一行，格式：编号. 译文；不要解释、不要引号。\n"
    + ("领导人姓名用国家或政府名代替（Trump→美国、Putin→俄罗斯、Xi→中国），不要输出人名。\n"
       if neutralize else "")
    + "\n" + "\n".join(lines)
)

translations: list[str] = []
for attempt in range(1, 3):
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
        )
        out = (resp.choices[0].message.content or "").strip()
        parsed = {}
        for line in out.splitlines():
            m = re.match(r"^(\d+)[.)、]\s*(.*)$", line.strip())
            if m:
                parsed[int(m.group(1))] = m.group(2).strip()
        translations = [parsed[i + 1] for i in range(len(raw)) if i + 1 in parsed]
        if len(translations) == len(raw) and all(translations):
            break
        raise ValueError(f"条数不匹配 {len(translations)}/{len(raw)}")
    except Exception as e:  # noqa: BLE001
        print(f"[07] 评论翻译重试 {attempt}/2: {e}")
        translations = []
        time.sleep(2)

if len(translations) != len(raw):
    write_empty("评论翻译失败")
    raise SystemExit(0)

comments = [{
    "author": c.get("author") or "观众",
    "likes": c.get("like_count") or 0,
    "text": re.sub(r"\s+", " ", c["text"]).strip(),
    "translated": t,
} for c, t in zip(raw, translations)]

(work / "comments.json").write_text(
    json.dumps({"total": total, "comments": comments}, ensure_ascii=False),
    encoding="utf-8",
)
for c in comments:
    print(f"[07] {c['author']}（赞 {c['likes']}）→ {c['translated'][:50]}")
print(f"[07] comments.json: {len(comments)} 条（总评论 {total}）")
