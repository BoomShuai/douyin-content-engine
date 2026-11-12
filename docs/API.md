# API 与数据契约

默认 `http://127.0.0.1:8768`。命令：`python3 -m douyin_engine serve`。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/health` | 服务、模型状态及输入限制；不主动加载模型 |
| `GET /api/demo` | 36 条人工构造样例及合成来源说明 |
| `GET /api/template.csv` | UTF-8 BOM 的 CSV 模板 |
| `GET /api/evidence` | 明确说明公开版没有历史基准，不返回旧结果或私人文件 |
| `POST /api/analyze` | 导入、检测、四方向分析与来源记录 |

请求类型 `application/json`，带唯一 Content-Length，不支持分块编码：

```json
{
  "records": [{"id":"demo-1","text":"人工构造：服务很好。","context":"演示讨论"}],
  "backend": "rules",
  "bucket": "day",
  "source_name": "synthetic_example"
}
```

也可使用 `content`（UTF-8 文本）和 `format`（`csv` / `json`）代替 `records`，不能同时提供两种输入。未指定后端时使用规则模式，不是模型故障降级。`bucket` 支持 `hour` / `day`。

规范字段：`id`、`text`、`context`、`author_id`、`published_at`、`video_id`、`parent_id`、`likes`、`reply_count`。支持常见平台字段别名，见 `engine/ingest.py`。未知值不补造；缺少时刻的日期不补造午夜；无时区的完整时间按 Asia/Shanghai 解释，并在质量报告中记录。作者以批次内化名呈现，未知作者留空。

结果包含：`comments`、`summary`、`timeline`、`turning_points`、`topics`、`network`、`warnings`、`methodology`，以及 `run_id`、`created_at`、`backend`、`model`、`ingest`、`provenance`。每条评论保留反讽判定、字面情感、附原文区间的观点四元组。

规则模式反讽 `score=null`，不伪造概率。模型无法读到有效评论时 `label=null, available=false`，不会算为非反讽。模型分数未校准，所有机器结果都须人工复核。

每批最多 1000 条、原始输入 2 MiB；正文和语境各最多 4000 字符。部分行无效或重复时，响应的 `ingest` 列出拒绝/去重与原因；不能把接收数量当作原始输入数量。整个输入无效或模型失败时明确报错。

错误结构 `{"error":{"code":"...","message":"...","details":...}}`。常见状态码：400 输入问题、403 Host/Origin 不允许、413 超限、415 类型不支持、503 模型不可用或已有分析进行中。错误不包含系统路径或堆栈。

服务限制 Host 为对应端口的 localhost / 127.0.0.1；若有 Origin 须为同一本地来源，拒绝 cross-site，不开放 CORS。页面及 API 响应禁用缓存。没有公网认证、TLS 或多租户隔离，不应直接暴露到公网。
