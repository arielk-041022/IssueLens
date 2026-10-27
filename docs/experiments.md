# 冻结实验阅读入口

本页仅导航已有结果，不重新评分、不调用模型。公开阅读集合保留失败结论；原始完整包仍在本地，没有被删除或改写。

|类别|原始入口|阅读边界|
|---|---|---|
|主要性能对照|[independent-eval-v1 正式评分](../experiments/independent-eval-v1/prediction-environment/output/unified-scoring-v1-report-v2/audit-report.md)|8例／36 GT；C和BRD各Hit@5=5/8，BM25=2/8；Direct协议失败另列|
|主要对照机器摘要|[summary.json](../experiments/independent-eval-v1/prediction-environment/output/unified-scoring-v1-report-v2/summary.json)|不能混合有效子集与全8例分母；5个身份异常必须保留|
|源码核验协议|[Evidence Validator](evidence_validation.md)|事实范围、错误码、UNVERIFIABLE，不证明根因|
|Evidence Trace演示|[展示包](../experiments/competition-evidence-pack-v1/demo.md)|LANG-1610 5/5候选绑定、13条源码事实；ACC-006 BRD20条UNBOUND；原绑定目录为 `experiments/evidence-trace-v1/`，未默认公开绝对路径绑定JSON|
|Idea 1演示|[LANG-1610证据卡](../experiments/localization-evidence-card-v1/LANG-1610.md)|历史BM25；局部PASS、整仓FAIL；并非最终LLM Top-5全部认证|
|Idea 2离线分析|[检索—重排瓶颈](../experiments/retrieval-reranking-bottleneck-v1/analysis.md)|不同候选池分开；检索遗漏、池内未选、容量上限和N/A不可混淆|
|故障注入|[14项原报告](../experiments/validator-fault-injection-v1/report.md)|受控合成故障，不是定位准确率|
|限制与负面结果|[证据包限制](../experiments/competition-evidence-pack-v1/limitations.md)|CEA 8例全部REVIEW，尚未证明区分能力；保留UNBOUND与未执行测试|
|复核方法|[证据包复核说明](../experiments/competition-evidence-pack-v1/reproducibility.md)|原manifest引用本地完整包；只有摘要时不能声称所有上游哈希可在GitHub闭合|

## 本地保留但暂不默认上传

`experiments/` 的其余内容含多轮开发实验、一次性API执行器、原始请求响应、余额／费用、私有GT、第三方源码快照、缓存和完整仓库。已有失败／BLOCKED／不确定交付记录全部保留；不会为减少体积删除反例。

Hierarchical、Class-Aware、Structure-Evidence、Top-50和CEA的协议／样本各不相同，不能汇总成统一泛化准确率。需发布完整复现包时，先审核依赖闭合、第三方许可、隐私和体积，再作为独立附件；不改原manifest来掩盖缺文件。待发布范围中没有队友正在维护的报告、截图或视频。
