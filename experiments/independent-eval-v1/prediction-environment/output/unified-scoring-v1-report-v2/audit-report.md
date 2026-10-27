# IssueLens：首次冻结 GT 统一评分

预测/协议先核验并写入prediction-gate.json，随后才读取GT。GT manifest64972074…1605个文件全部通过，原始GT/Issue/预测/Prompt/源码均未修改。本次无新API、索引、检索或模型运行。

## GT映射与异常

固定8例/36方法；映射分布：{'RESOLVED': 32, 'ABSENT_FROM_CATALOG': 4}。唯一绑定32个；可证实目录缺失4个，按已预注册的GT_ABSENT_FROM_CATALOG口径计为已知漏检，保留36分母，绝不映射到外层方法。UNRESOLVED=0；若非零则全样本官方准确率N/A。
31个AVAILABLE方法同时验证完整java-source-v2身份及限定类名；1个UNAVAILABLE_TYPE_ERASURE方法以相同repo/Parent SHA/文件完整哈希/原始声明参数及唯一UTF16源范围/源码切片绑定到已有occurrence ID，未伪造严格v2身份。匿名类不构造$1/$2名称。
匹配先于加载预测排名；没有按命中选择映射。全部GT的独立parent AST声明、源参数、词法外层、匿名站点、enclosing_callable和真实预测前源码逐条核验。四个缺失方法所在文件的目录记录全部有源码位置，且无同名隐藏记录，因此不是无法判断的UNRESOLVED。

|案例|方法|旧身份异常|评分映射|
|---|---|---|---|
|GSON-1875|construct|NOT_INDEXED_ANONYMOUS_DECLARATION|ABSENT_FROM_CATALOG|
|GSON-1875|run|NOT_INDEXED_ANONYMOUS_DECLARATION|ABSENT_FROM_CATALOG|
|GSON-1931|create|UNAVAILABLE_TYPE_ERASURE|RESOLVED|
|GSON-1931|read|NOT_INDEXED_ANONYMOUS_DECLARATION|ABSENT_FROM_CATALOG|
|GSON-1931|write|NOT_INDEXED_ANONYMOUS_DECLARATION|ABSENT_FROM_CATALOG|

## 统计口径

Hit@1/5：任一主GT命中；MRR@5：第一个GT命中排名的倒数，否则0。方法Recall同时报案例宏平均及方法微平均；完整覆盖要求该案例全部GT均命中。无效/未执行预测在全8例口径按任务失败计0，原始ranking仍null，不生成伪排名。有效子集只取协议有效且GT可判定案例，明确分母；有效样本0时N/A。ABSENT保留在方法分母中。若存在UNRESOLVED则官方全样本指标N/A，而非缩小分母。
GSON-1908含17GT、LANG-1659含7GT，Top5不可能完整覆盖；加上4个目录缺失方法，理论上最多18/36方法命中、4/8案例完整覆盖。这是任务/目录上限，不用于改变GT或预测。

|组|有效|Hit@1|Hit@5|MRR@5|方法微召回|方法宏召回|完整覆盖@5|
|---|---:|---:|---:|---:|---:|---:|---:|
|BM25|8/8|0.250000|0.250000|0.250000|0.055556|0.156250|0.125000|
|IssueLens-C|8/8|0.250000|0.625000|0.395833|0.138889|0.385417|0.250000|
|BRD|8/8|0.250000|0.625000|0.395833|0.138889|0.385417|0.250000|
|Direct-v1|0/8|0.000000|0.000000|0.000000|0.000000|0.000000|0.000000|
|Direct-v2|3/8|0.375000|0.375000|0.375000|0.111111|0.202381|0.125000|

### 有效子集

BM25有效子集：n=8，GT分母=36；Hit@1=0.250000，Hit@5=0.250000，MRR@5=0.250000，方法微召回=0.055556，宏召回=0.156250，完整覆盖=0.125000。

IssueLens-C有效子集：n=8，GT分母=36；Hit@1=0.250000，Hit@5=0.625000，MRR@5=0.395833，方法微召回=0.138889，宏召回=0.385417，完整覆盖=0.250000。

BRD有效子集：n=8，GT分母=36；Hit@1=0.250000，Hit@5=0.625000，MRR@5=0.395833，方法微召回=0.138889，宏召回=0.385417，完整覆盖=0.250000。

Direct-v1有效子集：n=0，GT分母=0；Hit@1=N/A，Hit@5=N/A，MRR@5=N/A，方法微召回=N/A，宏召回=N/A，完整覆盖=N/A。

Direct-v2有效子集：n=3，GT分母=11；Hit@1=1.000000，Hit@5=1.000000，MRR@5=1.000000，方法微召回=0.363636，宏召回=0.539683，完整覆盖=0.333333。


## 逐例命中、失败及Token

完整GT逐项位置、BM25全排名、各组有效排名和全部失败原因见per-case.json/per-case.csv；所有实际调用包括失败调用的usage见call-usage.json。没有使用v2反事实输出替代任何正式预测。

