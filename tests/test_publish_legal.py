"""
publish_legal.py 的单元测试。
约定：本文件只加不删不改——新增行为补新用例，不改旧用例；网络与 R2 一律 mock，绝不真发。
"""
import os
import sys
import json

import pytest

# 让测试能 import 到项目根的脚本
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import publish_legal  # noqa: E402


# ---------- 标题提取 ----------

def test_title_is_h1_without_hash(tmp_path):
    md = tmp_path / "某事件.md"
    md.write_text("# 这是文章标题\n\n正文……\n", encoding="utf-8")
    raw = md.read_text(encoding="utf-8")
    title = raw.splitlines()[0].strip().lstrip("#").strip()
    assert title == "这是文章标题"


# ---------- 封面 png 路径推导 ----------

def test_cover_png_path_is_same_stem(tmp_path):
    md = tmp_path / "某事件.md"
    png = str(md).rsplit(".", 1)[0] + ".png"
    assert png.endswith("某事件.png")
    assert os.path.basename(png) == "某事件.png"


# ---------- 封面链接替换 ----------

def test_replaces_local_cover_link_with_r2_url():
    raw = "# 标题\n\n![封面](某事件.png)\n\n正文"
    out = publish_legal.re.sub(
        r"(!\[[^\]]*\]\()[^)]*\.png(\))",
        r"\1https://cdn.example.com/covers/某事件.png\2",
        raw,
        count=1,
    )
    assert "![封面](https://cdn.example.com/covers/某事件.png)" in out
    assert "](某事件.png)" not in out


def test_replaces_existing_http_cover_link_too():
    raw = "# 标题\n\n![封面](https://old.example.com/a.png)\n正文"
    out = publish_legal.re.sub(
        r"(!\[[^\]]*\]\()[^)]*\.png(\))",
        r"\1https://new.r2/covers/a.png\2",
        raw,
        count=1,
    )
    assert "https://old.example.com/a.png" not in out
    assert "https://new.r2/covers/a.png" in out


# ---------- publish_md：上传 + POST（全部 mock） ----------

class FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_publish_md_uploads_cover_and_posts(tmp_path, monkeypatch):
    md = tmp_path / "某事件.md"
    png = tmp_path / "某事件.png"
    md.write_text("# 某事件标题\n\n![封面](某事件.png)\n正文", encoding="utf-8")
    png.write_bytes(b"\x89PNG fake")

    uploaded = {}

    def fake_upload(path):
        uploaded["path"] = path
        return "https://r2.example/covers/某事件.png"

    posted = {}

    def fake_post(url, headers, json, timeout):
        posted["url"] = url
        posted["headers"] = headers
        posted["payload"] = json
        return FakeResp(201, {"id": 999})

    monkeypatch.setattr(publish_legal, "upload_cover_to_r2", fake_upload)
    monkeypatch.setattr(publish_legal.requests, "post", fake_post)

    result = publish_legal.publish_md(str(md))

    assert result["success"] is True
    assert result["id"] == 999
    assert os.path.basename(uploaded["path"]) == "某事件.png"
    assert posted["url"] == publish_legal.PUBLISH_API
    assert posted["payload"]["title"] == "某事件标题"
    # content 里的封面已替换成 R2 地址
    assert "https://r2.example/covers/某事件.png" in posted["payload"]["content"]
    # 本地相对路径形式（括号内直接是文件名）已被替换
    assert "](某事件.png)" not in posted["payload"]["content"]


def test_publish_md_without_cover_still_posts(tmp_path, monkeypatch):
    md = tmp_path / "无封面.md"
    md.write_text("# 无封面标题\n\n正文", encoding="utf-8")

    def fake_upload(path):
        raise AssertionError("不该上传封面")

    posted = {}

    def fake_post(url, headers, json, timeout):
        posted["payload"] = json
        return FakeResp(201, {"id": 1})

    monkeypatch.setattr(publish_legal, "upload_cover_to_r2", fake_upload)
    monkeypatch.setattr(publish_legal.requests, "post", fake_post)

    result = publish_legal.publish_md(str(md))
    assert result["success"] is True
    # 无封面时 content 原样
    assert posted["payload"]["content"] == "# 无封面标题\n\n正文"


def test_publish_md_reports_failure_on_http_error(tmp_path, monkeypatch):
    md = tmp_path / "失败.md"
    md.write_text("# 失败标题\n正文", encoding="utf-8")

    def fake_post(url, headers, json, timeout):
        return FakeResp(500, {"err": "boom"})

    monkeypatch.setattr(publish_legal.requests, "post", fake_post)
    result = publish_legal.publish_md(str(md))
    assert result["success"] is False
    assert "HTTP 500" in result["message"]
