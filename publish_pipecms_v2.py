#!/usr/bin/env python3
"""
Pipe CMS v2 文章发布脚本 —— 一次调用灌入全部结构化字段。

与 v1（publish_pipecms.py）的差异：
- 走 v2 接口 POST /articles/api/v2/create/，tag_names 直传（服务端 get_or_create），
  省掉 v1 里「先 bulk 拿 ID 再 create」的两次往返
- 支持结构化元数据：summary / cover_image / creation_type / status
- 封面图不再替换进 md 正文，而是填 cover_image 字段（新数据模型）
- 兼容旧 md：没有 front matter 时按 v1 约定取（第一行 # 标题、末尾 #标签行）

md 支持的 front matter（可选，放在文件最开头，--- 分隔）：

    ---
    title: 文章标题            # 缺省则取正文第一行 # 标题
    summary: 一百字以内的摘要    # 缺省则自动截取正文第一段
    creation_type: ai_only     # ai_assisted / ai_only / human_only，缺省取 --creation-type
    status: published          # draft / published，缺省取 --status
    cover_image: https://…     # 已有图床 URL 直接填；缺省找同名本地封面图传 R2
    tags: [AI, CMS]            # 或 tags: AI, CMS；缺省提取末尾 #标签行
    ---

配置 config.json 示例：

    {
      "api": "https://pipecms.com/articles/api/v2/create/",
      "token": "你的内部API token",
      "r2": {
        "endpoint": "https://xxx.r2.cloudflarestorage.com",
        "access_key": "xxx",
        "secret_key": "xxx",
        "bucket": "xxx",
        "public_base": "https://pub-xxx.r2.dev",
        "key_prefix": "covers"
      }
    }

用法：
    python3 publish_pipecms_v2.py config.json --author 程序员白大力 篇1.md 篇2.md
    python3 publish_pipecms_v2.py config.json --author 程序员白大力 \
        --creation-type ai_only --status published 篇.md
"""

import argparse
import json
import os
import re
import sys

import requests

CONTENT_TYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
COVER_ORDER = (".png", ".jpg", ".jpeg")
SUMMARY_LIMIT = 100

CREATION_TYPE_CHOICES = ("ai_assisted", "ai_only", "human_only")
STATUS_CHOICES = ("draft", "published")


def parse_front_matter(raw):
    """解析 md 开头的 --- front matter 块。

    返回 (meta_dict, body)；没有 front matter 时返回 ({}, raw)。
    meta 支持：title/summary/creation_type/status/cover_image/tags（数组或逗号串）
    """
    if not raw.lstrip().startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, raw

    meta = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [x.strip().strip("\"'") for x in value[1:-1].split(",") if x.strip()]
        else:
            meta[key] = value.strip("\"'")

    body = "\n".join(lines[end + 1:])
    return meta, body


def first_h1(body):
    """取正文第一个非空行作为标题（须以 # 开头）；不是标题就返回空。"""
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            return line[2:].strip()
        return ""
    return ""


def extract_tags_from_body(body):
    """从正文末尾非空行提取 #标签。"""
    lines = [l.strip() for l in body.strip().splitlines()]
    for line in reversed(lines):
        if not line:
            continue
        return re.findall(r"#(\S+)", line)
    return []


def make_summary(body, limit=SUMMARY_LIMIT):
    """自动摘要：跳过第一行标题，取第一个非空段落去掉 markdown 标记后的前 limit 字。"""
    lines = body.strip().splitlines()
    start = 0
    if lines and lines[0].lstrip().startswith("#"):
        start = 1
    for line in lines[start:]:
        line = line.strip()
        if not line:
            continue
        text = re.sub(r"[#>*`_\[\]()!\-]", "", line).strip()
        if text:
            return text[:limit]
    return ""


def find_cover(md_path):
    """按 png→jpg→jpeg 顺序找同名封面，返回 (路径, content_type) 或 (None, None)。"""
    stem = md_path.rsplit(".", 1)[0]
    for ext in COVER_ORDER:
        p = stem + ext
        if os.path.exists(p):
            return p, CONTENT_TYPE[ext]
    return None, None


