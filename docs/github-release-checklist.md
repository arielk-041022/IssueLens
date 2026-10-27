# GitHub 发布检查表

检查日期：2026-10-27。**结论：安全整理已完成，尚不应直接将整个本地目录上传。** 未提交、未推送、未创建GitHub仓库，未删除／移动历史资产；主CLI、算法、Prompt、GT和身份协议未改。

## 教师要求与交付状态

按《issuelens分析改进(1).docx》开源交付意见核对：README需说明环境、依赖、索引与真实运行步骤，提供无实时API演示，核对第三方许可、项目许可证及AI辅助开发情况。

|项目|状态|结果|
|---|---|---|
|根目录Git状态／历史|已初始化空仓库|本轮经授权核对项目及父目录均无仓库后git init；此前没有Git历史可检查，未覆盖旧仓库|
|README、安装、真实参数|已补充|README.md；两个Python CLI的--help实跑退出0；Java Main帮助实跑退出0|
|最小依赖|已补充|requirements.txt固定jieba 0.42.1；Python 3.10+；JDK17+；POM固定JavaParser/Jackson/JUnit等版本|
|本机环境|已核对|Python3.10.11、JDK25、Maven3.9.16；没有自动下载依赖|
|实际离线演示|通过|LANG-1610，3213方法索引，--no-llm，退出0；5个局部PASS、整仓FAIL；未执行目标仓库Maven测试，未调用模型|
|Python必要测试|通过但保留环境记录|9套共210个测试：沙箱首轮209通过、1项Windows链接创建权限失败；同一失败项获准在沙箱外单独复跑通过。不是一次全绿运行，也不是全仓测试|
|Java测试|通过|Maven -o，54项（MainTest38、MethodIdentityV2Test16），0失败／错误／跳过；构建输出在被忽略的output目录，不改原target|
|最小公开文件集冒烟测试|通过|只复制白名单文件到忽略目录中的临时树，不复制私有数据；标题／正文CLI17项、Replay6项、BM25合成55项，共78项通过|
|第三方许可|已核对主要依赖|见third-party-and-ai.md；未复制上游仓库或运行时。完整实验包仍需逐项再分发审查|
|项目LICENSE|已确认Apache-2.0|维护者确认代码公开权利；LICENSE为Apache官方完整原文，未改正文|
|AI使用披露|已补充初稿|范围与责任说明已写；团队实际贡献、署名仍需确认|
|GitHub地址|待用户决定|没有虚构地址，也没有创建仓库|
|报告、截图、视频|未修改|排除默认上传，继续由原负责人维护|

## 文件分类与实际整理

保留核心：`src/`、`java-indexer/src/`、`java-indexer/pom.xml`、`tests/`和必要协议。保留旧源码备份，不为目录美观删文件。新增根README、requirements、.gitignore、examples/lang1610/README.md、实验导航及许可／AI说明。

`.gitignore` 默认排除：缓存、target、虚拟环境、压缩包、output、队友报告、私有GT／筛选目录、仓库副本及绝大部分原始实验。仅放行核心测试所需两个data协议、结构选择器依赖脚本以及少量公开结果、证据卡、故障注入和失败说明。上一轮规则用独立临时Git元数据核对；当时临时元数据仅位于 `output/github-release-preflight-20261027/ignore-check/`，当时项目根目录没有被初始化，也未执行add、rm、commit、reset、clean或push；本轮用户另行授权的初始化及暂存见文末。

**不能把忽略规则当成删除或脱敏。** 若用户之后恢复原.git或从其他仓库取得已跟踪文件，.gitignore不会移除它们；应重新检查实际暂存内容，不得直接git add整个历史目录。发布可用核心与公开阅读集，不等于发布全部冻结实验包。

## 实验与失败记录

入口见 [experiments.md](experiments.md)。主要8例／36GT对照与开发／事后实验分开；Direct协议失败、CEA全部REVIEW、UNBOUND、局部PASS／整仓FAIL等均保留。

原manifest完整保留，但白名单公开材料只是阅读集；某些manifest仍引用不默认发布的原始输入、GT及本机源码。GitHub上缺少这些文件时只能核对可用文件，不能宣传完整包封存闭合。完整复现附件应在许可、隐私和文件大小确认后另行发布；不要更改原manifest迁就缺失文件。

## 安全范围与尚存风险

