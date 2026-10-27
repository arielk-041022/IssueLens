# Retrieval–Reranking Bottleneck Analysis v1

EXPLORATORY_POST_HOC；不运行检索或模型。

检索遗漏＝GT−池内GT；池内未入选＝池内GT−实际命中；容量下限＝max(池内GT−5,0)；可改善重排差距＝min(池内GT,5)−实际命中。
后三者中，池内未入选包含容量限制，不能再次相加。数量分解不指定具体哪个遗漏方法“属于容量”。

## 完整离线排名的候选覆盖

|案例|GT|BM25 Top-50|Hierarchical Top-50|
|---|---:|---:|---:|
|LANG-1743|N/A|N/A|N/A|
|LANG-1745|1|1|1|
|LANG-1747|N/A|N/A|N/A|
|LANG-1750|20|3|7|
|LANG-1753|2|2|2|
|LANG-1757|1|1|1|
|LANG-1759|1|1|1|
|LANG-1761|N/A|N/A|N/A|

可靠GT全5例：BM25 8/25；Hierarchical 12/25。

## 真实LLM执行分组

|案例|组|状态|GT|实际入池GT|Top-5命中|理论上限|差距|检索遗漏|池内未选|容量下限|
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
|LANG-1743|A|BLOCKED|N/A|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1743|B|BLOCKED|N/A|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1745|A|BLOCKED|1|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1745|B|BLOCKED|1|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1747|A|VALID|N/A|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1747|B|VALID|N/A|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1750|A|VALID|20|3|2|3|1|17|1|0|
|LANG-1750|B|VALID|20|7|2|5|3|13|5|2|
|LANG-1753|A|BLOCKED|2|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1753|B|BLOCKED|2|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1757|A|VALID|1|1|1|1|0|0|0|0|
|LANG-1757|B|VALID|1|1|1|1|0|0|0|0|
|LANG-1759|A|VALID|1|1|1|1|0|0|0|0|
|LANG-1759|B|VALID|1|1|1|1|0|0|0|0|
|LANG-1761|A|BLOCKED|N/A|N/A|N/A|N/A|N/A|N/A|N/A|N/A|
|LANG-1761|B|NOT_EXECUTED_OUTSIDE_LIVE_SCOPE|N/A|N/A|N/A|N/A|N/A|N/A|N/A|N/A|

A＝BM25 Top-50；B＝Hierarchical Top-50。BLOCKED的预览候选不冒充实际入池；协议失败不产生有效Top-5。

## 三项发现

1. 实际配对3例：A覆盖5/22、B覆盖9/22，实际Top-5均4/22。
2. A检索遗漏17、可改善重排差距1、容量下限0；B分别13、3、2。
3. 另两条可靠GT案例因源码映射BLOCKED；三条未知GT均保留，不能扩充模型评价分母。

## 可用于技术报告第6章的结论

在已公开GT的回顾性案例中，3条两组均协议有效的案例共22个GT。BM25与Hierarchical Top-50分别覆盖5、9个GT，检索遗漏分别为17、13个。最终Top-5各命中4、4个GT；按各Issue分别取min(池内GT,5)后求和，理论上限为5、7，可改善差距为1、3。Hierarchical池内未入选的5个GT中，2个属于Top-5不可避免的容量下限，不能全部归咎于模型。这表明当前检索仍遗漏大量方法，扩充候选覆盖也未自动转化为最终定位收益。结论仅限完整源码准入子集，不是独立盲测或因果证明；源码映射阻塞与未知GT案例另列N/A。

## 复核与边界

完整method_id保留类和签名，先复用旧评分器精确GT/source绑定，再核对冻结occurrence映射；不使用名称模糊匹配。
原输入、快照、候选集合与状态逐组保存在details.json，源文件路径与SHA-256在source-hashes.json。
不同Issue不被视为相同快照；只有各自A/B的Issue、快照和格式一致时才进入配对汇总。所有原实验保持不变。
