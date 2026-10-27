# CLI 原始 Issue 输入

现有 `issue_text` 检索流程本来就接受多行完整文本。本次仅补充 CLI 输入和来源记录，不改 Rule/BM25、候选权重、模型 Prompt、Evidence Replay 步骤、source evidence validator 或验证命令。没有准确率提升承诺；三例 Input Robustness 结果属于小样本探索性结果。

以下两入口支持相同参数：`python src/evidence_replay.py`（交互展示）与 `python src/retriever.py`（规则检索）。`llm_reranker.py` 的旧 benchmark 批评测接口保持原样。

## 输入方式

```powershell
# 仅标题
python src/evidence_replay.py --repo C:\path\to\pre-fix-repo --issue-title "Example request returns an empty result" --no-llm --auto 0

# 标题 + 原始正文（正文中的换行可使用 PowerShell here-string）
python src/evidence_replay.py --repo C:\path\to\pre-fix-repo --issue-title "Example request returns an empty result" --issue-body "Steps: submit an empty list. Expected: an explicit response." --no-llm --auto 0

# 标题 + 正文文件
python src/evidence_replay.py --repo C:\path\to\pre-fix-repo --issue-title "Example request returns an empty result" --issue-body-file C:\inputs\body.txt --no-llm --auto 0

# 完整原始文本文件（自己在文件中保留标题、正文及其原始分隔）
python src/evidence_replay.py --repo C:\path\to\pre-fix-repo --issue-file C:\inputs\issue.txt --issue-url https://example.org/issues/demo --issue-record C:\outputs\demo-input.json --no-llm --auto 0

# 同样适用于规则检索入口，无网络调用
python src/retriever.py --repo C:\path\to\pre-fix-repo --issue-file C:\inputs\issue.txt --issue-record C:\outputs\rule-input.json
```

直接输入需要 `--repo`，默认复用其 `index.json`，不自动索引。可用 `--index` 指向已有索引。直接输入不读取默认 benchmark，不能与 `--id` 混用；不要同时指定 benchmark。示例为虚构 Issue，不依赖任何真实 GT。

`--issue-title` 与 `--issue-file` 互斥。`--issue-body` 与 `--issue-body-file` 互斥，且仅能配合标题。标题加非空正文按原样 `title + "\n\n" + body` 组成检索输入；空正文退化为标题，原始空正文仍记录。纯空白正文不会自动删去。完整文件不拆标题、解析 JSON/Markdown、转换换行或删除 BOM；按严格 UTF-8 解码，非法编码报错。只用 strip 检查整个输入是否为空，不修改实际检索文本。

## 来源与原文

CLI 输出输入来源、UTF-8 字节数和 SHA-256。`--issue-record` 可将完整 `issue_text`、原始标题/正文、来源文件/参数、输入内容 SHA-256 和文件原始字节 SHA-256 保存为 JSON。使用 exclusive create，已有记录不覆盖；拒绝写入本项目 `experiments/`（包括解析后的链接目标）。URL仅作为用户来源说明保存，明确标记未联网核验，不自动访问 GitHub/Jira。

原始 benchmark JSON 不修改。旧命令仍可用，额外打印其来源文件 SHA-256、case ID 和所用 `issue_text` SHA-256。即使 benchmark 有其他字段，继续只使用原有 `issue_text`，不会自动改变历史输入构造规则。

默认完整输入上限 **200,000 UTF-8 字节**，包括标题、两个LF分隔、正文中所有日志与代码块；不按字符计数、不做关键词预筛或删除代码块。文件仅有界读取上限+1字节以判断超限，任何超限都会报 `ISSUE_INPUT_TOO_LARGE` 并在读取索引/检索/模型调用之前退出。需要更大文本时显式指定正整数 `--max-issue-bytes N`。该边界不是模型 Token 预算保证；开启已有 LLM 路径时仍沿用原有 provider 行为。本次开发测试全部禁止网络，不调用模型。

检索使用完整输入。规则 CLI 原有200字符显示预览现在明确标记“显示预览；检索使用完整原文”，不应误认为检索截断。Evidence Replay 的原有分步展示、`test_command` 展示、`--evidence-protocol evidence-local-v2` 及 pinned index/metadata 认证参数维持原样。要避免模型调用，请显式使用 `--no-llm`；`--dry-run` 的原有模拟重排语义未改变。

## 旧接口与测试

```powershell
python src/evidence_replay.py --benchmark data\benchmark.json --id EXISTING_ID --no-llm --auto 0
python src/retriever.py --benchmark data\benchmark.json --id EXISTING_ID --index C:\path\to\index.json

python -m unittest discover -s tests -p test_issue_input_cli.py -v
python -m unittest discover -s tests -p test_evidence_replay_local.py -v
python -m unittest discover -s tests -p test_evidence_validator.py -q
```

新增17项端到端/集成测试仅使用合成索引、虚构文本与临时 benchmark。子进程禁用网络，覆盖UTF-8 BOM/CRLF保真、正文文件、空白正文、精确字节边界、非法编码、两个CLI旧接口和验证命令展示、记录拒绝覆盖及冻结实验目录保护；并确认完整正文进入原有 scorer。另6项Replay测试、74项认证器回归通过。Windows链接保护测试需具备创建symlink或junction权限，不能因权限不足跳过或放宽验证。
