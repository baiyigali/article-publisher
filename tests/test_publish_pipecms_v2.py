# tests/test_publish_pipecms_v2.py
# v2 发布脚本测试：front matter 解析、payload 组装、发布链路（全部 mock，不联网）
import json
import os

import pytest
import requests

import publish_pipecms_v2 as pub


def write_md(tmp_path, name="篇1.md", content="", cover=False):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    if cover:
        (tmp_path / (name.rsplit(".", 1)[0] + ".png")).write_bytes(b"fake-image")
    return str(p)


R2_CFG = {
    "endpoint": "https://r2.example.com",
    "access_key": "ak",
    "secret_key": "sk",
    "bucket": "bkt",
    "public_base": "https://pub.example.com",
    "key_prefix": "covers",
}
CFG = {"api": "https://pipecms.com/articles/api/v2/create/", "token": "tok", "r2": R2_CFG}


# ==================== front matter 解析 ====================

class TestParseFrontMatter:
    def test_no_front_matter_returns_raw(self):
        raw = "# 标题\n\n正文"
        meta, body = pub.parse_front_matter(raw)
        assert meta == {}
        assert body == raw

    def test_parses_full_meta(self):
        raw = (
            "---\n"
            "title: 标题A\n"
            "summary: 摘要A\n"
            "creation_type: ai_only\n"
            "status: draft\n"
            "cover_image: https://img.example.com/a.png\n"
            "tags: [AI, CMS]\n"
            "---\n"
            "# 标题A\n\n正文"
        )
        meta, body = pub.parse_front_matter(raw)
        assert meta["title"] == "标题A"
        assert meta["summary"] == "摘要A"
        assert meta["creation_type"] == "ai_only"
        assert meta["status"] == "draft"
        assert meta["cover_image"] == "https://img.example.com/a.png"
        assert meta["tags"] == ["AI", "CMS"]
        assert body.startswith("# 标题A")

    def test_tags_comma_string(self):
        raw = "---\ntags: AI, CMS\n---\n正文"
        meta, _ = pub.parse_front_matter(raw)
        assert meta["tags"] == "AI, CMS"

    def test_unclosed_front_matter_returns_raw(self):
        raw = "---\ntitle: 没闭合\n正文"
        meta, body = pub.parse_front_matter(raw)
        assert meta == {}
        assert body == raw


# ==================== 标题 / 标签 / 摘要 ====================

class TestExtractors:
    def test_first_h1(self):
        assert pub.first_h1("# 标题\n\n正文") == "标题"
        assert pub.first_h1("正文\n# 不是标题") == ""

    def test_extract_tags_from_body(self):
        assert pub.extract_tags_from_body("正文\n\n#标签1 #标签2") == ["标签1", "标签2"]
        assert pub.extract_tags_from_body("正文") == []

    def test_make_summary_skips_title_and_strips_markdown(self):
        body = "# 标题\n\n**加粗**的第一段内容"
        assert pub.make_summary(body) == "加粗的第一段内容"

    def test_make_summary_limits_length(self):
        body = "# 标题\n\n" + "长" * 300
        s = pub.make_summary(body)
        assert len(s) == 100

    def test_make_summary_empty_body(self):
        assert pub.make_summary("") == ""


# ==================== payload 组装 ====================

