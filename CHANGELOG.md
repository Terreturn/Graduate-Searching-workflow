# Changelog · 更新日志

[English](#changelog-en) · [简体中文](#changelog-zh)

<a id="changelog-en"></a>

## English

### v1.1.0 · 2026-11-20

Labeled the school and advisor screening workflow v1.1.0 in both READMEs. [Version commit](https://github.com/Terreturn/Graduate-Searching-workflow/commit/f285915)

The four-turn live acceptance test on 2026-11-20 used the same UBC goal throughout.

| Turn | What the applicant requested | Branches started | Result |
| --- | --- | --- | --- |
| 1 | Find the Statistics MSc at UBC Vancouver and its official page. | School only | Saved the program and campus. |
| 2 | Find Paul Gustafson's research interests and link him to the MSc. | Advisor only | Reused the saved program and verified the advisor's link to its thesis pathway. |
| 3 | Require a thesis option and public evidence of graduate thesis supervision. | School and advisor, in parallel | The worker intervals overlapped. Both conditions were verified for the same pair. |
| 4 | Show only the first verified pair. | Neither | Read the same pair and sources from SQLite with **zero web calls**. |

**Release checks.** All 56 offline regression tests passed. All four live acceptance turns passed, including a recovery that reused earlier evidence. Reopening SQLite reproduced the same pair, sources and table for turns 3 and 4.

### v1.0.0 · 2026-11-20

Added advisor screening and parallel school and advisor searches driven by per-turn requirement changes. SQLite stores verified advisor–program links, training pathways and sources for later turns. The advisor catalog gained scholarly indexes, conference proceedings, project registers and thesis repositories. [Feature commit](https://github.com/Terreturn/Graduate-Searching-workflow/commit/4d7533a)

### 2026-11-17 · Screening requirements and evidence checks

An unresolved hard requirement stays with its goal, degree and country until the applicant answers or withdraws it. Each turn saves the questions that applied at that time. For example, a master's tuition limit of 40,000 with no currency or billing period allows discovery to continue while the affected master's results await clarification. Eligible PhD results can still appear.

For country-specific rules, both researchers and the auditor verify the delivery campus's country. The table renders tuition, stipend amounts and deadlines from their agreed values and links to the pages used for the displayed facts.

All 42 offline regression tests passed. A three-turn live intake test also confirmed that a master's tuition cap survives an unrelated preference change and takes effect once the applicant supplies the missing currency and billing period.

<a id="changelog-zh"></a>

## 简体中文

### v1.1.0 · 2026-11-20

将院校与导师筛选版本标为 `v1.1.0`，中英文 README 同步标注版本号。[版本提交](https://github.com/Terreturn/Graduate-Searching-workflow/commit/f285915)

2026-11-20 的真实验收在同一个 UBC 搜索目标下连续提了四个问题。

| 轮次 | 申请人要求 | 实际启动的分支 | 结果 |
| --- | --- | --- | --- |
| 1 | 查 UBC 温哥华校区统计学硕士及其官网。 | 仅院校 | 保存项目和校区。 |
| 2 | 查 Paul Gustafson 的研究方向，并关联上轮硕士项目。 | 仅导师 | 复用已有项目，核实导师与该项目论文路径的关联。 |
| 3 | 项目要允许写硕士论文，导师要有公开的研究生论文指导记录。 | 院校与导师并行 | 两条分支的执行时间重叠，同一组合通过两项核查。 |
| 4 | 只展示第一组已核实结果。 | 均未启动 | 从 SQLite 读取同一组合和来源，**网页调用为零**。 |

**版本测试。** 56 项离线回归测试全部通过，四轮真实验收全部通过，其中一次补查复用了先前的取证记录。重新打开 SQLite 后，第三、第四轮复现出的组合、来源和表格完全一致。

### v1.0.0 · 2026-11-20

加入导师筛选与按需求差分并行的院校、导师分支。SQLite 保存核实过的导师、项目、培养路径和来源，供后续轮次复用。扩充学术索引、会议、科研项目与学位论文等导师发现来源。[功能提交](https://github.com/Terreturn/Graduate-Searching-workflow/commit/4d7533a)

### 2026-11-17 · 筛选条件与证据核查

待澄清的硬条件会按目标、学位和国家保存，直到用户补全或撤回。每轮保留当时的快照。例如，硕士学费上限为 40,000，但币种和计费周期待确认时，系统可以继续找项目，只暂缓发布受影响的硕士结果。已核实的 PhD 项目仍可展示。

遇到限定国家的规则时，两位检索者和审核者都要核实授课校区所在国。最终表格使用三方确认的学费、津贴金额和截止日期，并附上展示内容对应的来源链接。

42 个离线回归测试全部通过。另用真实拆解模型测试了三轮，确认变更专业偏好后，硕士预算问题仍会保留，补全币种和计费周期后预算上限生效。