def upload_cover(path, content_type, r2):
    """上传封面到 R2，返回公开 URL。"""
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=r2["endpoint"],
        aws_access_key_id=r2["access_key"],
        aws_secret_access_key=r2["secret_key"],
        region_name="auto",
    )
    key = f"{r2['key_prefix']}/{os.path.basename(path)}"
    client.upload_file(
        path, r2["bucket"], key,
        ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=31536000"},
    )
    return f"{r2['public_base'].rstrip('/')}/{key}"


def strip_cover_line(body, cover_basename):
    """去掉正文里引用封面文件的图片行。

    老 md 的封面嵌在正文（![封面](同名图.png)），新数据模型封面走 cover_image 字段，
    封面被提取后把正文里对应图片行剥掉，避免页面出现 broken image。
    只处理引用封面文件名的那一行，正文其他图片不受影响。
    """
    if not cover_basename:
        return body
    return re.sub(
        rf"!\[[^\]]*\]\([^)]*{re.escape(cover_basename)}[^)]*\)[ \t]*\n?",
        "", body, count=1,
    )


def strip_title_line(body):
    """去掉正文第一行 # 标题（标题已进 title 字段，正文里不再重复渲染）。"""
    lines = body.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        return "\n".join(lines[1:]).lstrip("\n")
    return body


def is_tags_line(line):
    """判断一行是否为 #标签 行。

    覆盖：纯 #标签 行、末尾标签带空格（#AI Agent 这种双词话题标签）、
    "**话题标签**：#xx" 前缀行。
    判定：去掉可选标签前缀后，行以 # 开头，且以 # 开头的 token 占多数
    （双词标签的第二部分允许不以 # 开头）。
    """
    stripped = line.strip()
    if "#" not in stripped:
        return False
    body = re.sub(r"^\*{1,2}[^*]*\*{1,2}\s*[:：]?\s*", "", stripped)
    toks = body.split()
    if not toks:
        return False
    tag_ratio = sum(1 for t in toks if t.startswith("#")) / len(toks)
    return toks[0].startswith("#") and tag_ratio > 0.5


def strip_tags_line(body):
    """去掉正文末尾的 #标签行（标签已进 tag_names 字段）。

    规则与前端 strip_article_chrome 一致：从末尾跳过空行，最后一个非空行
    是 #标签 行则去掉；标签行上方常有的分隔线（---/***/___）一并剥掉。
    """
    lines = body.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and is_tags_line(lines[-1].strip()):
        lines.pop()
        # 标签行上方的分隔线（---/***/___）剥掉，避免正文末尾留孤线
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and re.fullmatch(r"[-*_]{3,}", lines[-1].strip()):
            lines.pop()
    return "\n".join(lines)


def build_payload(md_path, cfg, author, creation_type="ai_assisted", status="published"):
    """读 md 并组装 v2 接口 payload。返回 (payload, cover_log, errors)。

    cover_log：封面处理的人类可读说明（'' 表示无封面）；
    errors：非空表示有致命问题（如本地有封面但没配 r2），payload 不可用。
    """
    with open(md_path, "r", encoding="utf-8") as f:
        raw = f.read()

    meta, body = parse_front_matter(raw)

    title = meta.get("title") or first_h1(body)
    if not title:
        return None, "", "md 里找不到标题（front matter title 或第一行 # 标题）"
    # 标题已进 title 字段，正文第一行 # 标题剥掉（无论 front matter 是否给了 title）
    body = strip_title_line(body)

    # 封面：front matter cover_image URL 优先；否则同名本地图上传 R2。
    # 必须在摘要/标签提取之前处理——老 md 封面行嵌在正文，会污染摘要提取
    cover_image = ""
    cover_log = ""
    if meta.get("cover_image"):
        cover_image = meta["cover_image"]
        cover_log = f"使用 front matter 封面: {cover_image}"
    else:
        cover_path, content_type = find_cover(md_path)
        if cover_path:
            r2 = cfg.get("r2")
            if not r2:
                return None, "", (
                    f"有封面 {os.path.basename(cover_path)} 但配置文件没写 r2"
                    "（要么补 r2 配置，要么在 front matter 里写 cover_image URL）"
                )
            cover_image = upload_cover(cover_path, content_type, r2)
            cover_log = f"上传封面 -> {cover_image}"
            # 老 md 封面嵌在正文，封面已进 cover_image 字段，剥掉正文里的封面行
            body = strip_cover_line(body, os.path.basename(cover_path))

    # 标签：front matter tags 优先，否则从正文末尾提取；
    # 无论来源，标签已进 tag_names 字段，正文末尾的 #标签行剥掉
    tag_names = []
    if meta.get("tags"):
        if isinstance(meta["tags"], list):
            tag_names = meta["tags"]
        else:
            tag_names = [t.strip() for t in meta["tags"].split(",") if t.strip()]
    else:
        tag_names = extract_tags_from_body(body)
    body = strip_tags_line(body)

    # 摘要：front matter 优先，否则从正文（已剥标题/封面/标签）提取
    summary = meta.get("summary") or make_summary(body)

    ct = meta.get("creation_type", creation_type)
    st = meta.get("status", status)

    payload = {
        "title": title,
        "content": body,
        "author": author,
        "summary": summary,
        "cover_image": cover_image,
        "creation_type": ct,
        "status": st,
    }
    if tag_names:
        payload["tag_names"] = tag_names

    return payload, cover_log, ""


