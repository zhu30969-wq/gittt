# 第三方来源与原创边界

本文件记录本项目在设计 CUMCM 数学建模 Skill 时查阅的第三方公开仓库、固定版本、许可证状态与 clean-room（洁净室）原创边界。记录日期为 2026-08-27。

## 来源清单

### 1. zhnnky329/MathModeling-skills

- 固定分支与 HEAD：`main` @ `046a6e74814c2e5fef72b5ee56305509a8635e1d`
- 固定版本：[zhnnky329/MathModeling-skills@046a6e7](https://github.com/zhnnky329/MathModeling-skills/tree/046a6e74814c2e5fef72b5ee56305509a8635e1d)
- 参考角色：用于研究人类决策驱动的阶段门禁、模型中立的问题解析、主方法与可用基线的筛选、方法特异风险探针以及结果冻结思想。
- 许可证/版权状态：仓库根目录采用 **MIT License**，版权声明为 `Copyright (c) 2026 Zhijun Zhang`。
- 固定许可证据：[根目录 LICENSE](https://github.com/zhnnky329/MathModeling-skills/blob/046a6e74814c2e5fef72b5ee56305509a8635e1d/LICENSE)
- 使用边界：本项目没有复制其多 Skill 编排、`SKILL.md` 表述、提示词、代码、示例或资产；阶段语义、G0–G7 门禁和全部契约均为独立设计。

### 2. yushui2022/MathModel-Skill

- 固定分支与 HEAD：`master` @ `51054497f052197c3afe434e502e38edb85b2870`
- 固定版本：[yushui2022/MathModel-Skill@5105449](https://github.com/yushui2022/MathModel-Skill/tree/51054497f052197c3afe434e502e38edb85b2870)
- 参考角色：用于研究可恢复的竞赛生产流水线、输入预检、状态恢复、SHA-256 新鲜度、运行账本、证据门禁、正式论文交付与持续集成。
- 许可证/版权状态：固定版本的根目录及完整 Git tree 未发现 `LICENSE`、`LICENCE`、`COPYING` 或 `NOTICE`，GitHub API 也未识别许可证。公开可见不等于获得复制、修改或再分发许可。
- 固定版权证据：[固定版本根目录](https://github.com/yushui2022/MathModel-Skill/tree/51054497f052197c3afe434e502e38edb85b2870)
- 使用边界：仅借鉴抽象的可恢复工作流和可复现性思想；未复制其 `SKILL.md`、脚本、提示词、Word/OMML 模板、示例论文、图表、数据、发布包或其他资产。

### 3. capwitf/My-MathModeling-skills

- 固定分支与 HEAD：`main` @ `da53b41cab7e25be906f5899488229387e7921c0`
- 固定版本：[capwitf/My-MathModeling-skills@da53b41](https://github.com/capwitf/My-MathModeling-skills/tree/da53b41cab7e25be906f5899488229387e7921c0)
- 参考角色：用于研究独立数学核验、条件性数值诊断、结果—结论—图表证据关系、跨媒体一致性检查和评审视角。
- 许可证/版权状态：仓库根目录采用 **MIT License**，版权声明为 `Copyright (c) 2026 capwitf`。
- 固定许可证据：[根目录 LICENSE](https://github.com/capwitf/My-MathModeling-skills/blob/da53b41cab7e25be906f5899488229387e7921c0/LICENSE)
- 使用边界：本项目没有复制其 Skill 文本、CSV 登记表、脚本、论文模板、图表模板或资产；证据 DAG、JSON Schema、审计器、论文 lint 与测试均为独立实现。

## Clean-room 原创规则

本项目遵循以下边界：

1. 可以参考不受版权保护的思想、事实、公知数学方法、公式、模型类别名称和抽象工作流程。
2. 所有 Skill 指令、提示词、检查表、示例、脚本和目录编排均独立设计和撰写，不沿用来源仓库的独特表达、选择或编排。
3. 算法实现依据数学定义、原始论文或官方文档重新实现；需要核对竞赛格式时以当年官方材料为准。
4. 示例数据采用原创、公开且许可证兼容的数据，或明确标注为合成数据；不得从来源仓库抽取附件作为样例。
5. 不引入第三方文本、代码、模板、图片、字体、论文、赛题或二进制资产，除非已经完成逐项许可证审查、归因和兼容性确认。
6. 若未来确需引入第三方受保护内容，必须在合并前记录来源、固定版本、作者、许可证、修改说明与分发条件；许可证不兼容或权利链不清时不得合并。

## 未复制声明

本项目对上述三个仓库的使用仅限于概念性调研和事实核对。项目没有复制、翻译、改写或打包上述来源中的受保护文本、提示词、源代码、模板、图表、图片、数据集、论文、赛题、压缩包或其他二进制资产；相关链接仅用于透明记录灵感来源和版权边界。

无明确许可证并不等于可以自由复用。GitHub 对无许可证仓库的说明见：[Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。本文件是保守的工程合规记录，不构成法律意见。
