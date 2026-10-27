# 第三方与 AI 使用说明

## 本项目许可

维护者已明确确认拥有全部拟发布代码的公开发布权利，并选择Apache License 2.0。项目许可为 **Apache-2.0**，完整原文见 [LICENSE](../LICENSE)，直接取自 `https://www.apache.org/licenses/LICENSE-2.0.txt`，未替换附录中的占位符，也未编造版权持有人。第三方材料继续受原有许可约束。

本次白名单不包含第三方依赖二进制包或完整源码仓库；拟发布Python/Java源码未发现第三方版权头或随附NOTICE条目，故不凭空创建NOTICE。若将来打包依赖、复制第三方实现或发布完整上游快照，必须重新保留相应LICENSE、NOTICE和版权归属，不能用本项目LICENSE替代。

## 依赖与再分发

|用途|依赖及固定版本来源|许可处理|
|---|---|---|
|最小Python CLI|jieba 0.42.1，requirements.txt及本机安装元数据|MIT；保留其版权及许可，发布前核对实际分发包|
|Java语法解析|javaparser-core 3.26.4，java-indexer/pom.xml|JavaParser提供Apache-2.0／LGPL选项；以实际依赖包许可为准，确认采用的许可路径|
|JSON|jackson-databind 2.18.2及传递依赖，POM|Apache-2.0；保留相应NOTICE／许可|
|Java测试|JUnit Jupiter 5.11.4，POM|EPL-2.0；仅测试依赖|
|可选绘图|src/make_figures.py中的matplotlib、numpy|不属于最小CLI；本机3.10.9／2.2.6不是本次新增运行依赖，分发前另核许可证与传递依赖|
|离线实验SDK／Tokenizer|各原实验协议的独立环境|不纳入根requirements；不重新分发虚拟环境或模型资产，逐包核验原协议与许可|

本轮不下载第三方包、不复制其代码或完整仓库。Commons Lang／Gson源码与Issue文本属于上游资产；完整快照、修复diff及抓取元数据不默认上传。使用者主动取得上游仓库时，应保留上游LICENSE与NOTICE。Python／JDK／Maven运行时本身也不随仓库打包。

本次许可核对依据为已安装jieba的distribution元数据及本机Maven缓存中的上述版本POM；未用猜测补充许可，也未联网更新依赖。

## AI辅助开发

项目使用过AI辅助编码、测试、文档、实验工具和材料整理；使用者仍需人工审查代码、授权、实验输入和结论。具体模型／贡献清单、团队成员署名及第三方片段溯源尚需团队确认。已有DeepSeek实验是冻结研究数据，不是开源安装或离线演示的必需步骤；本次发布整理没有新增模型请求或重评分。