def publish_md(md_path, cfg, author, creation_type="ai_assisted", status="published"):
    """发布单篇 md 到 v2 接口。返回 dict：success/id/title/message。"""
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        return {"success": False, "title": os.path.basename(md_path), "message": str(e)}

    meta, body = parse_front_matter(raw)
    title = meta.get("title") or first_h1(body)

    payload, cover_log, error = build_payload(md_path, cfg, author, creation_type, status)
    if error:
        return {"success": False, "title": title or os.path.basename(md_path), "message": error}
    if cover_log:
        print(f"    {cover_log}")

    if payload.get("tag_names"):
        print(f"    标签: {payload['tag_names']}")

    headers = {"X-Internal-Token": cfg["token"], "Content-Type": "application/json"}
    try:
        resp = requests.post(cfg["api"], headers=headers, json=payload, timeout=60)
    except requests.RequestException as e:
        return {"success": False, "title": title or os.path.basename(md_path), "message": f"请求失败: {e}"}

    if resp.status_code in (200, 201):
        rid = resp.json().get("id")
        return {"success": True, "id": rid, "title": title}
    return {"success": False, "title": title or os.path.basename(md_path),
            "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}


def main():
    parser = argparse.ArgumentParser(
        description="Pipe CMS v2 发布脚本：一次调用灌入全部结构化字段",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("config", help="配置文件路径（json，含 api/token，可选 r2）")
    parser.add_argument("--author", required=True, help="作者名（与文章强绑定，不放全局配置）")
    parser.add_argument("--creation-type", default="ai_assisted", choices=CREATION_TYPE_CHOICES,
                        help="创作类型（front matter 里可逐篇覆盖），默认 ai_assisted")
    parser.add_argument("--status", default="published", choices=STATUS_CHOICES,
                        help="发布状态（front matter 里可逐篇覆盖），默认 published")
    parser.add_argument("files", nargs="+", metavar="FILE.md", help="要发布的 md 文件")

    args = parser.parse_args()

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[致命] 读取配置失败: {e}")
        sys.exit(1)

    if not cfg.get("api") or not cfg.get("token"):
        print("[致命] 配置缺少 api 或 token")
        sys.exit(1)

    ok = 0
    for md_path in args.files:
        if not os.path.exists(md_path):
            print(f"  [跳过] 文件不存在: {md_path}")
            continue
        if "prompts" in os.path.basename(md_path).lower():
            print(f"  [跳过] 提示词文件: {os.path.basename(md_path)}")
            continue
        print(f"  [发布] {os.path.basename(md_path)}")
        result = publish_md(md_path, cfg, args.author, args.creation_type, args.status)
        if result["success"]:
            print(f"    OK id={result['id']}  {result['title']}")
            ok += 1
        else:
            print(f"    FAIL {result['message']}")

    print(f"\n完成，本次发布 {ok}/{len(args.files)} 篇")


if __name__ == "__main__":
    main()
