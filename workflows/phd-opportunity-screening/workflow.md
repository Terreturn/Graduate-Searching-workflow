# 研究生项目筛选工作流 · Master 与 PhD

输入一条自然语言消息、用户 ID、目标 ID 和稳定的消息 ID。需求拆解 agent 用本机已登录的 Codex CLI 启动一个独立的 `gpt-6.1-sol`、`xhigh` 会话。存储模块返回实际差分、目标版本和当前规则。需要 Python 3.9+ 与支持 `exec --output-schema`、`--ephemeral` 及本流程调用选项的 Codex CLI。Python 仅使用标准库。

### 从需求到结果

1. `turn` 把当前消息拆成个人背景、长期目标和当轮条件。用户在读的学位与要申请的学位分别记录。
2. 存储层计算差分、版本和当轮生效规则。`degree.type` 选择目标学位，`when_degree` 和 `when_country` 可组合限定条件。历史规则缺少 `when_degree` 时按共享规则读取。
3. `plan` 按学位标签读取来源库，生成搜索方向、优先级、搜索重点和排序理由。预算问题优先核查收费表，论文要求优先核查培养方案。
4. `search` 调用规划器，再由两个 Sol high 独立检索。程序合并候选时区分正式学位、培养路径、校区与入学批次。两路各自打开原文，按每个候选的学位分支核对条件。
5. Astra xhigh 回读证据。程序核对硬条件、收费口径、数值和引用，再按当轮提问输出带来源的表格。`--save-test` 用于实际运行时归档完整记录。

### 硕士条件

| 用户条件 | 存储与执行 |
| --- | --- |
| 培养路径 | `program.track` 区分研究型、授课型和混合型。`program.thesis` 单独区分论文必修、可选和无论文路径。以具体项目和方向的培养方案为准。 |
| 学费上限 | `tuition.budget` 保存十进制金额、币种、上限是否含边界、全项目／每年／每学期／每学分、学生收费类别，以及是否包含强制费用。按奖助前学费比较。两路核验与审核给出的同口径数值须一致。身份未确定时，须有适用于各相关类别的费率或有依据的上界。 |
| 资助方式 | `funding.coverage` 描述覆盖内容，`funding.mechanism` 描述录取保证、竞争评选、单独申请或自费。核查同一资助方案的覆盖与获得方式。允许自费可解除该学位的资助门槛。 |
| 学位分支 | 指定学位的规则可覆盖同字段、同国家条件、同角色和同匹配模式的共享规则。显式“不限”解除本分支该项限制，撤回本分支规则恢复对共享值的继承。临时修改只在所选轮次生效。 |

例如“博士必须保证全奖，硕士可以自费，国际生全项目学费最多 4 万加元”可放在一个目标中。博士分支核对全额资助及录取保证，硕士分支核对国际生学费总额。两边共享用户明确适用于所有项目的条件。

## 开始和续接

在工作区根目录运行。下面两次调用属于同一个目标。

```bash
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd turn --id t01 '我想找美国的 PhD 项目'
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd turn --id t02 '加拿大也可以。'
```

退出终端或换到新聊天后，回到同一工作区并使用原目标 ID。

```bash
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd show
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd show --version 1
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd history
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd modules
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd modules --turn t01
```

也可以在聊天中说“按 PhD 筛选工作流读取目标 my-phd”或“把‘加拿大只考虑全奖’作为新一轮写入 my-phd”。每条新消息使用新 ID。网络中断后，重试同一消息时沿用原 ID。成功重试会返回该消息当时的结果，可另用 `show` 查看最新状态。

默认用户为 `local`，数据库位于 `runs/workflows/phd-opportunity-screening/goals.sqlite3`。可在子命令前用 `--user`、`--goal`、`--db` 指定命名空间和文件。另一个申请人或独立申请计划使用单独的用户或目标 ID。SQLite 中的 ID 用于本地记录隔离。跨会话读取需要同一份数据库。

### 在聊天中测试

用户说 “Test start” 时，先为测试目标执行 `test-start`，再用 `turn` 处理第一条问题。每次成功处理或校正后，同一份 `runs/test/` JSON 会保存本次测试从开始到当前的所有轮次，包括背景、目标与临时条件的原始输出、差分及版本。旧记录缺失的字段保留原样。处理失败的尝试会写入 `attempt_errors`。跨会话使用同一数据库、用户和目标即可续写。用户说 “Test end” 时执行 `test-end`。

```bash
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd-test test-start
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd-test turn --id q1 '我想找美国的 PhD 项目'
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --goal my-phd-test test-end
```

