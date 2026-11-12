# 知微 · 抖音内容分析研究引擎

[![CI](https://github.com/BoomShuai/douyin-content-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/BoomShuai/douyin-content-engine/actions/workflows/ci.yml)

一个可独立运行的本地评论研究工作台：**反讽线索、观点四元组、时序主题、回复网络与人工复核**。导入自己有权使用的 CSV / JSON，查看每条判断对应的原文证据，再导出 JSON / CSV。

默认规则模式只依赖 Python 标准库，无 API Key、数据库、Node.js 或云端推理。页面不使用外部 CDN，不自动保存输入。公开仓库包含应用源码、36 条人工构造的演示评论和测试；**不包含历史模型权重、原始语料、逐条预测、个人报告或旧 Git 历史**。

## 快速启动

需要 Python 3.9 或更新版本：

```sh
git clone https://github.com/BoomShuai/douyin-content-engine.git
cd douyin-content-engine
python3 -m douyin_engine serve
```

打开 **http://127.0.0.1:8768/**，点击“载入并分析演示样例”。Windows 将 `python3` 改为 `python`。macOS 也可以运行 `sh start.command`；端口占用时加 `--port 8769`。

默认选择“规则演示（无需模型）”，各方向的分析、人工复核与导出均可使用；反讽线索属于有限启发式，分数为 `null`，不冒充训练模型结果。若主动选择模型但没有配置权重，系统会明确报错，不自动替换结果。

## 功能与边界

| 模块 | 当前实现 | 解释范围 |
| --- | --- | --- |
| 导入与数据质量 | CSV / JSON、字段别名、缺失值、重复 ID、批次化名及常见隐私模式遮盖 | 不补造时间、作者和回复关系；不是完全匿名化 |
| 反讽 | 默认规则线索；可选本地二分类模型 | 未校准、待人工复核，不自动翻转情感 |
| 观点与情感 | 评价人、对象、观点、情感、原文区间及词典证据 | 可解释规则基线，不是已训练的四元组模型 |
| 时序与主题 | 按小时/天聚合、空桶/缺口、词典主题、变化提示 | 只代表导入样本，不是 BERTopic 或因果结论 |
| 互动关系 | 已提供回复边、度数、PageRank、原文回查 | 样本内可见关系，不证明操纵或幕后影响 |
| 人工复核 | 标签、备注、时间单独保存于当前页面 | 保留原始机器预测，不混作已验证标注 |
| 导出 | JSON / CSV，CSV 公式注入防护 | 含原文及复核记录，分享前仍需人工检查 |

本项目不是抖音官方产品。没有真实平台采集验收或真实抖音人工标注效果评估；构造样例和单元测试不能证明模型准确率。

## 批量分析与 Python SDK

```sh
python3 -m douyin_engine analyze douyin_engine/data/demo.json --source-name synthetic_demo --output outputs/demo.json
python3 -m douyin_engine analyze douyin_engine/data/demo.json --backend rules --source-name synthetic_demo --bucket hour --output outputs/demo.csv
python3 -m douyin_engine doctor
```

输入为 UTF-8 CSV 或 JSON，每批最多 1000 条、2 MiB；正文和语境各最多 4000 字符。输出目标必须不存在，避免覆盖旧分析。模型模式是明确的 `--backend model`，用法见 [本地模型接入](docs/MODELS.md)。

在其他目录使用 SDK 或命令入口，可先安装：

```sh
python3 -m pip install .
douyin-content-engine serve
```

```python
from douyin_engine.engine.pipeline import AnalysisEngine

result = AnalysisEngine().analyze({
    "records": [{"text": "等了三天还没送到，可真是贴心的服务。", "context": "虚构配送讨论"}],
    "backend": "rules",
    "source_name": "synthetic_example"
})
print(result["summary"])
```

## 自备模型

公开版本保留本地模型适配器，但不分发原项目的微调权重。持有使用权限的用户可提供兼容模型：

```sh
python3 -m pip install '.[model]'
python3 -m douyin_engine serve --model-dir models/sarcasm_m4 --device cpu
```

在页面中明确切换到本地模型。模型要求标签 `0=非反讽、1=反讽`，并兼容既定预处理协议。模型、训练数据及来源许可由提供者核对；**不要用未微调的基座模型或随机分类头冒充反讽识别器**。详情见 [MODELS.md](docs/MODELS.md)。

首次安装可选依赖需要联网；正常文件分析与本地推理无需外部模型服务。启动器不会自动安装依赖或下载权重。`sh setup.sh` 安装核心包，`sh setup.sh --model` 安装可选模型依赖，均不下载模型。

## 本地 API 与响应适配

```sh
curl http://127.0.0.1:8768/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"records":[{"text":"人工构造：配送速度很快。"}],"backend":"rules"}'
```

API、字段与错误码见 [API.md](docs/API.md)。只绑定 `127.0.0.1`，不开放 CORS，没有账号认证或公网部署入口。

可选 SDK / CLI 能适配用户合法保存的评论响应 JSON，也有受限的公开页面 HTML 检查。它不登录、不读取 Cookie、不逆向签名、不绕过验证码；访问成功不代表评论采集成功。默认工作台不会调用采集功能，详见 [采集边界](docs/COLLECTION.md)。

## 验证与维护

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
node scripts/test_frontend.cjs
python3 scripts/privacy_check.py --history
python3 scripts/build_release.py --output dist/douyin-content-engine-v1.0.1.zip
python3 scripts/verify_release.py dist/douyin-content-engine-v1.0.1.zip
```

Node.js 只用于开发时的前端检查。源码 ZIP 附带 SHA-256 清单，打包白名单主动排除模型、原始数据、个人文档、运行结果及历史评测。它不依赖 Git LFS，不打包虚拟环境或第三方运行库。

```text
douyin_engine/
  engine/           导入、反讽适配、观点/时序/网络与可选响应适配
  web/              无外部 CDN 的本地工作台
  data/demo.json    36 条人工构造样例
  server.py         本地 HTTP API
  cli.py            批量分析与导出
  doctor.py         只读环境诊断
scripts/            前端检查、隐私扫描、源码打包与校验
tests/              软件行为与可选真实本地模型测试
docs/               API、模型、架构、数据边界与验证记录
```

更多说明：[架构与局限](docs/ARCHITECTURE.md) · [验证记录](docs/VALIDATION.md) · [隐私说明](PRIVACY.md) · [第三方来源](docs/THIRD_PARTY_NOTICES.md) · [贡献规范](CONTRIBUTING.md)。许可证暂未指定。
