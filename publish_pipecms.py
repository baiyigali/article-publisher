#!/usr/bin/env python3
"""
Pipe CMS 文章发布脚本。

配置文件 config.json 示例（站点级配置）：
{
  "api": "https://pipecms.com/articles/api/create/",
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
    python3 publish_pipecms.py config.json --author 程序员白大力 篇1.md 篇2.md [篇3.md ...]
"""

import json
import os
import re
import sys

import requests

CONTENT_TYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
COVER_ORDER = (".png", ".jpg", ".jpeg")


def extract_tags(raw):
    last_line = raw.strip().splitlines()[-1] if raw.strip() else ""
    return re.findall(r"#(\S+)", last_line)


def find_cover(md_path):
    stem = md_path.rsplit(".", 1)[0]
    for ext in COVER_ORDER:
        p = stem + ext
        if os.path.exists(p):
            return p, CONTENT_TYPE[ext]
    return None, None


def upload_cover(path, content_type, r2):
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


def publish_md(md_path, cfg, author):
    with open(md_path, "r", encoding="utf-8") as f:
        raw = f.read()

    title = raw.splitlines()[0].strip().lstrip("#").strip()
    content = raw
    r2 = cfg.get("r2")

    cover_path, content_type = find_cover(md_path)
    if cover_path:
        if not r2:
            return {"success": False, "title": title,
                    "message": f"有封面 {os.path.basename(cover_path)} 但配置文件没写 r2"}
        print(f"  上传封面: {os.path.basename(cover_path)}")
        r2_url = upload_cover(cover_path, content_type, r2)
        print(f"  -> {r2_url}")
        content = re.sub(
            r"(!\[[^\]]*\]\()[^)]*\.(?:png|jpg|jpeg)(\))",
            rf"\1{r2_url}\2",
            raw,
            count=1,
        )

    # 提取标签并批量获取 ID
    tag_names = extract_tags(raw)
    tag_ids = []
    if tag_names:
        bulk_url = cfg["api"].rsplit("/create/", 1)[0] + "/tags/bulk/"
        tag_resp = requests.post(
            bulk_url,
            headers={"X-Internal-Token": cfg["token"], "Content-Type": "application/json"},
            json={"names": tag_names}, timeout=30,
        )
        if tag_resp.status_code == 200:
            tag_ids = [t["id"] for t in tag_resp.json()]
            print(f"  标签: {tag_names} -> IDs {tag_ids}")
        else:
            print(f"  标签获取失败: {tag_resp.status_code} {tag_resp.text[:100]}")

    payload = {"title": title, "content": content, "author": author}
    if tag_ids:
        payload["tags"] = tag_ids
    headers = {"X-Internal-Token": cfg["token"], "Content-Type": "application/json"}
    resp = requests.post(cfg["api"], headers=headers, json=payload, timeout=60)

    if resp.status_code in (200, 201):
        rid = resp.json().get("id")
        return {"success": True, "id": rid, "title": title}
    return {"success": False, "title": title, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}


def main():
    if len(sys.argv) < 4 or sys.argv[2] != "--author":
        print(__doc__)
        sys.exit(1)

    config_path = sys.argv[1]
    author = sys.argv[3]
    files = sys.argv[4:]

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    ok = 0
    for md_path in files:
        if not os.path.exists(md_path):
            print(f"  [跳过] 文件不存在: {md_path}")
            continue
        if "prompts" in os.path.basename(md_path).lower():
            print(f"  [跳过] 提示词文件: {os.path.basename(md_path)}")
            continue
        if "prompts" in os.path.basename(md_path).lower():
            print(f"  [跳过] 提示词文件: {os.path.basename(md_path)}")
            continue
        print(f"  [发布] {os.path.basename(md_path)}")
        result = publish_md(md_path, cfg, author)
        if result["success"]:
            print(f"    OK id={result['id']}  {result['title']}")
            ok += 1
        else:
            print(f"    FAIL {result['message']}")

    print(f"\n完成，本次发布 {ok}/{len(files)} 篇")


if __name__ == "__main__":
    main()
