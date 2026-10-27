# IssueLens 参赛证据展示包 v0.1

仅展示已有结果的可追溯性和能力边界；不提供新的准确率或算法收益。

|层次|本包可以说明|不能据此说明|
|---|---|---|
|候选身份关联|最终排序→精确方法ID→固定索引→证据ID|定位正确性；v1/v2身份等价|
|源码事实匹配|已保存报告中的文件哈希、UTF-16范围、原始片段匹配|当前源码重新认证；语义根因|
|语义根因证明|本包未提供|不能用模型VALID、局部PASS、CEA稳定替代|

## LANG-1610：局部事实匹配

这是已有离线BM25演示Top-5，不是最终LLM Top-5认证。5/5候选BOUND，关联13条 SOURCE_FACT_MATCH_RECORDED。
原Validator局部选择证据PASS，原整仓结论仍为FAIL；局部成功不能升级整仓，整仓失败也不抹去独立局部事实。

首候选：`java-source-v1:org.apache.commons.lang3.StringUtils#unwrap(String,String)`。

- 证据ID：`sha256:8945655d36645e95efdfad7625cf37effc9613e3521e9aac92c1a2be2d135f0c`。
- 类型：`METHOD_CALL`；UTF-16范围（1-based inclusive）：9345:13–9345:24。
- 保存片段：`isEmpty(str)`。
- 原Validator状态：`VERIFIED`；原因：`ALL_LOCAL_SOURCE_CHECKS_PASSED`。

快照关联依据是候选原有index_sha256与索引、metadata和报告一致；旧source_snapshot_id标签保留，不重新解释为Git提交。
[完整LANG-1610追溯](../evidence-trace-v1/LANG-1610.trace.json)

## ACC-006 LANG-1645 / BRD：明确展示UNBOUND

复用已封存位置排列与原候选池。完整20条排序均UNBOUND，以下只展示前5条；没有重新调用API或修正排序。

|最终排名|原BM25排名|精确方法ID|追溯/源码事实状态|
|---:|---:|---|---|
|1|2|`java-source-v2:org.apache.commons.lang3.math.NumberUtils#createNumber(java.lang.String)`|UNBOUND / UNVERIFIABLE|
|2|1|`java-source-v2:org.apache.commons.lang3.math.NumberUtils#createBigInteger(java.lang.String)`|UNBOUND / UNVERIFIABLE|
|3|3|`java-source-v2:org.apache.commons.lang3.math.NumberUtils#createInteger(java.lang.String)`|UNBOUND / UNVERIFIABLE|
|4|13|`java-source-v2:org.apache.commons.lang3.math.NumberUtils#createLong(java.lang.String)`|UNBOUND / UNVERIFIABLE|
|5|14|`java-source-v2:org.apache.commons.lang3.math.NumberUtils#createFloat(java.lang.String)`|UNBOUND / UNVERIFIABLE|

原未核验原因原样保留：
- `IDENTITY_PROTOCOL_INCOMPATIBLE_WITH_SAVED_VALIDATOR`
- `SAVED_VALIDATOR_INDEX_INTEGRITY_NOT_PASS`
- `SOURCE_SNAPSHOT_BINDING_NOT_PROVEN`

现有source-binding-v2报告不是兼容的Validator认证结构；候选也缺少该追溯器要求的逐候选index_sha256。
v2身份不能强行当成旧java-source-v1认证对象。这里的UNBOUND不等于定位错误或源码事实被反驳。
模型排序协议成功不等于证据VERIFIED；不得宣称最终LLM Top-5全部认证成功。
[完整BRD追溯](../evidence-trace-v1/ACC006-LANG-1645-BRD.trace.json)

## 必须保留的CEA负面结果

固定8例全部REVIEW；5例有可靠标签，3例LABEL_UNKNOWN。封存统计：TP=4、FP=1、TN=0、FN=0。
错误识别召回为4/4，误报为1/1；与全部送审相比没有区分能力，不能据100%召回宣称诊断有效。
不设新阈值，不宣称严格盲测、因果证明或泛化收益。
[原CEA评估摘要](../cea-evaluation-20261025-v1/summary.json)
