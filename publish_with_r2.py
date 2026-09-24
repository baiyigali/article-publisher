#!/usr/bin/env python3
"""
文章自动发布脚本（含 R2 图床上传）
流程：读 md → 上传同名 jpg 到 R2 → 替换 md 里的图片链接 → 调用发布 API
"""

import os
import re
import json
import requests
import boto3
from datetime import datetime

# 从配置文件读取
try:
    from config import *
except ImportError:
    print("请先复制 config.template.py 为 config.py 并填入配置")
    exit(1)

STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "articles_status.json")

# ============ 初始化 R2 client ============
s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto",
)


def upload_cover_to_r2(jpg_path):
    """上传封面图到 R2，返回公开 URL"""
    filename = os.path.basename(jpg_path)
    key = f"{R2_KEY_PREFIX}/{filename}"
    s3.upload_file(
        jpg_path,
        R2_BUCKET,
        key,
        ExtraArgs={
            "ContentType": "image/jpeg",
            "CacheControl": "public, max-age=31536000",
        },
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


def extract_policy_url(content):
    """从文章末尾提取政策原文 URL"""
    m = re.search(r"政策原文[：:]\s*\[.*?\]\((.*?)\)", content)
    return m.group(1) if m else None


def publish_md(md_path):
    """发布单篇 md：上传封面 → 替换链接 → 调 API"""
    with open(md_path, "r", encoding="utf-8") as f:
        raw = f.read()

    lines = raw.splitlines()
    title = lines[0].strip().lstrip("#").strip()

    # 找同名 jpg
    jpg_path = md_path.rsplit(".", 1)[0] + ".jpg"
    if not os.path.exists(jpg_path):
        print(f"  [警告] 找不到封面图 {jpg_path}，跳过上传")
        r2_url = None
    else:
        print(f"  上传封面到 R2: {os.path.basename(jpg_path)}")
        r2_url = upload_cover_to_r2(jpg_path)
        print(f"  → {r2_url}")

    # 替换 md 里的图片链接（第一行 ![xxx](旧url) → ![xxx](r2_url)）
    # 同时处理http开头的URL和本地相对路径
    if r2_url:
        content = re.sub(
            r"(!\[[^\]]*\]\()[^)]*\.jpg(\))",
            rf"\1{r2_url}\2",
            raw,
            count=1,
        )
    else:
        content = raw

    # 调发布 API
    headers = {
        "X-Internal-Token": PUBLISH_TOKEN,
        "Content-Type": "application/json",
    }
    payload = {"title": title, "content": content}
    resp = requests.post(PUBLISH_API, headers=headers, json=payload, timeout=60)

    if resp.status_code == 201:
        result = resp.json()
        return {"success": True, "id": result.get("id"), "title": title}
    else:
        return {
            "success": False,
            "title": title,
            "message": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }


def main():
    status = load_status()
    md_files = sorted(
        f for f in os.listdir(ARTICLES_DIR) if f.endswith(".md")
    )
    print(f"扫描到 {len(md_files)} 篇 md")

    new_count = 0
    for fn in md_files:
        md_path = os.path.join(ARTICLES_DIR, fn)
        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        policy_url = extract_policy_url(content)

        # 按文件名去重
        file_key = fn
        if file_key in status and status[file_key].get("status") == "published":
            print(f"  [已发布] {fn}")
            continue

        print(f"  [发布] {fn}")
        result = publish_md(md_path)

        if result["success"]:
            status[file_key] = {
                "status": "published",
                "policy_url": policy_url,
                "article_path": md_path,
                "article_id": result["id"],
                "article_title": result["title"],
                "published_at": datetime.now().isoformat(),
            }
            save_status(status)
            print(f"    ✅ ID: {result['id']}")
            new_count += 1
        else:
            print(f"    ❌ {result['message']}")

    print(f"\n完成。本次新发布 {new_count} 篇")


if __name__ == "__main__":
    main()
