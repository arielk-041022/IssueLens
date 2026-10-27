# IssueLens

IssueLens 根据真实 Java Issue 的标题、正文和修复前源码索引，检索方法级候选，可选地通过 LLM 重排，并展示可追溯的源码证据及核验状态。它不自动修复 Bug，不保证找到全部真实修改方法，也不把源码事实匹配当成 Bug 根因证明。

**许可证：[Apache License 2.0](LICENSE)（Apache-2.0）。** 维护者已确认拥有全部拟发布代码的公开发布权利。正式GitHub地址与完整实验包的再分发范围另行确认；本次只准备本地首次提交，不自动提交或推送。请先阅读 [发布检查表](docs/github-release-checklist.md)。

## 能力与入口

- `src/evidence_replay.py`：实际交互 CLI，读取已有 Java 索引 → Rule 候选 → 可选 LLM 重排 → 验证命令展示；显式启用 `evidence-local-v2` 时增加局部源码证据核验。
- `src/retriever.py`：规则检索 CLI，支持完整 Issue 输入。
- `src/bm25_baseline.py` / `src/metadata_ranking_runner.py`：BM25 元数据评分／协议接口，**不是主 CLI 默认检索策略**。
- Evidence Trace、Markdown 证据卡、瓶颈分析、CEA、Hierarchical、Class-Aware 等有独立离线入口；不宣称它们已经成为主 CLI 默认输出。
- 原主 CLI 的 DeepSeek provider 配置是 `deepseek-chat`，不应与冻结实验中的 `deepseek-flash` 配置混淆。无 API 演示必须传 `--no-llm`；旧 `--dry-run` 会模拟重排，不能作为真实模型结果。

## 目录

```text
src/                    核心 Python 代码与独立离线入口
java-indexer/pom.xml     JavaParser 索引器构建配置
java-indexer/src/        Java 源码和 JUnit 测试
tests/                  Python unittest 测试
docs/                   协议、输入说明、发布检查表、实验导航
examples/lang1610/       真实离线演示步骤（不复制上游仓库）
experiments/            冻结实验；默认仅开放经筛选的阅读材料
data/                   仅公开测试所需的两个协议；旧 benchmark / GT 不默认发布
output/                 本地生成物，不发布
reports/                队友维护的报告，不在本次整理中修改
requirements.txt        CLI 最小 Python 依赖
README_CEA.md            独立 CEA 工具旧说明
```

`.gitignore` 只控制未来未跟踪文件；不会移除已经跟踪的文件，也不会删除本地数据。公开摘要不是完整冻结包，原 manifest 引用的部分原始输入仍在本地，不能宣称 GitHub 摘要集足以复跑全部评测。

## 环境与安装

Python **3.10+**；JDK **17+**（POM source/target=17）；Maven **3.9.x**。本机核验为 Python 3.10.11、JDK 25、Maven 3.9.16。兼容下限来自语法／构建配置，不表示所有版本组合都测试过。核心 Python CLI 只需第三方依赖 `jieba==0.42.1`，模型 HTTP 调用使用标准库；实验 SDK、Tokenizer、绘图工具不属于最小安装。

在项目根目录执行：

```powershell
python -m venv .venv
# 不要求激活 PowerShell 脚本；不修改 ExecutionPolicy
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B src/evidence_replay.py --help
.\.venv\Scripts\python.exe -B src/retriever.py --help
java -version
mvn -version
```

安装依赖、首次 Maven 构建和首次取得上游源码需要网络；**定位演示运行本身不需要模型 API**。本次整理未自动安装／下载依赖。确认 `JAVA_HOME` 指向 JDK；本机 PATH 中 Oracle `javapath` 曾无响应，直接使用 `$env:JAVA_HOME\bin\java.exe` 可正常运行。

## 生成 Java 源码索引

`$repo` 必须是修复前仓库根目录，包含 `src/main/java`；输出必须是新路径，索引及 `.meta.json` 成对生成，不覆盖已有文件。

```powershell
$repo = (Resolve-Path 'C:\path\to\pre-fix-repo').Path
$index = Join-Path (Get-Location) 'output\my-case\index.json'
mvn -f java-indexer/pom.xml compile exec:java "-Dexec.args=$repo --output $index"
```

路径带空格时，为 Main 参数内部也加引号：