开始和结束命令都会返回报告路径。结束时有验收文件可加 `--evaluation 路径/eval.json`，报告按其中的 `passed` 标记通过或失败。手动结束时标记为 `completed`。再次开始测试会创建新文件，默认只收录新轮次。`verify_live.py` 和 `verify_modules.py` 会自动开启并结束报告。

## 角色交接

### 三个模块

| 模块 | 存储与作用范围 | 读取内容 |
| --- | --- | --- |
| [个人背景](scripts/personal_background.py) | SQLite 表 `personal_background` 按用户保存，跨同一用户的目标复用，有独立版本号 | 在读学位、专业、研究经历和技能；每项可追溯到目标、轮次和逐字引文 |
| [目标存储](scripts/store.py) | 表 `goals` 按用户和目标保存 | 目标版本、长期范围、门槛和偏好 |
| [临时条件](scripts/temporary_conditions.py) | 表 `temporary_conditions` 按用户、目标和消息 ID 保存。每次解析从空的临时条件开始 | 本轮要求或假设、它们相对持久规则产生的差分 |

`modules` 返回当前背景、当前目标与最近已处理消息的临时条件；`modules --turn ID` 读取该消息提交时的三个模块。旧版历史未记录的背景显示为 `not_recorded`。三个模块与审计表 `turns` 在同一事务内提交。背景更新不会递增目标版本。回复每条测试消息时分别展示背景差分、目标差分和本轮条件；在后续消息中注明上一轮专属条件已经到期。

旧库首次写入时会从已有轮次补齐临时条件表。若旧首轮把背景误作条件，且用户之后只做过查询，可用 `reparse-initial --source t01 --id repair-t01` 追加校正事件。该操作从原话重新解析，保留原始历史。已经出现后续持久修改的目标或已有背景的用户需根据当前状态明确纠正。

1. [入口脚本](scripts/goals.py)装载个人背景、该目标的所有当前规则，以及最近六条消息、回显和澄清问题。已提交的规则与版本独立于聊天历史。
2. [拆解模块](scripts/decomposer.py)把这些数据与本轮原话交给 [Sol 提示词](prompts/decompose.md)，通过 JSON Schema 取得意图、变更、逐字引用的依据、澄清问题和本轮提问语言的回显。模型调用失败时，命令返回错误，消息可用原 ID 重试。
3. [规则模块](scripts/rules.py)校验字段、值域、作用范围和证据，计算变更前后的规则。未提及字段没有条目，明确“不限”保存为 `unrestricted`。同一条规则中的 `any`、`all`、`none` 分别表示任一、全部和排除。不同硬条件同时生效。`when_country` 指定仅在某个国家生效的条件。超出字段或分支表达能力的要求由 agent 提问澄清。
4. [存储模块](scripts/store.py)用同一事务写入目标和轮次记录。只有持久规则发生变化才增加目标版本号。临时比较和展示要求仍有轮次记录。版本或近期对话在解析期间发生变化时，入口重新装载并解析，最多尝试三个快照。对最近一次持久修改的撤销也作为新的可追溯版本写入。

[语言核对](prompts/check-language.md)在新提案提交前检查摘要和每一条澄清问题。拆解仍用 Sol xhigh，语言核对使用同一模型的 low 推理强度。发现语种不符时只修订这两项展示文字，再核对一次；复核失败则返回错误。本轮输入缺少语言线索时参考最近的用户提问。

Present results, module changes, source descriptions, clarifying questions and error explanations in the language of the current user request. For mixed text, use the language framing the request. Preserve quoted evidence, official names, identifiers and URLs in their original form.

## 下游应读取的字段

| 字段 | 用途 |
| --- | --- |
| `personal_background`、`background_diff` | 已提交的背景事实、版本与本轮变化 |
| `temporary_conditions` | 本轮全部临时操作、作用范围和差分 |
| `proposal.changes`、`proposal.questions` | 理解模型提议的操作、原话依据和仍需澄清之处 |
| `base_version`、`version`、`diff` | 核对持久目标从哪一版变到哪一版，以及各条规则的旧值和新值 |
| `rules` | 传递给后续会话的持久搜索范围、条件和偏好 |
| `temporary_diff`、`effective_rules` | 本轮对比或展示所需的调整与应用后的完整规则 |
| `proposal.summary` | 与本轮提问同语种的一句话回显。对精确条件和值以实际提交的 `diff` 和 `rules` 为准 |

例如地域为 US 或 CA，且一条全奖要求带有 `when_country=CA`，表示任何获准地域都可进入搜索，而全奖门槛只作用于加拿大。模型只负责解释需求，项目数量、实际经费和可申请资格留给后续阶段取得。