- 先审计目录元数据，未递归读取.git对象或大型源码仓库；清单统计8606个文件，54个重目录／私有目录被剪枝。初步UTF-8内容扫描4526文件、约63.9MiB，单文件上限1MiB，总上限64MiB，不能覆盖整个本地盘。
- 初步5处Token模式命中均已核实为JSON键 `skip_blocked_arm_without_replacement` 的误报；没有输出任何密钥原文。没有发现敏感扩展名文件，但被剪枝目录／未扫描大文件并未获得无密钥证明。
- 扫描发现多份本机绝对路径：旧data、旧协议文档、演示日志及实验绑定。这些字段既有可移植性问题，也可能含用户名。冻结材料不就地脱敏；若必须发布原件，需用户确认公开范围，或在另外的明确标记副本中脱敏并保留原哈希。
- 不公开上游Commons Lang/Gson完整仓库、Git对象、缓存Tokenizer／venv及原始请求／余额／费用记录。这里只给出排除／附件建议，没有转移或删除大文件。剪枝目录没有完整体积统计，不声称仓库已达到任何上传大小上限。
- 由于没有原Git历史，无法确认是否曾提交密钥；如用户有其他.git备份，应单独检查，发现泄漏需先吊销／轮换。本次不重写历史。
- 新安装、首次clone及冷缓存Maven完整流程未联网重跑；已验证的是现有环境的CLI、测试和本机真实离线案例，不能把本机成功当作任意机器开箱即跑保证。

上一轮白名单检查为135个候选公开文件，未发现明显密钥模式；当时9份旧文件仍含本机路径。其后处理见下一节，不再把这9份全部视为待公开文件。116个受保护的源码／测试／关键报告文件与整理前一致；6份关键manifest的567个文件引用全部核验通过（包含证据卡、Trace、瓶颈分析、故障注入、8例评分与截图封存）。该核验范围不是所有历史实验全量认证。

## 最后一轮发布范围复核（2026-10-27）

原9份文件逐一处理如下。只修改没有已发现封存绑定的公开说明文档中的个人路径及占位说明；查阅现有346份小型封存／哈希元数据后，严格评分协议另发现明确manifest引用，未改原件。没有修改任何算法、索引、GT、实验结果、哈希记录或原始日志。

|原文件|分类|处理|
|---|---|---|
|docs/evidence_validation.md|公开技术说明|个人路径改为C:\path\to占位；原核验语义、历史指标及原始日志不改|
|docs/index_schema_v2.md|公开技术说明|同上；不改索引schema或实现|
|docs/baseline_producers.exp001_b3.v1.md|历史实验／验收记录|保守保留原件，加入.gitignore，不默认发布|
|docs/bm25_protocol.exp001_b2.v1.md|冻结实验协议|原件不改，排除默认发布|
|docs/data001_d6_jsoup2068_freeze_v1.md|冻结GT来源记录|原件不改，排除默认发布|
|docs/data001_d6_jsoup2078_freeze_v1.md|冻结GT来源记录|原件不改，排除默认发布|
|docs/experiment_protocol.exp001_b1.v1.md|历史实验协议|原件不改，排除默认发布|
|docs/strict_evaluation_protocol.md|冻结评分协议（有manifest引用）|原件不改，排除默认发布|
|issuelens_v2_offline_check.py|其他：本机旧检查脚本|不改代码，只排除默认发布|

另外发现一份此前扫描未识别的双反斜杠路径：`docs/case-002-processUpdateOwnerForm-id-mismatch.md`。作为旧案例记录保留原件并排除默认发布，不扩展为新一轮项目整理。

### 计划发布清单与忽略规则

机器可读清单：[github-release-files.json](github-release-files.json)，共 **129个文件（原128文件加LICENSE，包含清单自身）**：

|类别|数量|
|---|---:|
|Python核心源码与现有离线入口|41|
|Java索引器源码、测试与POM|5|
|Python测试|30|
|README与安装配置|4|
|许可证|1|
|真实离线演示说明|1|
|合成测试所需公开协议|2|
|实验汇总、证据与失败阅读材料|18|
|技术文档及发布清单|14|
|已有离线辅助脚本|13|

复用上一轮临时Git元数据，仅执行只读check-ignore；上一轮未执行git init/add/commit/push；本轮明确获准初始化与白名单暂存，仍禁止commit/push。129个计划文件均须经.gitignore和暂存清单一致性核对，核心CLI、Java POM/源码、测试、两个data协议及结构选择器依赖脚本均保留。对计划文件重新检查普通及JSON转义的个人目录路径、明显Token和私钥模式，未再检出；这不是全部历史目录或Git历史无密钥证明。

### 离线演示适用性

公开演示现在明确不使用未发布的旧target索引、冻结case.json或历史issue-input.txt。程序、依赖声明、索引器源码/POM及原始标题命令均在当前公开文件集合内。使用者需先主动取得公开Commons Lang指定修复前版本并生成新索引；依赖和源码准备完成后，定位命令通过--no-llm离线运行，不需要GT或模型API。

只复制计划文件的隔离副本中，使用已有公开修复前源码及其既有索引作为外部前提运行同一标题CLI，退出0、Replay完成、明确跳过LLM。没有重新建索引、修改旧索引或下载源码。首次clone、冷缓存安装与建索引完整链未重跑，不能声称下载本项目后无需任何准备即可全离线执行。部分历史测试仍需未公开的冻结资产，该限制继续保留。

### 不直接上传的具体材料