|案例|组|状态|GT命中/全部|Hit@1/5|MRR@5|调用|输入/输出Token|
|---|---|---|---:|---|---:|---:|---:|
|GSON-1870|BM25|VALID|0/1|0.0/0.0|0.0|0|0/0|
|GSON-1875|BM25|VALID|1/4|1.0/1.0|1.0|0|0/0|
|GSON-1908|BM25|VALID|0/17|0.0/0.0|0.0|0|0/0|
|GSON-1931|BM25|VALID|0/3|0.0/0.0|0.0|0|0/0|
|LANG-1655|BM25|VALID|1/1|1.0/1.0|1.0|0|0/0|
|LANG-1659|BM25|VALID|0/7|0.0/0.0|0.0|0|0/0|
|LANG-1662|BM25|VALID|0/2|0.0/0.0|0.0|0|0/0|
|LANG-1669|BM25|VALID|0/1|0.0/0.0|0.0|0|0/0|
|GSON-1870|IssueLens-C|VALID|1/1|0.0/1.0|0.3333333333333333|1|10203/372|
|GSON-1875|IssueLens-C|VALID|1/4|0.0/1.0|0.3333333333333333|1|7732/387|
|GSON-1908|IssueLens-C|VALID|0/17|0.0/0.0|0.0|1|7599/329|
|GSON-1931|IssueLens-C|VALID|1/3|1.0/1.0|1.0|1|8061/346|
|LANG-1655|IssueLens-C|VALID|1/1|1.0/1.0|1.0|1|6333/325|
|LANG-1659|IssueLens-C|VALID|0/7|0.0/0.0|0.0|1|8978/335|
|LANG-1662|IssueLens-C|VALID|1/2|0.0/1.0|0.5|1|9689/330|
|LANG-1669|IssueLens-C|VALID|0/1|0.0/0.0|0.0|1|7205/340|
|GSON-1870|BRD|VALID|1/1|0.0/1.0|0.3333333333333333|1|10410/392|
|GSON-1875|BRD|VALID|1/4|0.0/1.0|0.3333333333333333|1|7939/401|
|GSON-1908|BRD|VALID|0/17|0.0/0.0|0.0|1|7806/341|
|GSON-1931|BRD|VALID|1/3|1.0/1.0|1.0|1|8268/410|
|LANG-1655|BRD|VALID|1/1|1.0/1.0|1.0|1|6540/343|
|LANG-1659|BRD|VALID|0/7|0.0/0.0|0.0|1|9185/398|
|LANG-1662|BRD|VALID|1/2|0.0/1.0|0.5|1|9896/389|
|LANG-1669|BRD|VALID|0/1|0.0/0.0|0.0|1|7412/343|
|GSON-1870|Direct-v1|OUTPUT_PROTOCOL_FAILED|0/1|0.0/0.0|0.0|6|40160/881|
|GSON-1875|Direct-v1|OUTPUT_PROTOCOL_FAILED|0/4|0.0/0.0|0.0|3|9965/418|
|GSON-1908|Direct-v1|TOOL_OPERATION_BLOCKED|0/17|0.0/0.0|0.0|3|11967/181|
|GSON-1931|Direct-v1|TOOL_OPERATION_BLOCKED|0/3|0.0/0.0|0.0|5|18895/347|
|LANG-1655|Direct-v1|TOOL_OPERATION_BLOCKED|0/1|0.0/0.0|0.0|2|2035/115|
|LANG-1659|Direct-v1|OUTPUT_PROTOCOL_FAILED|0/7|0.0/0.0|0.0|4|26412/613|
|LANG-1662|Direct-v1|OUTPUT_PROTOCOL_FAILED|0/2|0.0/0.0|0.0|3|2631/110|
|LANG-1669|Direct-v1|TOOL_OPERATION_BLOCKED|0/1|0.0/0.0|0.0|2|6470/75|
|GSON-1870|Direct-v2|TOOL_OUTPUT_PROTOCOL_FAILED|0/1|0.0/0.0|0.0|2|4195/186|
|GSON-1875|Direct-v2|TOOL_OUTPUT_PROTOCOL_FAILED|0/4|0.0/0.0|0.0|2|5313/244|
|GSON-1908|Direct-v2|TOOL_OUTPUT_PROTOCOL_FAILED|0/17|0.0/0.0|0.0|3|11141/244|
|GSON-1931|Direct-v2|VALID|1/3|1.0/1.0|1.0|3|11087/485|
|LANG-1655|Direct-v2|VALID|1/1|1.0/1.0|1.0|7|63245/901|
|LANG-1659|Direct-v2|VALID|2/7|1.0/1.0|1.0|5|43848/772|
|LANG-1662|Direct-v2|TOOL_RANGE_EXCEEDED|0/2|0.0/0.0|0.0|5|34457/410|
|LANG-1669|Direct-v2|TOOL_RANGE_EXCEEDED|0/1|0.0/0.0|0.0|2|7654/75|

## 有效性与比赛报告边界

结果可作为8例冻结真实案例的描述性结果写入比赛报告，必须同时披露协议成功率、完整36方法分母、4个匿名方法未入候选目录、5项原始身份异常及有效子集选择。Direct v1和v2单独列出；v2为POST_FAILURE_PROTOCOL_RELIABILITY_SUPPLEMENT，不能视作全新盲测或覆盖v1。
C/BRD同一Top20及源码已经逐字节验证；Direct使用完整输出空间但交互预算不同，不能当作等算力因果比较。不同运行时间不能证明相同不可变后端；BACKEND_REVISION_UNATTESTED与PROVIDER_TOKEN_PARITY_UNVERIFIED持续成立。无统计显著性或全面泛化主张，非OS隔离/严格盲测。
该结果使用source-occurrence兼容目录，不能冒充原始严格java-source-v2通过。封存保证本地文件哈希一致，不等同于外部签名或第三方审计。所有旧资产在评分后再次哈希核验，保留原封存。

## 报告字段校订（不重新评分）

保留原始评分封存`c3d137bed09f790343c5ac16545a918e2a04d9579898dd0481ace217c37204d7`。本版本仅将三个有效Direct-v2输出的模型理由移至model_explanation_unverified，failure_reason置空；所有指标、排名、映射、Token用量及失败记录保持不变。模型理由未经事实认证，不是失败原因或人工GT结论。
