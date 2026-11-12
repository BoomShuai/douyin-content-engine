# 维护约定

`main` 保存可运行版本。使用 `feat/主题`、`fix/问题`、`docs/主题` 分支和 Pull Request；提交说明采用对应前缀，一次变更聚焦一个问题。

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
node scripts/test_frontend.cjs
git add douyin_engine tests scripts docs README.md PRIVACY.md CONTRIBUTING.md
python3 scripts/privacy_check.py --history
git diff --cached --stat
```

提交及标签使用项目署名和 GitHub noreply 邮箱，避免公开私人邮箱。新增示例使用合成内容，不提交真实评论、个人报告、权重和历史预测。变更数据流、存储或联网行为时同步更新隐私说明。

默认模式保持零依赖。新增功能需补充实际行为测试；涉及 UI 时检查实际页面和导出。模型真实推理测试需要显式开启，不用 mock 或合成样例替代模型效果评估。

发布使用 `vX.Y.Z` 标签。构建源码 ZIP 后，校验清单、解压到新目录，再验证启动和演示。源码 ZIP 和 wheel 都不包含模型；不要将个人运行目录整体打包上传。
