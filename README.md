# Article Publisher

把本地 Markdown 文章发布到自建网站，封面自动上传到 Cloudflare R2 对象存储。一个脚本通吃，不区分内容类型。

## 快速开始

### 1. 安装依赖

```bash
pip install requests boto3
```

### 2. 配置

```bash
cp config.template.py config.py
```

编辑 `config.py` 填入发布 API 与 R2 凭据（`config.py` 已在 .gitignore，不入库）。

### 3. 发布

给哪些 md 就发哪些，**不扫目录、不记状态、不管历史**：

```bash
python3 publish.py 篇1.md 篇2.md
```

## 工作流程

1. 读取传入的每个 `.md`
2. 找同名封面（`.png` 优先，其次 `.jpg`/`.jpeg`，没有就跳过封面）
3. 封面上传 R2，替换 md 里第一张图链接为 R2 地址
4. POST `{title, content}` 到发布 API

## 文件结构

```
article-publisher/
├── publish.py            # 统一发布脚本
├── config.template.py    # 配置模板
├── config.py             # 本地真实配置（不入库）
├── tests/                # pytest 测试
└── README.md
```

## 测试

```bash
python3 -m pytest tests/ -q
```

R2 与发布 API 全部 mock，不真上传、不真发布。协作约定见 [AGENTS.md](AGENTS.md)。

## 注意

- 支持相对路径和绝对路径的图片链接替换。
- 封面 png/jpg/jpeg 都认，按扩展名设 ContentType。