国家和区域互为备选时，读取同一条 `program.location` 规则。例如 `match=any` 配合 `["country:US","region:Europe"]` 表示美国或欧洲。区域按用户指定的粒度保存。

## 研究生项目搜索来源库

[SQLite 数据库](data/sources.sqlite3)保存跨用户共享的多学科搜索入口、帖子、公告和排名来源。每条记录有稳定的 `id`。`site` 表示网站、版块或榜单入口，`post` 和 `announcement` 通过 `parent_id` 关联所属入口。地区按项目目的地或来源覆盖范围记录。

从工作区根目录运行[查询脚本](scripts/sources.py)，标准输出为 JSON 数组。

```bash
python3 -B workflows/phd-opportunity-screening/scripts/sources.py --kind site
python3 -B workflows/phd-opportunity-screening/scripts/sources.py --degree master --type program
python3 -B workflows/phd-opportunity-screening/scripts/sources.py --region country:JP --type program
python3 -B workflows/phd-opportunity-screening/scripts/sources.py --region country:HK --kind post --type supervisor_lead
python3 -B workflows/phd-opportunity-screening/scripts/sources.py --type ranking
python3 -B workflows/phd-opportunity-screening/scripts/sources.py --ranking-scope subject --ranking-year 2026
python3 -B workflows/phd-opportunity-screening/scripts/sources.py --ranking-scope phd_program
```

Python 调用方可从 `scripts/sources.py` 导入 `find_sources`，例如 `find_sources(ranking_scope="overall", ranking_year=2027)`。SQL 调用方可以只读打开数据库，查询视图 `source_catalog`。视图每行对应一条记录，`regions` 和 `info_types` 为 JSON 数组。按单个标签关联时使用 `source_regions` 和 `source_types` 两张表。

`source_degrees` 将具体入口与适用学位关联，视图的 `degrees` 列是 JSON 数组。`--degree` 可重复，多个学位取并集，并与地区、信息类型等筛选相交。`find_sources(degrees=("master",))` 可用于硕士检索。研究生目录可以服务多种学位，硕士专项入口包括 Erasmus Mundus 联合硕士。同一条大学综合排名可用于比较申请院校，其排名对象仍为大学。旧版自定义来源库没有学位标签时，按学位查询返回空列表，规划器仍可提出应定位的官方页面类型。

| 字段 | 用法 |
| --- | --- |
| `coverage`、`regions` | 前者说明实际覆盖范围，后者供精确筛选，例如 `country:JP`、`region:Europe` 或 `global`。多个 `--region` 取并集；需要同时查跨国平台时显式添加 `--region global`。 |
| `info_types` | 可按博士项目 `program`、招聘 `vacancy`、资助 `funding`、导师线索 `supervisor_lead`、申请经验 `application_experience`、资源清单 `resource_list` 或排名 `ranking` 筛选。地区、内容类型和记录种类之间取交集。 |
| `url`、`official_url` | `url` 是发现入口或原帖。`official_url` 保存对应的官方依据；官方原文可与发现链接相同，未取得独立官方依据的社区记录留空。CAPES 的官方说明可作为其检索页的备用入口。 |
| `application_status`、`deadline` | 有具体招生活动时记录核实状态和已知截止日。网站和资源讨论留空；未核实的招生帖标为 `unverified`，有确定截止日期的历史公告可标为 `closed`。 |

每个具名榜单的一个版本对应一条排名记录。`publisher` 标识发布方，`ranking_scope` 可取大学综合榜 `overall`、大学学科榜 `subject`、研究生院榜 `graduate_school` 或博士项目榜 `phd_program`。`ranking_object` 说明榜单实际评估的对象。`disciplines` 保存覆盖的学科或学科大类，综合榜使用 `Multidisciplinary`。`ranking_year` 是发布方标注的版本年。读取每年滚动更新的 `official_url` 时，按 `url` 中的同期发布记录核对版本。

用户要求按博士项目排名筛选时使用 `--ranking-scope phd_program`。例如 PGR 的榜名含 “Overall”，衡量的仍是哲学博士项目的师资；美国 U.S. News Best Graduate Schools 的总入口使用 `graduate_school`，具体分榜另有学位层级。

[建库 SQL](data/sources.sql)包含表结构、全部初始数据和查询视图。在空文件上执行即可复现初始库，例如 `sqlite3 /tmp/phd-sources-new.sqlite3 < workflows/phd-opportunity-screening/data/sources.sql`。

## 生成搜索方向

