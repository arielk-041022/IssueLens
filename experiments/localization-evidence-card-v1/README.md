# 最小定位证据卡

真实示例为 `LANG-1610.md`：历史BM25 Top-5，5/5精确绑定、13条保存的源码事实记录。保存的选择证据局部PASS与整仓FAIL同时展示；不代表最终LLM结果、当前源码重新认证、AST语义或Bug根因认证。

## 运行

在项目根目录执行，输出必须是不存在的新文件：

```powershell
python -B src/localization_evidence_card.py `
  --bundle experiments/localization-evidence-card-v1/inputs.json `
  --bundle-sha256 67c15dca902f3d182694d34595f4e59bba923bd54aacf05acdde9effba055881 `
  --output evidence-card-new.md
```

```powershell
python -B -m unittest discover -s tests -p test_localization_evidence_card.py -v
```

`inputs.json`只绑定7份已有输入：历史候选、Trace、索引、metadata、Validator报告、快照绑定及公开Replay案例文本。各文件先核对SHA-256，再复用原Trace函数重现并与保存记录完整比较；不重跑检索、Validator、测试或模型。候选方法ID、路径、行号必须精确一致，范围冲突失败关闭。所有字段采用实际已有结构，没有新增证据或推荐解释。

## 自动化与演示边界

- 已自动化：输入指纹检查、原Trace一致性检查、5个候选身份与方法范围关联、源码片段与错误码展示、局部/整仓状态分开、缺失项提示、输出拒绝覆盖。
- 已有合成覆盖：UNBOUND、UNVERIFIABLE、明确源码不一致、模型推断与事实分离、命令打印不等于执行、身份和范围错配、确定性与输入不变。
- 仍是独立离线展示，不接入主CLI、不获取实时Issue、不自动检索源码、不运行Maven、不认证保存报告的时效性或正确根因。
- 当前公开Replay输入没有测试命令/执行结果、LLM理由或反面源码记录，均显示“未提供”。Trace的NOT_ESTABLISHED_BY_TRACE照原样展示；不能把未提供命令理解为其他历史目录不存在可选命令。
- 资产定位时发现 `demo-validation.json` 同时含 `gt_check`，因此未把该文件作为脚本或卡片输入，也未从中抽取推荐解释、Git提交或测试命令。程序只读取明确固定的7份输入；不宣称零历史答案接触或盲测。
- 快照标签保留原值，不能将其哈希标签改称Git提交；所选绑定文件未提供commit，故显示“未提供”。
- `saved-llm`模式只在排序原件确有reason时展示该排序级模型文字，明确未核验，不将其重新分配或编造成逐候选因果证据。原ACC-006位置排列若需展示，应先使用既有Trace身份化入口；本最小展示入口不新增位置协议适配。

本轮没有修改Replay、Validator、Trace、主CLI、排序算法、Prompt或历史实验文件；没有新模型调用或准确率评分。
