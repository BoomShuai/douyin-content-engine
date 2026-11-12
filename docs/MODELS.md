# 可选本地反讽模型

公开仓库和源码 ZIP 不包含模型、分词器、历史训练语料、逐条预测及旧实验指标。默认规则模式可独立运行完整操作流程，但反讽输出只是有限启发式。

## 兼容要求

模型目录由用户提供，放在已忽略的 `models/` 或通过 `--model-dir` 指定。也可用本机环境变量 `DOUYIN_MODEL_DIR` 指定默认目录；路径不会通过 API 返回。

最少文件：`config.json`、`model.safetensors`、`tokenizer_config.json`、`vocab.txt`；其他模型可能还需要 `tokenizer.json` 和 `special_tokens_map.json`。需能由 Transformers 的 `AutoTokenizer` 和 `AutoModelForSequenceClassification` 在纯本地、`trust_remote_code=False`、`use_safetensors=True` 条件下加载。

必须是兼容的已训练二分类器，标签 0 为非反讽、1 为反讽。仅检查二分类结构不能证明标签语义或模型质量；提供者须核对模型卡和训练协议。基座模型、随机分类头、正负面情感模型不能据此冒充反讽分类器。

## 预处理与推理

保留适配协议：去除 URL 和连续 @提及、合并空白；有语境时拼接“话题：… | 评论：…”，取前 256 字符，再分词到最多 256 token。过长语境可能挤掉评论，此时返回无法判定，不强行贴标签。`@` 规则也可能删除提及后的有效文字，是既有协议的已知限制。

阈值固定 0.5；[0.35, 0.65] 为优先复核区间，不是置信区间。softmax 分数未校准，不代表正确率。反讽不会自动翻转字面情感，不能用于自动处罚或个人属性推断。

```sh
python3 -m pip install '.[model]'
python3 -m douyin_engine doctor --model-dir models/sarcasm_m4 --require-model
python3 -m douyin_engine serve --model-dir models/sarcasm_m4 --device cpu
```

显式支持 CPU / MPS / CUDA，设备不可用会报错。加载使用本地文件、关闭远程代码，模型不会自动下载。设置模型目录不改变页面的默认规则选项，需要用户明确选择本地模型。

## 真实模型测试

仅在有权使用且准备好本地兼容权重时运行：

```sh
RUN_MODEL_TESTS=1 DOUYIN_MODEL_DIR=models/sarcasm_m4 python3 -m unittest discover -s tests -p 'test_model_offline.py' -v
```

该组测试阻断 socket 和 DNS，验证真实 CPU 加载、分数、单条/批量一致性、无法判定输入和不联网行为；不评估准确率。默认 CI 不含权重，会明确跳过这组测试。

原项目的微调权重没有在本次公开发布中重新分发。已有权重的使用和再分发范围，应依其实际来源、训练数据与适用条件另行核对；本项目不授予第三方材料的新权利。