```powershell
mvn -f java-indexer/pom.xml compile exec:java "-Dexec.args=`"$repo`" --output `"$index`""
```

上面生成原 `java-source-v1` 索引，适合已有 Evidence Replay 认证协议。独立严格 v2 入口存在，但身份协议不能互换：

```powershell
$v2index = Join-Path (Get-Location) 'output\my-case\index-v2.json'
mvn -f java-indexer/pom.xml compile exec:java "-Dexec.args=`"$repo`" --identity-v2 --output `"$v2index`""
```

v2 生成 `3.0/java-source-v2`，身份不可用／冲突应保留并按相应严格协议阻塞；不能将 v2 或 occurrence ID 传给旧认证器冒充认证通过。普通索引器要求单一 `src/main/java`；多源码根的实验适配不是其默认 CLI 能力。

## 实际 CLI 使用

```powershell
# 仅标题；$repo/$index 使用上一步值
python -B src/evidence_replay.py --repo "$repo" --index "$index" --issue-title 'Example request returns an empty result' --topk 5 --no-llm --auto 0
# 标题与原始正文
python -B src/evidence_replay.py --repo "$repo" --index "$index" --issue-title 'Example request returns an empty result' --issue-body 'Steps: submit an empty list. Expected: an explicit response.' --no-llm --auto 0
# 完整 UTF-8 原文文件及可选来源记录；输出记录必须是新文件
python -B src/evidence_replay.py --repo "$repo" --index "$index" --issue-file 'C:\inputs\issue.txt' --issue-record 'output\issue-input.json' --no-llm --auto 0
```

|参数|真实语义|
|---|---|
|`--repo` / `--index`|直接输入的源码根目录／已有索引；不自动建索引|
|`--issue-title` / `--issue-file`|标题或完整 UTF-8 文本，互斥|
|`--issue-body` / `--issue-body-file`|与标题配套的正文或正文文件，互斥|
|`--issue-url`|仅记录用户提供的来源，不联网验证|
|`--issue-record`|保存原文、来源和 SHA-256；拒绝覆盖及写入 experiments|
|`--max-issue-bytes`|默认 200,000 UTF-8 字节，超限报错，不静默截断；不是模型 Token 保证|
|`--no-llm`|完全跳过模型重排|
|`--auto 0`|不等待交互输入|
|`--benchmark` / `--id`|兼容旧 benchmark 模式；含本机路径的旧文件需用户自行准备，不是即取即跑数据集|

标题和非空正文按 `title + "\n\n" + body` 输入，不依据 GT 加词。更多边界见 [Issue 输入说明](docs/issue_input_cli.md)。启用已有模型路径需用户自行在进程环境设置 `DEEPSEEK_API_KEY`，不要写入文件、提交或截图；项目不会为开源发布任务调用模型。

## 无实时 API 的真实演示

见 [LANG-1610 演示](examples/lang1610/README.md)。公开文件集合包含运行程序和演示命令；上游源码由使用者主动取得，索引由公开Java索引器生成，旧本机索引不随包发布。首次取得源码和依赖需要网络，定位运行使用 `--no-llm`，不要求GT或实时API。已保存的 [证据卡](experiments/localization-evidence-card-v1/LANG-1610.md) 是历史 **BM25** 演示；普通主CLI默认 **Rule**，不能混写为同一次排序或最终LLM结果。

## 输出与可信边界

主 CLI 把候选排名、签名、路径、Rule 分数／证据及模型状态输出到终端；未指定输出参数时不会自动保存定位 JSON。`--issue-record` 保存原文及输入 SHA-256；索引 sidecar 保存索引哈希、schema、计数和身份状态。

已有索引为 v1 且哈希固定时可显式附加源码认证：

```powershell
$ih = (Get-FileHash -LiteralPath "$index" -Algorithm SHA256).Hash.ToLower()
$mh = (Get-FileHash -LiteralPath "$index.meta.json" -Algorithm SHA256).Hash.ToLower()
python -B src/evidence_replay.py --repo "$repo" --index "$index" --issue-file 'C:\inputs\issue.txt' --no-llm --auto 0 --evidence-protocol evidence-local-v2 --evidence-index-sha256 $ih --evidence-metadata-sha256 $mh --evidence-out 'output\new-evidence-report.json'
```

报告保留 `overall_verdict`、局部 verdict、`evidence_id`、方法身份、源码范围、`status` 与 `reason_code`。局部 PASS 不等于整仓 PASS；模型协议 VALID 不等于源码证据 VERIFIED；源码字节／范围／片段匹配不是独立 AST 语义、调用目标或 Bug 根因证明。打印测试命令不等于执行测试。认证器原接口见 [Evidence Validator](docs/evidence_validation.md)。

## 测试与冻结结果

```powershell
python -B -m unittest discover -s tests -p test_issue_input_cli.py -v
python -B -m unittest discover -s tests -p test_evidence_replay_local.py -v
python -B -m unittest discover -s tests -p test_evidence_validator.py -v
python -B -m unittest discover -s tests -p test_bm25_baseline.py -v
mvn -o -f java-indexer/pom.xml test
```

`mvn -o` 仅使用已有缓存；首次缺依赖会失败，不应偷偷下载。Windows 链接保护测试需创建 symlink/junction 的权限，不应改测试绕过。BM25 合成测试需要保留 `data/bm25_protocol.exp001_b2.v1.json`，本次已将其与基础实验协议加入公开白名单。部分历史集成测试依赖本机冻结资产；最小公开摘要集不保证全仓任意测试均可运行。不要为了复核阅读材料而重跑一次性预测或评分脚本。

[实验导航](docs/experiments.md) 区分主要8例对照、离线诊断、演示及失败记录。主要冻结评分：BM25 Hit@5=2/8、IssueLens-C/BRD=5/8，方法微召回分别2/36与5/36；Direct-v1有效0/8，Direct-v2有效3/8。协议失败、身份异常和目录缺失仍保留，不据此宣称纯定位能力全面优势。

## 局限、许可与 AI 使用

候选未召回时重排无法恢复；Top-5 对多 GT 有容量限制；标题信息不足、方法重载／匿名类及身份缺失会影响结果。CEA 的稳定性不是正确性，8例全部 REVIEW 尚未证明错误区分能力。Hierarchical、Structural、Class-Aware 和结构证据提示的探索性负面结果不能省略，也未默认接入主 CLI。

IssueLens拟发布代码采用 **Apache-2.0**，完整原文见 [LICENSE](LICENSE)。JavaParser、Jackson、jieba及上游Commons Lang/Gson源码保留各自许可义务，项目许可不重新授权第三方材料；详见 [第三方与AI披露](docs/third-party-and-ai.md)。本项目使用过AI辅助编程、文档及实验工具开发；具体贡献范围、人工审查责任和团队署名由维护者核对，不声称全部由人工独立完成。