class TestBuildPayload:
    def test_full_front_matter_payload(self, tmp_path):
        md = write_md(tmp_path, content=(
            "---\n"
            "title: 全字段文章\n"
            "summary: 摘要\n"
            "creation_type: ai_only\n"
            "status: draft\n"
            "cover_image: https://img.example.com/c.png\n"
            "tags: [AI, CMS]\n"
            "---\n"
            "# 全字段文章\n\n正文"
        ))
        payload, cover_log, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        assert payload["title"] == "全字段文章"
        assert payload["summary"] == "摘要"
        assert payload["creation_type"] == "ai_only"
        assert payload["status"] == "draft"
        assert payload["cover_image"] == "https://img.example.com/c.png"
        assert payload["tag_names"] == ["AI", "CMS"]
        assert payload["author"] == "作者"
        assert "front matter 封面" in cover_log

    def test_no_front_matter_fallback(self, tmp_path):
        md = write_md(tmp_path, content="# 回退标题\n\n第一段正文内容\n\n#标签A #标签B")
        payload, _, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        assert payload["title"] == "回退标题"
        assert payload["summary"] == "第一段正文内容"
        assert payload["tag_names"] == ["标签A", "标签B"]
        assert payload["creation_type"] == "ai_assisted"
        assert payload["status"] == "published"
        assert payload["cover_image"] == ""

    def test_cli_defaults_overridden_by_front_matter(self, tmp_path):
        md = write_md(tmp_path, content="---\nstatus: draft\n---\n# t\n\n正文")
        payload, _, _ = pub.build_payload(md, CFG, "作者", creation_type="ai_only", status="published")
        assert payload["creation_type"] == "ai_only"   # front matter 没写，用 CLI
        assert payload["status"] == "draft"            # front matter 覆盖 CLI

    def test_local_cover_without_r2_errors(self, tmp_path):
        md = write_md(tmp_path, content="# 标题\n\n正文", cover=True)
        _, _, error = pub.build_payload(md, {"api": "x", "token": "t"}, "作者")
        assert "没写 r2" in error

    def test_local_cover_uploads_to_r2(self, tmp_path, monkeypatch):
        md = write_md(tmp_path, content="# 标题\n\n正文", cover=True)
        import sys
        import types

        class FakeClient:
            def __init__(self, *args, **kw):
                self.kw = kw

            def upload_file(self, *a, **kw):
                pass

        # boto3 在 upload_cover 函数内 import，需 mock sys.modules 里的 boto3 模块
        fake_boto3 = types.SimpleNamespace(client=lambda *a, **kw: FakeClient(*a, **kw))
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
        payload, cover_log, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        assert payload["cover_image"] == "https://pub.example.com/covers/篇1.png"
        assert "上传封面" in cover_log

    def test_old_md_cover_line_stripped_from_body(self, tmp_path, monkeypatch):
        """老 md 封面嵌在正文：封面进 cover_image 字段后，正文里封面行要剥掉"""
        md = write_md(tmp_path, content=(
            "# 标题\n\n![封面](篇1.png)\n\n第一段正文内容"
        ), cover=True)
        import sys
        import types

        class FakeClient:
            def __init__(self, *args, **kw):
                pass

            def upload_file(self, *a, **kw):
                pass

        fake_boto3 = types.SimpleNamespace(client=lambda *a, **kw: FakeClient(*a, **kw))
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
        payload, _, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        # 封面行被剥离，正文不再有 ![封面](篇1.png)
        assert "![封面]" not in payload["content"]
        # 摘要取自第一段正文而非封面行
        assert payload["summary"] == "第一段正文内容"
        assert payload["cover_image"] == "https://pub.example.com/covers/篇1.png"

    def test_title_and_tags_lines_stripped_from_body(self, tmp_path):
        """标题行和末尾标签行都进独立字段后，正文里不再残留"""
        md = write_md(tmp_path, content="# 标题\n\n第一段正文内容\n\n#标签A #标签B")
        payload, _, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        assert payload["title"] == "标题"
        assert payload["tag_names"] == ["标签A", "标签B"]
        content = payload["content"]
        # 正文不再包含标题行和标签行
        assert not content.lstrip().startswith("# 标题")
        assert "#标签A" not in content
        assert "第一段正文内容" in content
        assert payload["summary"] == "第一段正文内容"

    def test_tags_line_with_spaced_tag_stripped(self, tmp_path):
        """#AI Agent 这种带空格的标签行也能被识别剥离（前端旧正则的坑）"""
        md = write_md(tmp_path, content="# 标题\n\n正文内容\n\n#AI大模型 #OpenAI #AI Agent")
        payload, _, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        assert "#AI大模型" not in payload["content"]
        assert "正文内容" in payload["content"]
        assert payload["tag_names"][0] == "AI大模型"

    def test_topic_tags_prefix_line_stripped(self, tmp_path):
        """"**话题标签**：#xx" 前缀格式的标签行也被剥离"""
        md = write_md(tmp_path, content="# 标题\n\n正文内容\n\n**话题标签**：#标签1 #标签2")
        payload, _, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        assert "话题标签" not in payload["content"]
        assert "正文内容" in payload["content"]

    def test_hr_line_not_stripped(self, tmp_path):
        """正文里独立的 --- 水平线不是标签行，不能被误删"""
        md = write_md(tmp_path, content="# 标题\n\n正文内容\n\n---\n\n结尾")
        payload, _, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        assert "---" in payload["content"]
        assert "结尾" in payload["content"]

    def test_separator_above_tags_line_stripped(self, tmp_path):
        """标签行上方的 --- 分隔线随标签行一起剥掉，正文末尾不留孤线"""
        md = write_md(tmp_path, content="# 标题\n\n正文内容\n\n---\n\n#标签1 #标签2")
        payload, _, error = pub.build_payload(md, CFG, "作者")
        assert error == ""
        content = payload["content"]
        assert "#标签1" not in content
        assert "---" not in content
        assert "正文内容" in content


# ==================== 发布链路（mock requests）====================

class TestPublishMd:
    def test_success_returns_id(self, tmp_path, monkeypatch):
        md = write_md(tmp_path, content="# 标题\n\n正文")

        class FakeResp:
            status_code = 201

            def json(self):
                return {"id": 42}

        monkeypatch.setattr("publish_pipecms_v2.requests.post", lambda *a, **kw: FakeResp())
        result = pub.publish_md(md, CFG, "作者")
        assert result["success"] is True
        assert result["id"] == 42
        assert result["title"] == "标题"

    def test_posts_to_v2_endpoint_with_tag_names(self, tmp_path, monkeypatch):
        md = write_md(tmp_path, content="# 标题\n\n正文\n\n#标签A")
        captured = {}

        class FakeResp:
            status_code = 201

            def json(self):
                return {"id": 1}

        def fake_post(url, headers, json, timeout):
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

        monkeypatch.setattr("publish_pipecms_v2.requests.post", fake_post)
        pub.publish_md(md, CFG, "作者")
        assert captured["url"] == CFG["api"]
        body = captured["json"]
        assert body["tag_names"] == ["标签A"]
        assert body["author"] == "作者"

    def test_http_400_reports_message(self, tmp_path, monkeypatch):
        md = write_md(tmp_path, content="# 标题\n\n正文")

        class FakeResp:
            status_code = 400
            text = '{"detail": "bad"}'

        monkeypatch.setattr("publish_pipecms_v2.requests.post", lambda *a, **kw: FakeResp())
        result = pub.publish_md(md, CFG, "作者")
        assert result["success"] is False
        assert "HTTP 400" in result["message"]

    def test_missing_file_reports_error(self, tmp_path):
        result = pub.publish_md(str(tmp_path / "不存在.md"), CFG, "作者")
        assert result["success"] is False

    def test_request_exception_reports_error(self, tmp_path, monkeypatch):
        md = write_md(tmp_path, content="# 标题\n\n正文")

        def boom(*a, **kw):
            raise requests.ConnectionError("网络断了")

        monkeypatch.setattr("publish_pipecms_v2.requests.post", boom)
        result = pub.publish_md(md, CFG, "作者")
        assert result["success"] is False
        assert "请求失败" in result["message"]
