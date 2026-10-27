# LANG-1610 无模型 API 演示

这是已知开发案例的操作演示，不是新盲测或准确率评价。不分发完整第三方仓库、GT或修复 diff。

## 公开文件集合与运行前提

GitHub计划公开集包含本说明、核心CLI、requirements和索引器源码/POM；不包含本机旧 `target` 索引、冻结case.json或历史issue-input.txt。公开用户按下一节自行取得指定修复前源码并建索引，然后离线运行最后一条命令。标题直接使用已保存的原始标题，不需要任何私有输入或GT；单行标题不冒称含尾部CRLF的历史原文逐字复现。

不指定 `--evidence-out` 不会保存新认证文件。若要核验源码，按根README追加明确的evidence-local-v2与自己生成的索引双哈希参数。

## 从公开源码准备

首次获取源码需要联网；下面命令仅供用户主动执行，本轮整理不执行网络操作。原实验记录的修复前版本是 `bd8dbbe0be26cc687d67a7aafe54936ef6285da3`。使用新目录，不在已有仓库执行 checkout：

```powershell
git clone https://github.com/apache/commons-lang.git examples/lang1610/repo
git -C examples/lang1610/repo checkout --detach bd8dbbe0be26cc687d67a7aafe54936ef6285da3
$repo = (Resolve-Path examples/lang1610/repo).Path
$index = Join-Path (Get-Location) 'output\lang1610\index.json'
mvn -f java-indexer/pom.xml compile exec:java "-Dexec.args=`"$repo`" --output `"$index`""
python -B src/evidence_replay.py --repo "$repo" --index "$index" --issue-title 'StringUtils.unwrap throws StringIndexOutOfBoundsException' --topk 5 --no-llm --auto 0
```

源码／依赖取得并建索引后，最后一条命令离线运行，不调用 DeepSeek、不运行 Maven 测试。Main 拒绝覆盖索引；再次演示复用原索引，不重新构建覆盖。上游许可见其 `LICENSE.txt`、`NOTICE.txt`。新生成索引指纹未必等于历史封存索引，不能用历史预期哈希冒充通过。

首次 clone／安装流程本次没有联网验证；本机已有源码的真实 CLI 离线验收结果见发布检查表。主 CLI 是 Rule，证据卡是历史 BM25，候选排名可能不同。
