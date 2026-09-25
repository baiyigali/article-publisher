"""
publish.py 的单元测试。
约定：本文件只加不删不改——新增行为补新用例，不改旧断言；网络与 R2 一律 mock，绝不真发。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# config.py 存在于本地（不入库），提供 PUBLISH_API / R2_* 等
import publish  # noqa: E402


# ---------- 封面查找：png / jpg 都支持 ----------

def test_find_cover_png_priority(tmp_path):
    md = tmp_path / "x.md"
    (tmp_path / "x.png").write_bytes(b"png")
    (tmp_path / "x.jpg").write_bytes(b"jpg")
    path, ct = publish.find_cover(str(md))
    assert path.endswith("x.png")
    assert ct == "image/png"


def test_find_cover_falls_back_to_jpg(tmp_path):
    md = tmp_path / "x.md"
    (tmp_path / "x.jpg").write_bytes(b"jpg")
    path, ct = publish.find_cover(str(md))
    assert path.endswith("x.jpg")
    assert ct == "image/jpeg"


def test_find_cover_none(tmp_path):
    md = tmp_path / "x.md"
    assert publish.find_cover(str(md)) == (None, None)


# ---------- 标题提取 ----------

def test_title_is_h1(tmp_path):
    md = tmp_path / "x.md"
    md.write_text("# 文章标题\n正文", encoding="utf-8")
    title = md.read_text(encoding="utf-8").splitlines()[0].strip().lstrip("#").strip()
    assert title == "文章标题"


# ---------- 发布：mock 掉 R2 与 HTTP ----------

class FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        import json
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _patch(monkeypatch, upload_url="https://r2/covers/x.png"):
    monkeypatch.setattr(publish, "upload_cover_to_r2", lambda p, ct: upload_url)

    posted = {}

    def fake_post(url, headers, json, timeout):
        posted["url"] = url
        posted["payload"] = json
        return FakeResp(201, {"id": 7})

    monkeypatch.setattr(publish.requests, "post", fake_post)
    return posted


def test_publish_uploads_png_cover_and_posts(tmp_path, monkeypatch):
    md = tmp_path / "x.md"
    md.write_text("# 标题\n\n![封面](x.png)\n正文", encoding="utf-8")
    (tmp_path / "x.png").write_bytes(b"png")
    posted = _patch(monkeypatch)

    r = publish.publish_md(str(md))
    assert r["success"] is True and r["id"] == 7
    assert posted["payload"]["title"] == "标题"
    assert "https://r2/covers/x.png" in posted["payload"]["content"]
    assert "](x.png)" not in posted["payload"]["content"]


def test_publish_jpg_cover_also_replaced(tmp_path, monkeypatch):
    md = tmp_path / "x.md"
    md.write_text("# 标题\n\n![封面](old.jpg)\n正文", encoding="utf-8")
    (tmp_path / "x.jpg").write_bytes(b"jpg")
    posted = _patch(monkeypatch, "https://r2/covers/x.jpg")

    r = publish.publish_md(str(md))
    assert r["success"] is True
    assert "old.jpg" not in posted["payload"]["content"]
    assert "https://r2/covers/x.jpg" in posted["payload"]["content"]


def test_publish_without_cover_still_posts(tmp_path, monkeypatch):
    md = tmp_path / "x.md"
    md.write_text("# 标题\n正文", encoding="utf-8")
    posted = _patch(monkeypatch)

    r = publish.publish_md(str(md))
    assert r["success"] is True
    assert posted["payload"]["content"] == "# 标题\n正文"


def test_publish_reports_http_error(tmp_path, monkeypatch):
    md = tmp_path / "x.md"
    md.write_text("# 标题\n正文", encoding="utf-8")
    import json

    def boom(url, headers, json, timeout):
        return FakeResp(500, {"err": "x"})

    monkeypatch.setattr(publish.requests, "post", boom)
    r = publish.publish_md(str(md))
    assert r["success"] is False
    assert "HTTP 500" in r["message"]