- 仓库／Git对象副本：`experiments/accuracy-recovery-20261023/official-commons-lang.git`、`experiments/class-aware-source-recovery-v1/commons-lang.git`、`experiments/fair-comparison-v1.1-admission-v1/work/`、`experiments/independent-eval-v1/upstream/`等已有工作副本；未逐库测体积，不声称已检查全部Git对象。
- 生成物／重复打包：`java-indexer/target/`、`output/`、虚拟环境、Tokenizer资产、`experiments/report-screenshots-v1.zip`（约4.46MiB）、`IssueLens_CEA_v0.1.zip`及其他压缩包。压缩包被排除不等于超过GitHub大小上限；本轮没有删除或搬移它们。
- 敏感／授权未确认：.env、密钥和Token文件、原始API请求响应及余额账单记录、个人路径历史记录、私有GT／筛选记录、修复diff、上游完整源码、未经再分发授权确认的Issue正文及抓取数据。原始冻结包继续本地保存；公开摘要不能替代完整数据授权与依赖闭合。
- 队友维护的报告、截图和视频不在本轮上传清单，也未修改。

许可证已由维护者确认采用 **Apache-2.0**，全部拟发布代码公开权利已确认；署名／AI披露细节和未纳入本次白名单的完整数据再分发授权仍由维护者核对。

## 验收与本地日志

本次检查日志和只读哈希记录保存在被忽略的 `output/github-release-preflight-20261027/`。包括源文件保护清单、目录审计、敏感模式检查、Python/Java测试、真实demo命令与stdout/stderr、忽略规则检查、链接与最终完整性结果。不要将本机原始日志作为公开包直接上传。

可复跑的低成本测试命令在根README中；一次性冻结实验及评分脚本不应为发布验收重跑。所有新增发布文档只做导航与说明，不重算准确率。

## 上传前必须由维护者决定

1. 代码公开权利与Apache-2.0已获用户明确确认；维护者继续核对团队署名与AI披露细节。
2. 确认GitHub仓库名称／地址，以及旧.git是否需要恢复；本轮不代替用户执行。
3. 当前白名单已无检出的个人目录路径；若以后扩大到被排除的原始记录，需再次确认个人路径和隐私公开范围，不能认为整盘已脱敏。
4. 决定完整实验包的第三方授权、GT解封范围、隐私、体积和附件托管方式；保留负面结果，不能为缩减体积删除反例。

完成以上决定后，先检查真实待发布文件清单及暂存差异，再上传。**当前状态：129文件白名单与Apache-2.0许可已确定；本轮只准备本地暂存，首次commit和GitHub发布必须由用户另行授权。未纳入清单的数据包授权不影响本次仅代码与公开摘要的范围。**

## 首次暂存准备

2026-10-27：用户明确授权Apache-2.0、确认全部拟发布代码的公开发布权利，并允许git init及逐文件暂存；没有授权commit、push或创建远程。LICENSE直接下载自Apache官方HTTPS来源，11,358字节，SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`。README及第三方说明已更新，未批量添加源码文件头。当前白名单未分发依赖二进制或第三方完整实现，未发现需另存的第三方NOTICE条目，因此不编造NOTICE或版权持有人。将来扩大分发范围时重新核对。

已按更新白名单逐个git add，共129文件；没有使用git add .或git add -A。git diff --cached --name-only与--stat已保存，并对Git暂存blob逐项检查精确路径、原始字节、许可原文、明显密钥、个人目录路径和文件大小；仅在与白名单完全一致时保留本轮完成状态。检查日志位于被忽略的output/github-first-stage-20261027。

空白检查没有冒充全绿：原始字节不归一化，git diff --cached --check返回2，主要是CRLF。仅作为诊断追加cr-at-eol后仍有2处既有new blank line at EOF：docs/case-004-tcs-find-by-name.md与src/retrieval_reranking_bottleneck.py。未修改任何源码、旧案例或冻结文件，也未修改Git默认配置来隐藏告警。这属于上一轮检查记录。最终验收时，仅普通、未发现封存绑定的docs/case-004-tcs-find-by-name.md第144行多余CRLF被移除，其他字节未变。src/retrieval_reranking_bottleneck.py第251行属于受保护源码，并被实验source-hashes.json绑定，保留原件。该既有末尾空行记录为可接受的格式告警，不以修改算法或冻结文件换取全绿。

必要离线Replay回归本轮6/6通过；源码未变，原Python210项、Java54项及公开集78项验收记录继续保留，未重复运行全套或新增模型API。上游完整数据包不在白名单，不对其再分发授权作扩大解释；团队署名和AI贡献细节仍可由维护者继续完善。Git仓库没有commit和remote，本轮不会进入自动提交。

## 首次提交前最终验收

两处EOF告警均与发布整理前副本一致，并非本轮引入。普通文档仅移除末尾一个额外CRLF；受保护源码原件及哈希不变。白名单仍为129个路径，内容修正不增加或移除发布文件。Windows CRLF按单次诊断参数识别后，只应剩src/retrieval_reranking_bottleneck.py:251的既有new blank line at EOF；Git默认空白验证未被全局禁用，默认check仍可能因原始CRLF返回非零，不能声称零告警。按保留该冻结格式告警的原则，可进入用户手动首次Commit阶段，本轮不执行commit/push或配置remote。
