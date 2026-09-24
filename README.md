# Article Publisher

文章自动发布工具，将本地 Markdown 文章发布到自建网站，自动上传封面图到 Cloudflare R2 对象存储。

## 功能

- 扫描指定目录下的 MD 文件
- 自动上传同名封面图（.jpg）到 Cloudflare R2
- 自动替换 MD 里的图片链接为 R2 云端链接
- 调用网站发布 API 发布文章
- 发布状态跟踪，避免重复发布

## 快速开始

### 1. 安装依赖

```bash
pip install requests boto3
```

### 2. 配置

复制配置模板：

```bash
cp config.template.py config.py
```

然后编辑 `config.py`，填入你的配置：

```python
# 网站发布配置
PUBLISH_API = "http://your-site.com/articles/api/create/"
PUBLISH_TOKEN = "your-token-here"

# R2 对象存储配置
R2_ENDPOINT = "https://xxxx.r2.cloudflarestorage.com"
R2_ACCESS_KEY = "your-access-key-id"
R2_SECRET_KEY = "your-secret-access-key"
R2_BUCKET = "your-bucket-name"
R2_PUBLIC_BASE = "https://pub-xxxx.r2.dev"
R2_KEY_PREFIX = "covers"

# 文章目录
ARTICLES_DIR = "/path/to/your/articles"
```

### 3. 运行

```bash
python3 publish_with_r2.py
```

## 文件结构

```
article-publisher/
├── publish_with_r2.py    # 主发布脚本
├── config.template.py    # 配置模板
├── articles_status.json  # 发布状态（自动生成）
└── README.md
```

## 工作流程

1. 扫描 `ARTICLES_DIR` 下的所有 `.md` 文件
2. 读取状态文件，跳过已发布的文章
3. 找到同名 `.jpg` 封面图，上传到 R2
4. 替换 MD 里的本地图片链接为 R2 云端 URL
5. 调用发布 API 发布文章
6. 更新状态文件

## 注意事项

- 配置文件 `config.py` 不要提交到 git（已在 .gitignore 里）
- 状态文件 `articles_status.json` 记录已发布的文章，避免重复发布
- 支持相对路径和绝对路径的图片链接替换
