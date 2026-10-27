# 可复核性说明

本包仅新增demo.md、reproducibility.md、limitations.md、manifest.sha256。
原代码、原报告和冻结实验不变。SHA-256是内容指纹，不是OS只读权限、数字签名或第三方认证。
本轮只核验已有资产和读取已封存摘要；未重新读取GT评分、预测、调用API或运行算法。

## 上游封存核验

- `experiments/evidence-trace-v1`：6文件核验通过；manifest SHA-256 `4247e2ab57d6ae09d41dcb9545d97f411ea1a68214ed6758a03f86356a17b45d`。
- `experiments/cea-evaluation-20261025-v1`：76文件核验通过；manifest SHA-256 `54ba60b8dd20ff1358d939ae4cb2b2401b73b74cde3eac620ee278998675b71f`。
- `experiments/cea-evaluation-20261025-v1/stage-a-completion-v1`：53文件核验通过；manifest SHA-256 `b928bf9ca4608c5cd1ccd64ce56bc7ab104b4d5f93e4812281939def680cbd34`。

Evidence Trace的原始输入和代码指纹、CEA协议中的代码指纹均已逐项比对。
原验收记录为19项新增追溯测试、6项既有局部证据测试通过；本轮不冒充重新执行测试。
本包所需资产未发现缺失。

## 只读离线复核

在项目根目录执行以下PowerShell命令，仅计算哈希，不运行实验脚本：

```powershell
python -B -c "from pathlib import Path; import hashlib; r=Path.cwd().resolve(); p=r/'experiments/competition-evidence-pack-v1'; rows=[x.split('  ',1) for x in (p/'manifest.sha256').read_text(encoding='utf-8').splitlines() if x and not x.startswith('#')]; assert all((p/n).resolve().is_relative_to(r) and hashlib.sha256((p/n).read_bytes()).hexdigest()==h for h,n in rows); print('PASS',len(rows),'files')"
```

清单路径相对本包目录，包含3份展示文档、相关上游代码、报告、原始输入和封存清单。
采用引用封存，不复制大型索引与排名；迁移时需保留同一仓库相对布局和全部清单文件。
清单不包含自身哈希，其SHA-256由最终交付摘要给出，避免循环哈希。
不要直接重跑acceptance.py、阶段A/B或模型传输脚本来复核：它们是一次性写入入口。
