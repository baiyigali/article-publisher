#!/usr/bin/env python3
"""
法律热点文章自动发布脚本（含 R2 图床上传）。
与 publish_with_r2.py 同源，但独立目录、独立状态文件、封面认 .png，
不与"政策解读"那路混用。

流程：扫 ARTICLES_DIR 下未发布的 md → 同名 png 封面传 R2 →
      把 md 里本地封面链接替换为 R2 URL → POST 到 falvshu.cn 发布 API → 记状态。
"""

import os
import re
import json
import requests
import boto3
from datetime import datetime

# ============ 配置 ============
ARTICLES_DIR = "/Users/baiyigali/workspace/articles/法律热点"
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legal_status.json")
PUBLISH_API = "http://falvshu.cn/articles/api/create/"
PUBLISH_TOKEN = "my-internal-secret-2026-article-api-123456"

# R2 配置（与 publish_with_r2.py 一致）
R2_ENDPOINT = "https://4176b137e6755c9ed16bf96cc322479d.r2.cloudflarestorage.com"
R2_ACCESS_KEY = "8745891eb480e7c6e8e2124cf19d1e50"
R2_SECRET_KEY = "331d1f8181c717f85bbd71f0842d64f2c6cc2ffb2d6a6ac3ea8d2b5e8136fb06"
R2_BUCKET = "falvshucn"
R2_PUBLIC_BASE = "https://pub-4e23808c40144f24b5749f0833e7339e.r2.dev"
R2_KEY_PREFIX = "covers"

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto",
)


def upload_cover_to_r2(png_path):
    filename = os.path.basename(png_path)
    key = f"{R2_KEY_PREFIX}/{filename}"
    s3.upload_file(
        png_path,
        R2_BUCKET,
        key,
        ExtraArgs={"ContentType": "image/png", "CacheControl": "public, max-age=31536000"},
    )
    return f"{R2_PUBLIC_BASE}/{key}"


def load_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_status(data):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def publish_md(md_path):
    with open(md_path, "r", encoding="utf-8") as f:
        raw = f.read()

    lines = raw.splitlines()
    title = lines[0].strip().lstrip("#").strip()

    png_path = md_path.rsplit(".", 1)[0] + ".png"
    if not os.path.exists(png_path):
        print(f"  [警告] 找不到封面 {png_path}，仍发布但无封面")
        r2_url = None
    else:
        print(f"  上传封面到 R2: {os.path.basename(png_path)}")
        r2_url = upload_cover_to_r2(png_path)
        print(f"  -> {r2_url}")

    # 把 md 里第一张图（本地相对/绝对路径或旧 URL）替换成 R2 地址
    if r2_url:
        content = re.sub(
            r"(!\[[^\]]*\]\()[^)]*\.png(\))",
            rf"\1{r2_url}\2",
            raw,
            count=1,
        )
    else:
        content = raw

    headers = {"X-Internal-Token": PUBLISH_TOKEN, "Content-Type": "application/json"}
    payload = {"title": title, "content": content}
    resp = requests.post(PUBLISH_API, headers=headers, json=payload, timeout=60)

    if resp.status_code in (200, 201):
        try:
            result = resp.json()
            return {"success": True, "id": result.get("id"), "title": title}
        except Exception:
            return {"success": True, "id": None, "title": title}
    return {"success": False, "title": title, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}


def main():
    import sys
    # 用法：
    #   python3 publish_legal.py a.md b.md   → 只发布指定的 md（本轮新生成的两篇）
    #   python3 publish_legal.py             → 不带参数则扫整目录、按 legal_status.json 去重（一般不用）
    if len(sys.argv) > 1:
        targets = [os.path.abspath(p) for p in sys.argv[1:]]
        new_count = 0
        for md_path in targets:
            if not os.path.exists(md_path):
                print(f"  [跳过] 文件不存在: {md_path}")
                continue
            print(f"  [发布] {os.path.basename(md_path)}")
            result = publish_md(md_path)
            if result["success"]:
                print(f"    OK id={result['id']}")
                new_count += 1
            else:
                print(f"    FAIL {result['message']}")
        print(f"\n完成，本次新发布 {new_count} 篇")
        return

    status = load_status()
    md_files = sorted(f for f in os.listdir(ARTICLES_DIR) if f.endswith(".md"))
    print(f"扫描到 {len(md_files)} 篇 md")

    new_count = 0
    for fn in md_files:
        md_path = os.path.join(ARTICLES_DIR, fn)
        if status.get(fn, {}).get("status") == "published":
            print(f"  [已发布] {fn}")
            continue
        print(f"  [发布] {fn}")
        result = publish_md(md_path)
        if result["success"]:
            status[fn] = {
                "status": "published",
                "article_path": md_path,
                "article_id": result["id"],
                "article_title": result["title"],
                "published_at": datetime.now().isoformat(),
            }
            save_status(status)
            print(f"    OK id={result['id']}")
            new_count += 1
        else:
            print(f"    FAIL {result['message']}")
    print(f"\n完成，本次新发布 {new_count} 篇")


if __name__ == "__main__":
    main()