从工作区根目录运行下列命令，可复现“假如只看加拿大，会有哪些选择？”这一轮的规划。该轮继承了加拿大博士项目必须提供全奖的条件。

```bash
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --user local --goal us-phd-demo plan --turn t04 --save-test
```

[规划 Agent](scripts/planner.py) 读取指定用户、目标和轮次对应的原话、已生效规则和当时记录的个人背景。省略 `--turn` 时使用该目标最近提交的一轮。历史记录中缺失的背景按未知处理。来源库默认使用本工作流的 SQLite 文件，`--catalog` 可指定另一份相同结构的库。

[规划提示词](prompts/plan-search.md) 按当轮的信息需求、发布方对该信息的权威性和覆盖范围排序。匹配的来源在搜索方向中附库内链接。缺少合适记录时，规划器给出下一步应定位的页面类型，例如院系招生公告。此阶段只读两个库，模型调用关闭网页搜索。

标准输出为 JSON 数组，每行包含 `search_direction`（搜索方向）、`priority`（P1 先查）、`search_focus`（搜索重点）和 `ranking_reason`（排序理由）。正文语言跟随当轮提问。加上 `--save-test` 时，相同数组也会写入 `runs/test`，文件路径输出到标准错误。

## 搜索、交叉核验与发布

在工作区根目录运行下列命令，读取加拿大示例当时生效的博士与全奖条件、查询来源库、生成搜索计划并实际核查网页。省略 `--turn` 时读取最新轮次。

```bash
python3 -B workflows/phd-opportunity-screening/scripts/goals.py --user local --goal us-phd-demo search --turn t04 --save-test
```

[搜索模块](scripts/research.py)启动两路独立的 `gpt-6.1-sol` high 会话，分别从目录和发布单位入手。合并候选后，两路只接收候选身份和当轮要求，再分别查找原文、记录适用范围并判断硬条件。每个硬条件和待发布字段都关联到具体页面。对单边未能证实的硬条件再做一次定向核查。

`gpt-6-astra` xhigh 阅读两份记录，回读原始页面，审核限定词、适用人群和原文版本。发布器按网页工具的打开记录校验链接，逐行检查双方的硬条件判断和字段证据。审核链接与字段错配时，向审核 Agent 返回该字段可以引用的原文，再核一次。表头随问题选择。标准输出是 Markdown 表格，来源列链接到支持展示内容的原文。用户偏好影响排序。硬条件未证实的条目暂缓发布；个别项目缺少可选信息时，相应单元格显示“—”。

使用 `--save-test` 时，`runs/test` 的同一份 JSON 会随各阶段原子更新。`table` 是用户看到的表格；其余字段保存轮次、计划、两个搜索角色的证据、网页工具调用记录、模型与审核决定。运行异常记录为 `failed`。需求库和来源库始终只读。联网检索需要本机 Codex 能访问以上两个模型及实时网页工具。

## 验收与复跑

[用例](evals/cases.yaml)用 JSON 兼容的 YAML 编写，包含七轮在线验收，以及一个使用虚构需求的五轮回放。[合成回放数据](evals/fixtures/graduate-five-turns.json)包含五次需求变更和一份旧版格式的快照。`test_store.py` 在临时库离线回放五个接受的提案，逐轮检查三个模块与实际输出是否一致。

```bash
python3 -B workflows/phd-opportunity-screening/evals/test_store.py
python3 -B workflows/phd-opportunity-screening/evals/test_planner.py
python3 -B workflows/phd-opportunity-screening/evals/test_research.py
python3 -B workflows/phd-opportunity-screening/evals/verify_language.py
python3 -B workflows/phd-opportunity-screening/evals/verify_modules.py
python3 -B workflows/phd-opportunity-screening/evals/verify_live.py runs/workflows/phd-opportunity-screening/YYYY-MM-DD_001
```

`verify_modules.py` 用独立的临时库实际运行两轮 Sol 拆解，检验个人背景、岗位目标、临时数量及到期行为。`verify_live.py` 命令中的日期和序号请换成本次运行目录。默认在该目录的 `output/` 建立独立数据库；也可通过 `--db` 指定共享库和 `--goal` 指定测试目标。新一轮实测应选择空的新目标或新的运行库。中断续跑则沿用同一目录、数据库和目标。

每条原话通过正常 CLI 运行，每轮结束再启动一个只读进程核对状态。最后重送第一条消息并读取全部历史，检查旧消息重试不会增加事件或回退版本。在 `steps/` 查看逐轮完整 JSON，`output/final-goal.json` 保存最终快照，`eval.json` 保存逐轮验收结果。离线测试另外验证并发重算、事务回滚、用户隔离及原话证据校验。
