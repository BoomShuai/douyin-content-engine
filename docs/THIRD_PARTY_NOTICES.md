# 第三方来源与发布范围

本次公开分发的是应用源码、网页、合成演示与测试。Python、Node.js、可选模型库、模型权重和第三方原始评论语料不随仓库或源码 ZIP 分发。

可选模型依赖按 `requirements-model.txt` 声明，分别保留各自上游许可：

| 组件 | 版本 | 来源 |
| --- | --- | --- |
| PyTorch | 2.8.0 | [官方许可证](https://github.com/pytorch/pytorch/blob/v2.8.0/LICENSE) |
| Transformers | 4.57.6 | [官方许可证](https://github.com/huggingface/transformers/blob/v4.57.6/LICENSE) |
| safetensors | 0.7.0 | [官方许可证](https://github.com/huggingface/safetensors/blob/v0.7.0/LICENSE) |
| NumPy | 2.0.2 | [官方许可证](https://github.com/numpy/numpy/blob/v2.0.2/LICENSE.txt) |

历史兼容模型所用 HFL Chinese RoBERTa 系列的[官方模型卡](https://huggingface.co/hfl/chinese-roberta-wwm-ext)标注 Apache-2.0。公开版本不包含该基座、分词器或微调衍生权重；基础模型许可标识不替代下游数据和实际权重来源的核对。本项目不宣称由 HFL 或抖音官方发布、授权或背书。

`data/demo.json` 是人工构造的虚构配送讨论，测试中的人物标识、评论、响应和隐私格式均为合成夹具，不代表真实平台用户。测试所用 example 域名和文档用 IP 地址不用于实际联络。

本项目尚未为原创部分指定开源许可证。公开可见与第三方材料的授权范围应分别理解；若未来加入模型、原始语料、截图或第三方二进制，需要重新检查实际分发范围。
