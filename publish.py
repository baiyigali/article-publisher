#!/usr/bin/env python3
"""
统一文章发布脚本：把指定的 Markdown 文章发布到自建站 falvshu.cn。
流程：读 md → 同名封面(.png/.jpg 均可)传 Cloudflare R2 →
      替换 md 里封面链接为 R2 地址 → POST 发布 API。

用法：
    python3 publish.py 篇1.md 篇2.md [篇3.md ...]
    给哪些 md 就发哪些，不扫目录、不维护历史去重、不记状态文件。

配置从 config.py 读（模板见 config.template.py），不在脚本里写死。
"""

import os
import re
import sys

import requests
import boto3

try:
    from config import *  # noqa: F401,F403
except ImportError:
    print("请先 cp config.template.py config.py 并填入配置")
    sys.exit(1)

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,  # noqa: F405
    aws_access_key_id=R2_ACCESS_KEY,  # noqa: F405
    aws_secret_access_key=R2_SECRET_KEY,  # noqa: F405
    region_name="auto",
)

CONTENT_TYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
COVER_ORDER = (".png", ".jpg", ".jpeg")


def find_cover(md_path):
    """按 png→jpg→jpeg 顺序找同名封面，返回 (路径, content_type) 或 (None, None)。"""
    stem = md_path.rsplit(".", 1)[0]
    for ext in COVER_ORDER:
        p = stem + ext
        if os.path.exists(p):
            return p, CONTENT_TYPE[ext]
    return None, None


def upload_cover_to_r2(path, content_type):
    key = f"{R2_KEY_PREFIX}/{os.path.basename(path)}"  # noqa: F405
    s3.upload_file(
        path, R2_BUCKET, key,  # noqa: F405
        ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=31536000"},
    )
    return f"{R2_PUBLIC_BASE}/{key}"  # noqa: F405


def publish_md(md_path):
    with open(md_path, "r", encoding="utf-8") as f:
        raw = f.read()

    title = raw.splitlines()[0].strip().lstrip("#").strip()

    cover_path, content_type = find_cover(md_path)
    r2_url = None
    if cover_path:
        print(f"  上传封面: {os.path.basename(cover_path)}")
        r2_url = upload_cover_to_r2(cover_path, content_type)
        print(f"  -> {r2_url}")

    content = raw
    if r2_url:
        # 把第一张图（本地相对/绝对路径或旧 URL，png/jpg/jpeg）替换为 R2 地址
        content = re.sub(
            r"(!\[[^\]]*\]\()[^)]*\.(?:png|jpg|jpeg)(\))",
            rf"\1{r2_url}\2",
            raw,
            count=1,
        )

    headers = {"X-Internal-Token": PUBLISH_TOKEN, "Content-Type": "application/json"}  # noqa: F405
    resp = requests.post(PUBLISH_API, headers=headers, json={"title": title, "content": content}, timeout=60)  # noqa: F405

    if resp.status_code in (200, 201):
        try:
            rid = resp.json().get("id")
        except Exception:
            rid = None
        return {"success": True, "id": rid, "title": title}
    return {"success": False, "title": title, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}


def main():
    if len(sys.argv) < 2:
        print("用法: python3 publish.py 篇1.md [篇2.md ...]")
        sys.exit(1)

    ok = 0
    for md_path in sys.argv[1:]:
        if not os.path.exists(md_path):
            print(f"  [跳过] 文件不存在: {md_path}")
            continue
        print(f"  [发布] {os.path.basename(md_path)}")
        result = publish_md(md_path)
        if result["success"]:
            print(f"    OK id={result['id']}")
            ok += 1
        else:
            print(f"    FAIL {result['message']}")
    print(f"\n完成，本次发布 {ok}/{len(sys.argv) - 1} 篇")


if __name__ == "__main__":
    main()
