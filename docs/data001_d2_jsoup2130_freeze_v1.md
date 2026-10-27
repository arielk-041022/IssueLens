# DATA 001-D2：jsoup #2130 GT 冻结 v1

## 授权与状态

本记录由 Codex 编录。授权来源为本聊天 DATA 001-D2 用户指令：

> DATA 001-D1 审核报告已由研究负责人审阅，并批准 jsoup #2130 的单方法定位目标进入正式冻结步骤。

研究负责人姓名、线下审核时间及独立签名未提供，不虚构第三方审核人。
2026-10-21 是本指令的批准记录日期，不声称是线下 D1 审核的确切日期。
D1 是本聊天中的 AI 辅助只读审核，不是第三方人工实验或本地红绿验证。

目标批准已获得，因此 Sidecar 使用 CONFIRMED/FROZEN；冻结产物自身状态仍为
PENDING_ARTIFACT_ACCEPTANCE。此状态不等于已获定位实验授权。

## 任务与唯一目标

- PRE_FIX_EXISTING_METHOD_LOCALIZATION
- repo_id: jhy/jsoup
- source_snapshot_id: sha256:dfa01854c9992baa5dd7aa7dc7eefc1f4cc49eb03389569113b6a5327ea7c74b
- file_path: src/main/java/org/jsoup/select/Evaluator.java
- method_id: java-source-v1:org.jsoup.select.Evaluator.IsEmpty#matches(Element,Element)
- 修复前: c3963d4bb19414d56f42986db96d62208309e748
- 修复: 6de7cfcd9dbe64297a2d430601f69d0b8005eece

任务指令中缩写的 `sha256` 和 `java-source-v1.jsoup...` 不是新的身份，使用 D1 审核的上述完整值。
accepted_targets 只有该方法，accepted_files 只有该文件。ANY_CONFIRMED_TARGET 只表示
找到应优先检查的方法，不表示完整修复或唯一根因；required_groups 为空。
ROOT_CAUSE_CONFIRMED=false，未独立复现原始报告中的 28 个错误匹配。

## 定位输入契约：TITLE_LF_LF_ORIGINAL_BODY_V1

输入精确为 title + 两个 LF + 原始 body。没有额外结尾换行；body 自身的 CRLF
保持不变，不做 strip、换行归一化、翻译、摘要或关键词补写。
标题为 `:empty selector bug [1.17.2]`。
原始 body 以独立 JSON 字符串完整保存在新 benchmark 的 issue_input.original_body。
将该字符串按 UTF-8 编码即可恢复归档的全部 547 字节。
issue_text 是唯一正式定位查询字段；sidecar、其他元数据及本文不允许作为模型输入。
不输入评论、修复 SHA/信息、补丁、测试、方法身份、GT 或本审核解释。
本次组合输入是新版本契约，不宣称先前开发实验已经使用相同输入。

正文来源：DATA001-C/evidence/original/issue.body.from-data001.txt；对应 JSON original_issue_text
逐字节相等。归档时间为 2026-10-19T08:26:52.735290+00:00，不保证是 Issue 创建时版本。
原始 HTML 文件及其哈希只作来源，不将其中评论或修复信息提取为输入。

## 审核依据及边界

封存 fix.patch 第 4–10 行的提交信息明确 Fixes #2130。
fix.diff 第 18–51 行修改 IsEmpty.matches：空白 TextNode 不再提前返回 true。
修复前 Evaluator.java 第 720–730 行是唯一目标，范围含注解；方法声明第 721 行。
修复后 SelectorTest.java 第 1178–1196 行新增 emptyPseudo；id=6 是空白文本后接元素。
第 23–29 行辅助函数检查匹配数量和全部 ID。此处审阅测试源码，没有执行上游测试。
IsEmpty.toString 仅格式变化，Selector.java 仅 Javadoc，CHANGES.md 是说明；
新增测试没有修复前生产方法身份，不纳入 GT。
证据支持 PATCHED_METHOD 和经负责人批准的 ACCEPTED_LOCALIZATION_TARGET，
不授予 ROOT_CAUSE_CONFIRMED。索引 Validator PASS 不证明故障根因。

Sidecar 的 basis 记录补丁、回归测试、冻结正文、已验收核验报告和本批准说明的
确切哈希及来源。Git 原始提交对象和完整源文件保存在既有 DATA001-C 目录中。
文件哈希是一致性绑定，不是审批人数字签名。

## 跨案例关联与发布限制

关联组 jsoup-2130-2137-parent-fix-dependency：#2130 的修复前提交就是 #2137 的修复提交。
同组不得跨开发集和独立测试集。split_assignment=UNASSIGNED，由负责人另行决定；
不因本次冻结自动把 #2130 指派给开发集。
提交写有 Regressed by #1976，作为维护者声明记录，不声称独立核实引入历史。
泄漏等级沿用 L0（仅标题/正文未直接披露内部目标），仍有 :empty 功能提示，且不能
保证不存在模型预训练污染。补丁与测试只用于审核。

## 结构兼容和最小验收

benchmark 是现有协议要求的数组；sidecar 仍为 schema_version=1.0、strict-localization-v1，
通过 benchmark_sha256 绑定完整输入文件字节。source_manifest 使用 TASK003 规范排序和序列化。
不修改现有六条 benchmark 或 ground_truth.strict.v1.json。JSON 结构以现有
strict_evaluation.read_json / validate_gt_sidecar 为权威检查，不引入另一套 GT Schema。
扩展 provenance、approval、input_contract 和关联字段是说明性字段；现有 audit 不强制
检查其全部语义，需结合独立冻结核对和人工验收。audit 模式不调用检索或读取排名。
预期分母 total=1/method=1/file=1，ranking_status=NOT_REQUESTED，定位指标为空。
GT 目标通过结构审核不表示这些指标已经测量，开发/测试划分及产物验收仍阻止发布实验。

## 路径和恢复

新增 data/real_issues.jsoup2130.v1.json、data/ground_truth.jsoup2130.strict.v1.json 和本文。
源快照、索引和部分依据目前引用 Windows 临时目录；移机时必须按哈希重新绑定，不得
仅修改 source_snapshot_id。将来外部材料长期归档须另行批准，不能原地改写 v1。
本任务无需恢复历史文件。若拒绝产物，保留当前文件和日志，用后续明确版本修正，
不覆盖失败记录、旧 benchmark、旧 sidecar 或封存输入。