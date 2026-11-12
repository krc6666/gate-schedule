# 2026-11-12 轻松班次月度均衡（软目标）

| 环节 | 唯一owner及消费点 |
| --- | --- |
| 设置与输入 | domain.default_relaxed_shift_settings/validate_relaxed_shift_settings：两项BalanceEnabled默认true、MaxDifference默认1（0至31整数）；原时段/末班口径保持 |
| 分类与候选 | domain.relaxed_shift_task_groups为统计与求解提供相同计次任务/阻挡任务；solver资格、出勤、固定、半休、时间冲突保持 |
| 历史 | relaxed_shift_statistics.relaxed_counts_before：目标同月且严格较早的最新正式；值班快照一致，不取草稿/自己/未来 |
| 求解 | Workbench._solve所有自动入口传设置/次数/值班；solver先取得正常基准，relaxed_shift_balance保留两项机会数量，优化超目标差距与少次数优先；覆盖/分队长/半休在前，时间不足保留基准 |
| 人工与后置 | 人工允许偏好例外；撤销、清空、重新采用后的统计按最终安排重算；均衡不作硬拒绝 |
| 最终合法性 | validator独立硬规则保持；无新硬规则，反向破坏固定仍拒绝 |
| 持久化/迁移 | workspace_document默认补齐；Workbench统一保存；configuration_transfer/excel_io保留旧缺字段与明确关闭；SQLite/JSON/完整Excel/A/B/完整备份携带 |
| 展示/输出 | 原规则区两项独立开关/目标差距，统一保存后重排；原名单/月度统计/正式与导出不追改，解释软目标可能受约束/时限影响 |
| 验证 | 先红后绿2/0及多班追平、两项独立启停、固定反向、旧格式保留/空库默认、完整Excel空库重启实际求解、双组相反选择、静态WASM与实际EXE、原入口 |

---

# 2026-11-05 缺人时按岗位保岗优先级

本项是软目标顺序，不新增或放宽硬规则。Owner最新要求由其自行选择，覆盖此前同意的岗位默认分档；初始全不勾。domain唯一拥有三档、未选正常与岗位覆盖；所有航班、共享任务和轮换段统一生效，同档同等优先。

| 环节 | 消费点及验收 |
| --- | --- |
| 输入/候选 | balanceSettings.coveragePriorities存roleName/priority；领域校验重复、未知档位/岗位。候选资格与可用时间保持 |
| 求解 | plan_scenario复制设置到Scenario；solver依次最少优先/正常/低档缺口，再原偏好；不突破固定、资格、专岗、冲突、差距等 |
| 人工/后置/最终校验 | 人工可自主选择空岗；validator仍独立检查原硬规则和缺口，不把优先级当硬规则；自动结果保留原最终门禁 |
| 页面/输出 | 规则页每岗两项互斥勾选，全不勾正常，进入统一保存与未保存保护；优先岗仍缺明确提示；旧正式班表不追改 |
| 保存/迁移 | SQLite/JSON/计划快照/双组完整Excel；可见保岗附表与版本标记配套，缺失损坏拒绝；旧无字段保留本机，空环境全不勾；明确空列表恢复正常；A/B独立 |
| 回归 | 分档取舍、跨航班统一生效、共享任务与原硬规则反向；配置修改→导出→空库→重启→实际求解、双组隔离、旧格式保留与损坏拒绝；真实用户配置仅副本验证 |

# 2026-11-05 上班对照班表展示

无新增设置、硬规则或迁移字段。schedule-extras沿用state.plans内更早日期正式快照选择，仅将姓名清单替换为完整航班岗位/人员双列表；formalDay/formalSchedule/formalCellDrafts/formalIssues为唯一显示来源，原问题与待完善姓名保留，不消费当前资格或旧草稿重算。共享轮换沿原矩阵归属显示，独立支援仅在原矩阵有支援记录时显示。求解、人工修改、validator、存储和统一配置导出不变；对照DOM无编辑/拖动标记或wb-matrix，不进入当前主表编辑/缩放/结果导出。回归previous-comparison覆盖正式隔离、问题快照/原文转义/空态/支援、真实Chrome宽窄屏；既有完整前端及静态Chrome验证原工作流与文件迁移。

# 2026-11-05 当天准备政策收口

没有新增硬规则、业务设置或文件字段，仅将前端准备过程归到同一module。

| 环节 | 消费点及保持边界 |
| --- | --- |
| 输入事实 | state长期配置/模板/周计划与后端preparationDay仍为原输入；day-preparation只编辑副本，不拥有资格/冲突规则 |
| 普通重排/最新配置/新日 | startPreparation区分keep-day与latest内部政策；loadPreparationConfiguration为明确更新；模板移除岗位0需求、人数/方式/时间与旧流程一致 |
| 周计划/手改 | applyWeeklyFlights统一补航班、裁剪格子/衔接后套模板；页面继续编辑未确认资料；copyPreparationAttendance只跨日清半休，时段复制保持 |
| 确认与失败 | confirmPreparation返回新副本，保留原错误顺序和新日清理/特殊时段确认；失败保留准备窗、取消不写 |
| 候选/求解/最终校验 | Python的_prepare_day→domain.prepare_regular_day、原求解器与validator及CAS保持；旧apply-config和fill_day并非同种覆盖，不合并、不复制判断 |
| 输出与迁移 | 原统一配置/历史导出、空库导入/重启和当天实际使用保持；不新增迁移字段 |
| 回归 | preparation/day-preparation公共interface，旧新版完整流程逐字段比对，test_regular_preparation及完整前后端/真实Chrome文件迁移；缺省/硬规则反向仍由原领域测试覆盖 |

# 2026-11-05 配置迁移解耦

没有新增业务设置、硬规则或文件版本。只移动既有归属，验收仍包含完整配置在空环境重启后的值与实际效果。

| 环节 | 唯一消费点及失败边界 |
| --- | --- |
| 文件输入 | excel_io/group_excel继续解码可见维护表、完整历史和班组附表；缺失或损坏拒绝 |
| 候选与旧字段 | configuration_transfer.prepare_import在副本应用原历史/身份/资格/长期设置/当天例外政策并验证；Workbench与GroupWorkspaces共用 |
| 文档与最终校验 | workspace_document从Workbench移入原归一化、场景、草稿/正式/撤销/警告归档检查；领域事实、转换与validator不复制、不弱化 |
| 手改与求解 | Workbench继续拥有命令和求解，消费同一plan_scenario/validate_document；候选未通过原校验不提交 |
| 持久提交 | Workbench组内版本/提交与GroupWorkspaces全组共享投影/CAS仍负责原子性；预览后任一组变化或磁盘失败拒绝 |
| 输出与迁移 | prepare_export在副本准备完整payload与Excel；双组从同一捕获版本取payload，删除内部两次Excel导出/解码往返；明确false与旧缺字段区别保留 |
| 回归 | test_configuration_preparation公共interface覆盖副本/失败/旧字段/关闭；test_group_workspaces覆盖无内部解码、双组导入重启排班/可见修改/缺附表/过期/磁盘失败；原configuration_history/transfer/browser_runtime及真实Chrome文件迁移保持 |

# 2026-11-05 编辑保存与继续操作收口

无新业务设置、硬规则或迁移格式。WorkbenchEdits统一持有全部未保存事实、保存次序、命令成功清理及离开确认；workbench页面消费hasChanges/confirming，不再枚举多份草稿清单。表单、请求、渲染为adapter；轮值导入继续时读取当前人员。web/tests/workbench-edits.test.mjs从interface验证混合修改、失败保留、保存中重复点击和日期隔离，balance-ui与完整真实Chrome流程验证页面接入；后端统一配置导入/导出与独立校验保持。

# 2026-11-05 已保存勾选迁移核查

无新增规则或配置字段；控件、保存、Excel、导入与真实使用的归属见spec“勾选项配置迁移边界”。综合回归tests/test_browser_runtime.py::test_saved_checkboxes_survive_all_group_excel_transfer_and_restart覆盖A/B相反开关、重点组合、基础/当天差异、人员启用/分队长/资质、轮值资格、共用周计划、半休/出勤/支援/固定及原计划保持，并实际求解与查询重点统计。旧字段缺省和逐规则反向校验继续由原有专项测试负责。

# 2026-11-05 浏览器本地运行与文件迁移

新增运行与保存方式，不新增硬规则或长期业务设置。

| 环节 | 消费点与失败边界 |
| --- | --- |
| 事实与候选 | 继续由domain、Workbench、imported_schedule、validator拥有；浏览器仅加载相同Python源码，不在JS复制资格/冲突/统计规则 |
| 求解/月度轮值 | cp_backend按运行环境选择；browser_cp/browser-cp串行化现有整数模型，十进制字符串保留int64；不支持操作失败，不放宽约束 |
| 手改/导入/恢复 | browser_runtime复用workspace_api路由、GroupWorkspaces和原输入/历史校验，浏览器只承担传输和文件下载 |
| 最终采用与保存 | BrowserControl复用RunControl独立校验和停止门禁；候选文档通过IndexedDB严格事务提交后才确认成功；写失败返回错误，下一请求重新读原文档 |
| A/B与并发 | 原全组文档和版本保护保持；同源Web Lock跨标签串行，旧版本同组竞争拒绝，任务ID与班组绑定 |
| 导出/换设备 | 统一配置Excel继续拥有两组设置与完整历史；导出→空白浏览器预览→确认→刷新→排班/重点统计/轮值；旧格式策略保持，不增加浏览器独有设置 |
| 静态交付 | build/browser仅程序，普通子路径/无自定义响应头浏览器实测，不将人员和原SQLite放入网站；原桌面入口独立保留 |
| 回归与拒绝 | test_browser_runtime复用路由和新旧文档版本拒绝；browser-local.browser.test覆盖无后台、真实求解、停止、损坏、写入失败、竞争、两组同名、航班筛选、真实下载/空存储导入/刷新/再次求解；实际24方案副本全草稿/正式快照保持 |

# 2026-11-05 A/B班组与配置传播

本次新增数据隔离边界，不新增求解硬规则。

| 环节 | 消费点与失败边界 |
| --- | --- |
| 输入事实/迁移 | GroupWorkspaces拥有group-workspaces-v1：原workspace-v2归A原样保留；B清人员/方案/次数/轮值人员，岗位规则复制，基准日+1 |
| 共享投影 | flightTemplates航班号/时刻与weeklyFlightPresets共用；flightLibrary的岗位规则、configuration、人员/资格和所有长期设置归组 |
| 候选池/求解 | 请求组→该组Workbench，复用原资格、时段、出勤、轮值和历史软目标；不增加休息日硬约束 |
| 人工/后台 | group-api显式路径、schedule-run捕获初始组，跨组plan/run拒绝；旧接口固定A且同组run管理器共用；离开保护覆盖切组 |
| 最终校验/保存 | 原Workbench/validator验证各组完整状态；根SQLite CAS原子保存；独立组或偏好竞争可重读合并，同组过期拒绝 |
| 输出/统计 | 各组完整账本进入原统计模块，不在统计页重新实现过滤规则；正式导出只取请求组方案 |
| 配置Excel | group_excel复用压缩/长度/checksum历史codec，前部表属于标记组，附带两组完整配置与历史；缺表/组不符拒绝；全组预览版本保护 |
| 旧配置/完整备份 | 旧配置缺组只导入当前组，旧备份仅替换当前组，共享航班同步；新formatVersion3全组预览确认/原子恢复/撤销版本重定位 |
| 重启/迁移验证 | test_group_workspaces覆盖A保留、B空、共享/独立、同名、后台跨组、竞争、可见表修改→空库导入→重启→实际排班/统计、损坏/磁盘失败和过期拒绝；真实Chrome切组/未保存保护/窄屏；实际EXE和原数据副本复验 |

# 2026-11-05 重点航班＋岗位范围与配置迁移

本次为软目标范围及统计合同调整，无新增硬排班规则。

| 环节 | 消费点与边界 |
| --- | --- |
| 输入事实/默认 | domain.validate_priority_flight_roles验证明确且唯一的航班/岗位组合；Workbench根设置迁移旧字段为新范围空选，旧计划不改写 |
| 页面升级/旧端保存 | server原入口按页面内容版本打开、不缓存静态与实时数据；workspace_api要求save-rules/update-balance-settings显式声明flight-role-v1，旧页面即使回传新字段也拒绝并提示刷新；Workbench保存须有新范围字段；不改变Excel/备份旧字段兼容或业务硬规则；test_frontend_upgrade及真实旧JS/新EXE隔离Chrome回归 |
| 唯一计次事实 | domain.member_flight_role_participation按人员/航班ID/岗位去重，member_role_flight_counts消费该事实聚合；分段一次、共享按实际覆盖航班 |
| 候选池 | 资格、出勤、时段、半休及冲突候选保持；手选重点不扩大资格或强制某人 |
| 求解 | _priority_costs按选中航班号过滤参与变量，priority_counts_before的航班账本与上一归档组合事实供月度/换人软目标；独立稀缺目标保持原岗位口径 |
| 人工/后置 | 规则统一保存所有航班勾选，切换航班不丢选择；修正键为月/人/航班/岗，缺航班、非选中组合、0再减均拒绝且不写 |
| 最终校验/存储 | validator验证新场景组合结构；Workbench完整文档验证新设置及历史快照，原硬规则独立复查/问题封存/身份/CAS不变 |
| 输出/统计 | priority_statistics按组合列出日期与修正，并拥有所有筛选范围合计；界面只筛列及消费对应合计，不计算领域次数；legacyAdjustments单列待归属 |
| 导出/恢复 | 配置Excel范围版本＋航班重点岗位表＋修正航班列；旧缺字段保留本机组合/空库空选，缺新附表拒绝；SQLite和完整备份保留新字段及全部原历史 |
| 验证 | test_priority_flights涵盖双航班同岗隔离、共享分段去重、相反月次数/上班换人、旧库迁移、缺航班修正原子拒绝、缺附表、导出→空库→重启→实际排班；原迁移/统计/人员回归同步；Chrome验证航班切换、范围合计、次数修正、失败/迟到回复及窄屏 |

# 允许带问题归档和导出（2026-11-03）

Owner明确要求：岗位空缺、资质不符、时段冲突、航班差距超限或资料待完善，也能归档并排后天、仅归档、采用保存和导出Excel/HTML/图片；保留原姓名、空缺和全部问题，不把错误写成“校验通过”。此要求覆盖旧“必须无错误才能归档/导出”的门槛。独立校验仍运行，自动求解的资质/冲突规则保持。

例：甲在无资质岗位、乙岗空缺，归档保存当时安排及问题清单，再建立后天草稿；后天暂时排不开也保留待处理草稿，能继续下一班。历史只读及导出显示当时的问题和待完善姓名；修改当前草稿不追改历史。配置导出→空库导入→重启问题保持。损坏结构、未知任务/人员ID、篡改快照与问题清单、并发/磁盘失败或主动取消仍拒绝写入。

职责：Workbench归档校验和封存问题，新的显式archiveVersion=1快照含scenario（资料无法转换时为null）、issues与cellDrafts，老无标记快照继续严格复查；保存后重新构建输入验证快照和问题一致。Excel适配与前端只消费归档事实，不改validator/solver；历史统计仅累计实际结构明确的安排，不为待完善姓名捏造任务。归档后天业务排不开保存空草稿及原因，系统错误和取消仍原子回退。沿用既有便携打包、备份更新原入口流程，值机只读，单人完成。

| 环节 | 消费点与边界 |
| --- | --- |
| 输入事实 | 已保存day、draft、cellDrafts及规则快照，结构与身份继续校验；不为待完善资料捏造任务 |
| 候选/求解 | 原资格、出勤、冲突、半休和差值规则保持；下一班业务无解可保留待处理草稿 |
| 人工与归档 | Workbench._archive_review统一封存输入问题、待完善姓名和validate_schedule输出；_adopt不再按业务报警拒绝 |
| 最终校验/存储 | 新archiveVersion=1重建原输入、比对scenario及issues；旧无标记正式仍严格复查，损坏/未知ID/篡改/并发/磁盘失败和主动取消原子拒绝 |
| 输出/历史 | Excel首表保留姓名、空岗和待配置，附排班问题与待完善姓名；HTML/PNG和只读历史消费归档问题，整体差值不关联全部格 |
| 迁移/统计 | 配置压缩快照完整携带问题/待完善姓名，人员ID映射覆盖问题引用；无scenario记录保留历史但不捏造实际岗位次数 |
| 测试 | test_warning_archive覆盖报警/连续下一班/Excel/空库及同名ID迁移/重启/无任务/不可转换输入/篡改；真实Chrome下载三种格式、历史、窄屏和连续后天；实际EXE隔离副本/便携复验 |

# 2026-11-03 统一配置入口与历史紧凑存储

| 环节 | 消费点与边界 |
| --- | --- |
| 输入 | 侧栏统一xlsx/旧JSON识别；配置v5维护表及带校验的压缩历史，旧v1—v4兼容，无航班/空人员可维护 |
| 候选/求解 | 不变；仅消费导入后正常建立的日方案，不伪造航班或追改正式 |
| 人工 | 前部维护表小改；历史目录/航班/分配供核对，旧班表在工作台编辑；原预览确认/取消与CAS沿用 |
| 校验 | 全历史无损、字节长度/分段/摘要校验后交Workbench独立验证，原正式与输入快照必须一致；损坏原子拒绝 |
| 输出 | 唯一导出配置包含长期配置、全部每日班表；隐藏压缩快照保留所有恢复字段，业务历史表减少重复节点 |
| 验证 | 新旧文件兼容、边缘类型/长文本无损、损坏拒绝、10天与真实24份迁移/重启/统计/小改/重复导入、最终EXE侧栏唯一入口及便携 |

# 2026-11-03 可编辑配置与全部历史迁移

| 环节 | 消费点与边界 |
| --- | --- |
| 输入事实 | 完整配置v4保留可编辑人员/资质/航班/规则表，history使用Workbench全工作台快照；旧v1/v2/v3无历史兼容 |
| 候选/求解 | 不变；新维护方案消费修改后的输入，原草稿与正式不重算 |
| 导入与人员身份 | Workbench先独立验证源快照，configuration_history按稳定方案ID补入；同名人员ID映射草稿/正式/固定/轮换/策略/撤销引用，已删除身份独立保留 |
| 人工修改 | 前部表的小改更新基础配置并新增当天维护方案；历史班表仍在工作台编辑，同ID重导保留本机 |
| 最终校验 | 原_validate_document/validate_schedule同时验证源快照及合并后文档，CAS原子拒绝损坏/公式/不完整历史/版本冲突 |
| 输出/统计 | 历史页自然展示全部plans及正式矩阵；同日已有本机正式优先，其他保留源顺序；不叠加同日重复方案 |
| 测试 | 10天小改→空库→重启→历史/统计、同库重复导入/本机新版保留、同名ID映射、损坏原子拒绝、旧配置及xlsx/JSON备份兼容；最终EXE和真实副本验证 |

# 2026-11-02 上午/下午半休

| 环节 | 消费点与边界 |
| --- | --- |
| 输入事实 | 当天出勤halfRest：null/early-finish/late-start；Member与正式Scenario保留快照；半休设置exemptFlightCount默认true |
| 唯一规则owner | domain.half_rest_period_violation按任务开始的运营日06:00—12:00早班判定；12点、凌晨归另一时段，跨午按开始；flight_balance_members统一豁免集合 |
| 求解 | _eligible调用领域半休判断；覆盖及分队长设置后尽量落实允许时段、下午早点结束、上午最晚结束并减少之前安排，不能越过硬限制 |
| 人工/候选 | slot_issues消费独立validator；原手填问题草稿保留标红，候选说明半休限制，不授予例外 |
| 最终校验 | validator独立逐人逐任务调用领域判断，固定/手改/后置都检查；休息时段安排继续报警，归档/导出封存问题 |
| 展示与提示 | 排班和准备窗口多选半休及上午/下午；规则页豁免开关统一保存、说明边界；无岗仍保留半休并提示，显示人员半休身份 |
| 配置迁移 | 当前选择随当天出勤/完整Excel/JSON/SQLite/备份，豁免随统一配置；新日清空日选择，普通重排保留；旧输入缺半休为普通，旧设置缺字段保留/空库默认true，旧正式不追改 |
| 测试 | 时段边界/跨午/凌晨、自动后手改反向拒绝、固定无解/缺岗、两种目标/无岗提示、默认豁免与关闭、旧文件/新设备/重启/备份/实际使用、真实Chrome选择与全选 |

# 2026-11-02 当前班表计次与紧凑值班统计

| 环节 | 消费点与边界 |
| --- | --- |
| 输入与事实 | Workbench._statistics_draft消费所选draft/lastEdit及_scenario，不复制任务转换规则；未生成空方案无覆盖，明确清空按0 |
| 次数owner | _ledger(current=...)按日期替换正式，role_statistics及priority_statistics共享去重；重点人工修正仍独立，−/+按当前值调整1 |
| 求解与合法性 | priority_counts_before保持较早正式历史；候选/solver/validator/采用及正式输出不变，红色填写计数不授予合规许可 |
| 界面 | 所有岗及重点统计说明当前草稿口径；轮值默认折叠、40px标题、11px表格、顶部模板工具、编辑后保留展开 |
| 持久化与恢复 | 无新业务字段，草稿/正式/修正按既有SQLite及完整备份保留，配置Excel不携带真实历史；查询只读 |
| 回归 | test_role_statistics覆盖换人/清空/撤销/采用/红色任务/同日替换/跨月/修正/求解隔离/备份重启；真实Chrome验证手输即时计数与紧凑布局、原轮值操作 |

# 2026-11-02 所有岗位实际次数统计（只读）

| 环节 | 消费点与边界 |
| --- | --- |
| 事实与输入 | Workbench通过既有_scenario提供当前已保存草稿，_ledger替换当前日正式；其余正式经formal_history取同日最新，domain.member_role_flight_counts去重，配置/资格/历史/当前草稿拥有岗位集合 |
| 统计投影 | priority_statistics.role_statistics按月输出全部岗位实际count/dates、稳定人员身份及总计，不合入人工修正；现人员0次保留，已移除身份有实际历史才展示 |
| 求解/候选/人工/校验 | 不适用：只读查询，不增加或修改排班硬规则、软目标、手工安排、资格或修正；原正式门禁不变 |
| API与界面 | Workbench.role_statistics与GET /api/role-statistics只读；统计页独立区域按月/岗位/姓名筛选，合计随所选列，点次数展开日期/航班，失败/旧回复/版本/离页与中文输入保护 |
| 迁移与输出 | 无新配置或写入；实际历史随完整备份恢复重算，不把配置Excel当历史迁移；值班和重点统计入口与功能保持 |
| 测试 | test_role_statistics覆盖非重点/历史岗位、分段/共享去重、同日最新、跨月/零次/移除ID、实际与修正区分、HTTP只读与备份重启；role-statistics.test与真实workbench Chrome覆盖筛选/列合计/明细/姓名输入/旧回复/失败重试/版本/离页/窄屏 |

# 2026-11-02 稀缺岗位资质自动轮换（软偏好）

| 环节 | 消费点与边界 |
| --- | --- |
| 事实与输入 | domain.scarce_role_groups消费Scenario有需求航班任务及当天正常参加人员资格，按资质人数升序分层；同人数同层，人数最多不纳入稀缺，额外占用/0需求不参与；晚到早退只影响真实落位 |
| 自动与候选池 | solver在岗位覆盖和分队长使用目标之后，逐稀缺层调用原_priority_costs：当天分散→本月同岗正式次数与修正→上一归档换人，然后手选重点与原偏好；候选资格和冲突不变 |
| 人工与后置 | 原手调/固定/预览/恢复链不强制轮换，不授资格；固定无解仍拒绝替换原方案，无法安全换人可续用 |
| 最终校验 | 无新硬规则；独立validator继续校验原资格、出勤、固定、冲突、人数和航班差距，轮换未最优不判违法 |
| 界面与输出 | 规则页轮换与走动说明自动稀缺、同层、判断口径、2/8人例子及回退，搜索可见；草稿/正式/输出消费真实安排，旧正式不追改 |
| 配置与迁移 | 无新维护字段；完整配置迁移原资格、出勤/状态、重点设置与修正，完整备份迁移历史；新日或明确重排重新计算，手选重点仍独立可维护 |
| 测试 | test_workload_balance：旧实现先红两场景，稀缺优先于手选普通重点、无需手选时月次数/上一归档、固定/覆盖不退让、请假/0需求/额外占用排除、同层和多层；balance-ui：文案和真实Chrome搜索不写配置 |

# 2026-10-31 手工填人保留并报警（无新硬规则）

| 环节 | 消费点与边界 |
| --- | --- |
| 事实 | manual_editing.candidate_options以replace_slot结构检查结果给出manualAllowed；available继续表示资格/时段等合法性，不冒充空闲 |
| 自动/候选池 | 求解器、上方人员概览和自动补缺仍消费原合法规则；人工许可不提升资格或放松冲突 |
| 人工 | 姓名补全/格旁选人/详情选择可填入manualAllowed=true人员，写入edit-cell或edit-slot草稿；精确搜索姓名可直接回车；原因明确标注填入后报警 |
| 结构失败 | 同岗重复搭档、未知身份/位置、任务/版本过期仍拒绝该次变更；未知资料仍要求完善，不默猜任务 |
| 最终校验 | 原validator独立标红资格、冲突等问题，姓名不清空；违规草稿可带问题采用/归档/导出；修改草稿不追改原正式，明确采用时保存当次问题 |
| 持久化/输出 | 原草稿/撤销/完整备份保持，刷新重启显示有问题姓名；无新人工长期配置字段，配置Excel合同不变 |
| 回归 | 先红manualAllowed与浏览器禁用证据，资格不符/跨航班重叠写入→保留姓名→重启/备份恢复→带问题采用保持报警；正常候选与未知资料阻挡继续验证 |

# 2026-10-31 值班备勤轮值

| 消费点 | 合同 |
| --- | --- |
| 输入 | domain统一验证日期、资格ID、覆盖与三槽不重复 |
| 候选 | 正常启用非分队长且对应资格；请假不擦除人工记录 |
| 求解 | 独立月度轮值先覆盖再均衡，不变航班求解硬差距 |
| 人工 | 同排互换后复查全部资格，非法原子拒绝，清空留缺口 |
| 独立校验 | validate_roster_row检查重复/资格/可用性，覆盖后复查并标红 |
| 输出 | 计划轮值与正式重点次数区分；次日跨月日期明确 |
| 迁移 | dutyRoster统一JSON/SQLite/Excel/备份；独立表预览导入 |
| 回归 | 均衡、跨月、休假/重复破坏、手调恢复、旧格式/重启/备份 |

# 人员状态与删除（2026-10-31）

| 环节 | 消费点与边界 |
| --- | --- |
| 输入事实 | domain.PERSONNEL_STATUSES、validate_personnel_details、personnel_states和member_available唯一负责正常/病假/休假/停用/删除可用性 |
| 候选与求解 | solver、manual_editing候选统一消费人员可用性；不可用人员不参与航班数硬差距比较；普通在岗人员仍严守差1 |
| 人工与最终校验 | validator拒绝不可用人员后置安排，问题草稿标红且不能正式采用；原正式按快照，不追改 |
| 持久化 | Workbench逻辑删除保留原ID与旧计划、正式及重点历史；重新新增同名使用新ID，原名单顺序保持 |
| 配置迁移 | Excel人员状态五列、JSON peopleStatus/peopleRemarks、SQLite与完整备份；旧缺字段保留，空库正常/空备注，显式空备注清除 |
| 输出 | 当前配置清理删除人员固定/原名单/额外占用引用但不降低人数；删除人员后续历史修正仅随完整备份迁移 |
| 验证 | tests/test_personnel_management.py覆盖休假后求解/候选/手改反向拒绝、历史保留、同名身份、旧格式与空库重启实际使用 |

# 分队长最后使用与航班数例外（2026-10-31）

|环节|消费点/边界|
|---|---|
|输入事实|domain.Member.team_leader、Scenario分队长策略；人员标记布尔、默认关闭；策略默认最后使用/豁免开启，Owner确认分队长不比较硬差距|
|配置迁移|peopleTeamLeaders与teamLeaderSettings进入完整Excel/JSON/SQLite/备份，旧缺字段保留、空库默认，显式否取消|
|候选池|仍可选合格出勤分队长；标记不赋予资格或豁免冲突|
|求解|缺岗最少→分队长任务数最少→原重点/接续偏好；比较人员集合由domain统一提供，普通人含0班|
|人工/后置|手工合法使用分队长允许；最后使用是偏好，不把手排标非法|
|最终校验|相同domain比较集合；只豁免航班数，其他硬规则不变|
|输出/快照|人员概览仍显示分队长真实航班数；正式场景固化标记与规则，旧正式不追改|
|反向回归|合法豁免结果关豁免后差距超限拒绝；分队长无资质/冲突仍拒绝；迁移重启实际求解|

## 2026-10-31 同航班专岗专人硬规则

| 环节 | 消费点 |
| --- | --- |
| 输入事实 | ImportedRoleConfig.exclusive_same_flight / JSON exclusiveSameFlight；每岗位独立开关，默认false，旧文件缺字段保留当前值 |
| 候选池 | manual_editing.slot_issues→validator→domain.task_pair_conflict；含零需求cell-candidates试排，冲突人禁选并解释 |
| 求解器 | solver原任务对约束直接消费task_pair_conflict；专岗限制先于接续/可兼任，缺岗也不放宽 |
| 人工/后置 | edit-cell/slot、move-assignment、预览和恢复仍独立复查；非法组合可保存问题草稿、标红，正式门禁拒绝 |
| 最终校验 | validator消费同一任务对事实，同航班不同岗/共享覆盖相交时任一专岗即冲突；同岗分段不新增限制 |
| 输出与存储 | Task快照、Scenario JSON、SQLite、Excel岗位性质及当天例外、完整备份同值；旧正式默认关闭不追改 |
| 回归 | 先合法后手改破坏、自动分人、接续/兼任不能绕过、不同航班、特殊非重叠分钟、共享大件、开关关闭、Excel显式否/旧缺列保留/空库重启实际使用 |

## 2026-10-31 统一规则保存（工作流，无新硬规则）

| 环节 | 消费点与边界 |
| --- | --- |
| 输入 | 规则页草稿包含基础轮换/走动、balanceSettings、handoffPreferences及可选day/轮换；未保存不写库 |
| 候选/求解/最终校验 | 规则事实和消费点不变；仅消费既有持久配置/计划快照，不读取页面未保存内容 |
| 原子提交 | Workbench.save-rules复用原save-config/update-balance-settings/save-handoff-preferences/save-input的验证owner；一个expectedRevision，一个_commit，失败全部回退 |
| 人工修改 | 切页/日期/方案提供三选项，失败保留；当天例外输入失效先确认；正式快照保留 |
| 输出与恢复 | 复用完整Excel/JSON/SQLite/备份，无新字段；空库导入重启后实际求解验证硬差距；搜索/折叠不进入业务配置 |
| 回归 | test_rules_save覆盖原子回退、CAS、历史隔离、日输入失效及Excel迁移实际效果；balance-ui和workbench.browser覆盖真实Chrome交互 |

## 2026-10-31 重点岗位轮换与航班数差值

Owner已批准plan方案并要求统计展示、展开次数和手动加减参照值机。硬规则仅为可关闭的当天航班数最大差距；重点轮岗为软偏好。

恢复边界：priority_statistics.next_formal_revision取已有正式序号与当前工作台revision最大值再加1，避免恢复后CAS从1重置使新归档输给旧设备序号；重复采用等同正式快照保持原序号。test_priority_statistics反向覆盖旧100/恢复1/新归档及月账选择。UI数字清空必须保持草稿空值并阻止保存，不得由Number("")变成硬上限0；balance-ui及最终实际EXE键盘/切页/不写/填1/重启专项保护。

| 事实与唯一owner | 输入/候选/求解 | 人工/最终校验 | 输出/保存/迁移 | 反向与迁移回归 |
|---|---|---|---|---|
| domain.task_flight_ids / member_flight_participation拥有参与航班身份；同人同航班多岗/多段去重，共享任务按covered_flight_ids，无航班占用不计 | 当日Scenario.members全部参加者含0班；solver人员×航班布尔变量从同一任务覆盖事实构建，限制最大减最小<=flight_count_gap，None关闭；部分安排不放宽 | 候选仅局部资格/冲突禁选，整表差值不得阻死阈值0的逐步手排；validator独立按去重集合复算并拒绝超限。违规手改可存红色草稿，采用/归档/正式输出拒绝 | schedule.memberFlights统一投影供人员概览，不用姓名反查matrix；正式Scenario保存当日规则，新设置不追改旧快照 | 合法结果后人工集中给一人必须拒绝正式门禁；阈值0/1/关闭、0班、共享大件、重复段、多岗、固定无解/partial、安全停止 |
| domain.member_role_flight_counts拥有同人同岗同航班一次计数；Workbench正式历史按目标日之前本月、同日确定最新正式一份，稳定memberId关联 | 合格无冲突候选不变；最大覆盖后优先重点当天分散/月累计少者及上一归档换人，再原接续与普通偏好；不因轮岗空岗 | 加减修正仅改变月累计账，不写正式历史、不授资格；有效次数最低0，越界整笔拒绝 | balanceSettings含开关/岗位名单/gap/月次数修正，完整Excel/JSON/SQLite/备份统一保存；peopleIds可迁移稳定身份；历史页同值展示实际次数/修正/有效数，切月/筛选/展开及加减 | 月界/未来/草稿不累计、同日多方案去重、减至0/下界拒绝、重启与Excel空库实际轮岗；完整备份保留历史，配置不伪装历史备份 |
| domain.default_balance_settings / validate_balance_settings拥有设置默认与合法性，Workbench每份计划快照独立采用 | 新日/明确重排应用当前设置；旧计划缺字段默认关闭，旧正式快照不追溯 | 重新排班设置变化须生成或明确更新当日输入，原正式快照独立校验不被新规则改写 | 旧配置缺字段保留已有设置，空库默认关闭/空名单；显式空名单可清空，非法ID/岗位/日期/类型拒绝整笔 | 修改→导出完整Excel→空库导入→重启→实际求解/校验；JSON备份恢复与旧格式保留，原日输入/历史不变 |

## 2026-10-31 岗位空缺与统一人选检查（无新硬规则）

| 事实与唯一owner | 候选/求解 | 人工/最终校验 | 展示/保存/迁移 | 回归 |
|---|---|---|---|---|
| 候选为目标任务及位置的试排结论；manual_editing.candidate_options复用slot_issues→replace_slot→validate_schedule，冲突仍domain.task_pair_conflict | 保留原资格/出勤/锁定/轮换及同人其他任务检查；无新求解目标；先阻挡再给可接续/可兼任标签，不把一对允许扩大为全人空闲 | 原编辑和采用仍独立校验；已在本岗标签不放宽搭档重复限制，无关既有冲突过滤合同不变 | Workbench.candidates只读附revision与缺勤说明；上方按目标名单、格旁＋、姓名补全、详情共用同链，等待/失效/失败清旧结果；详情选择框pending/失败禁用，首位置人选按版本/离开保护，分类和原因真实展示；候选不持久化 | test_candidate_availability：同航班普通岗/兼任开关、跨机确认与走动、0910实际占用、迟到/离岗/资格、固定/搭档/轮换、只读/revision/重启；前端候选与真实Chrome |
| 0/未知人数空格只读试排；Workbench.cell_candidates深拷贝真实输入，普通格临时requiredCountOverride=1后走原_scenario和candidate_options | 仅查询单位置可行性，不生成真实需求/安排，不授资格；资料不齐、大件需求/轮换待确认、多段未选段、第二临时位置或旧方案试排改变已有任务分段均unverified且available=false | 选择后仍走原cellDrafts/configure-cell显式确认真实人数/方式与独立校验；普通环节无需分钟，timed需真实起止 | GET /api/workspace/cell-candidates/{plan_id}/{cell_id}返回requiresConfiguration及待确认标签；禁止no-taskId时把roster映射为available=true；SQLite/Excel/JSON合同不变 | test_cell_candidates及schedule-editor-candidates：0/未知人数副本试排、资格/冲突/缺勤、未知资料拒绝、查询不写、临时草稿和原配置迁移 |
| 缺口事实为validator.coverage_shortfall，其余问题保留原code/关联任务；前端只分类颜色 | 不改变人数或solver安全草稿合同 | 红色问题优先琥珀缺口；0需求中性，待选人显示原准备引导，正式按自身快照显示 | 两布局/共享大件一致；显示“待安排”及原因，不能只靠颜色；HTML/图片继续要求完整合法并采用，无新增草稿导出/设置/迁移字段 | 缺1/全缺/补齐消色、同格红优先、0需求/待选人/正式隔离、共享段；合法安排后人工破坏仍拒绝正式采用及导出 |

普通环节不推断真实分钟；跨航班本人接机完成后可支援，但重叠环节须明确衔接且无其他冲突，有走动分钟须有事实核对。旧正式releasePolicy=flight的结束/拆段合同仍保留。历史名单仅说明流向，不授资格、不确认跨机、不猜0910结束。无新增持久设置；人工维护配置和完整备份沿用AGENTS永久迁移合同。

## 2026-10-31 增加岗位新增独立行

Owner确认：本航班已有“督导”仍可按分类增加，生成全局唯一“督导2、督导3”等，初始复制性质和资质，人数独立。Workbench.add-template-role负责唯一命名和整笔复制：sourceRoleName可选来源、roleName可空或指定；新行复制来源已保存的workMode/concurrent/默认人数、当前航班人数/方式例外及资质，停用人员不自动启用。未知来源/空输入/错误类型拒绝整笔；已有全局岗位尚未属于本航班则只加入，不覆盖原设置/资格。新角色只加入所选模板roleNames，其他航班与旧日计划不写；来源仅用于创建时复制，不建立隐式持续联动或复制接续偏好。

候选/求解消费新角色自己的资格、人数、环节和兼任；人工修改/最终validator继续使用独立角色身份及原硬规则。新增普通行不会凭大件名称继承原岗共享轮换，界面选择大件来源时明确提示；不增加新的共享轮换业务规则。Excel/JSON/SQLite既有roleConfigs/人员资格/flightLibrary.roleNames和roles完整保存，无新增持久字段；来源分类为一次创建输入，复制后的实际值参与完整配置迁移。前端表格按模板成员顺序显示，新增后行数+1、定位新行、就近反馈，失败不显示成功。

回归：test_add_template_roles先失败证明已有分类拒增、分类空名称拒绝及未知分类未校验，后覆盖全局唯一后缀/初始性质与资格/独立人数/停用/已有未加入角色不重授/非法整笔拒绝/Excel→空库→重启→实际兼任排班；Chrome覆盖已有分类连续增加、新行计数/表尾/焦点、原其他航班/历史不写与配置迁移。原程序真实副本鼠标点击和安装后只读复验单列记录于plan。

## 2026-10-31 通用偏好与航班岗位独立成员

domain.general_handoff_preferences拥有旧偏好合并：按来源岗合并启用目标、关闭顺序；全停用保留停用，已显式通用设置保持顺序。validate先检查旧字段合法性，再规范化；preferred_handoff/handoff_rank消费通用*或独立Scenario旧航班偏好，不授资格/豁免冲突。Workbench载入/保存/恢复与Excel导入都规范化，求解只消费现有真实接机/登机任务，缺失目标无奖励；最终validator原硬规则保持。UI规则页/配置页只展示一份，Excel适用范围=通用，旧航班号表兼容合并、缺表保留/空表清空。JSON/SQLite同值，旧草稿/正式历史不写。

flight_configuration拥有模板roleNames成员合同与旧缺字段默认全通用岗位；每航班成员显式持久后，新通用岗位不自动加入其他航班。save-template/add-template-role/copy均经统一validate；fill_day新计划与前端prepareWithConfiguration准备副本把非成员投影为既有0需求格，不改变矩形日合同。候选/solver/imported_required_count消费真实0人数不生成任务；手动临时加人仍走原显式需求及validator；无新增硬规则。Excel航班模板可见本航班岗位列（顿号分隔，空白明确无岗位，旧缺列沿用通用岗位），模板岗位保留人数/方式例外。完整备份保留roleNames；实际旧日仍按原输入，最新模板仅在明确载入/新日使用。测试覆盖各航班增删隔离/新角色不扩散、缺失目标、旧格式、非法事务不写、导出/空库/重启/实际任务生成和原历史保持。

<!-- 2026-10-31 航班时间表头 -->

表头时间补充（仅展示）：domain/后端既有Flight窗口字符串为事实；web.flightHeader共用渲染，草稿/正式取当前Schedule.flights，无法生成任务时取day.flights；不重新解析或推断跨日。flightGrid与原矩阵、HTML/PNG消费同一表头，PNG分两行绘制。求解/候选/人工事务/最终校验/存储无变化、无新增配置。定向与Chrome验证跨日/两布局/正式快照/HTML/PNG，岗位格继续不显示重复时间。

## 2026-10-31 计算流程与抓手拖动（无新硬规则）

共享大件显示补充：既有imported_schedule任务covered_flight_ids→task_matrix在覆盖列重复投影为事实owner；web.sharedBaggageGroups只按相邻列交集task_id合组，并以task_id去重/按已给start排序，不重新解释时段、需求或姓名。workbench保留每段任务身份，shared详情按任务选择真实所属cell并复用edit-cell显式taskId，选人/拖动复用edit-slot/move-assignment与validator。未完成输入不隐藏，独立任务不因同人合并。flightGrid按逻辑colspan转实际列宽，HTML/PNG同源；无新增solver/validator/持久设置。定向单元/Chrome验证部分共享尾段、去重/传递/独立/空列、缺口报警、逐段打字与原安排保持。

航班时间排序补充：projections.flights_by_start统一使用domain.parse_minutes对展示副本稳定排序；schedule_data为草稿/正式提供有序flights，snapshot.flightColumns为不能生成任务的空表提供同序列。UI消费其顺序，不解析时间或写回配置，支援仍在末尾。solver/validator/正式Excel/原持久日输入与分配不变，HTML/PNG消费页面。test_checkin_parity覆盖乱序、同开始不同结束、次日、草稿/正式/无任务与原库不写；Chrome验证航班标题和原拖动/人员对应链路。无新硬规则或配置字段。

| 事实owner与输入 | 候选/求解 | 人工/最终校验 | 输出/持久化 | 回归 |
|---|---|---|---|---|
| RunControl拥有七阶段和完成比例；实际stage调用推进，只有finish提交成功标完成 | 不改变solver目标/资格/冲突，安全候选仍独立校验；不是搜索百分比 | 停止/失败不到100%，已有版本保护及停止不写保持 | get返回完整steps和percent，页面只展示；运行进度不属于可迁移业务设置 | test_checkin_parity完整流程/保存前不完成、停止状态不写、失败与CAS；Chrome提交前停止、完成状态 |
| ScheduleDrag只采集来源身份和落点，复用requestDrop/performMove | 不适用：人工拖动不另求解或授资格 | 原move-assignment→_scenario→独立validator；单人/多人/轮换、临时需求与撤销不变；违规草稿报警，正式采用拒绝 | 原草稿/撤销持久与正式快照不变；HTML/PNG移除抓手；无新配置字段 | Chrome原生与抓手鼠标/触屏跨航班交换、搭档保留、空位移动、取消不写/撤销/正式只读；既有人工破坏规则反向回归 |

## 2026-10-30 手动接续先后顺序（软偏好）

| 输入事实owner | 候选/求解 | 人工修改 | 最终校验 | 输出/恢复 | 证据 |
|---|---|---|---|---|---|
| domain.validate_handoff_preferences拥有可选ordered布尔合同；handoff_rank在开启时按boardingRoles先后返回名次，旧缺字段/关闭同等优先 | 候选资格不变；solver仅对合法同航班pair先最大化偏好匹配，再最小化名次，位于覆盖/继续工作之后、旧支援轮换历史之前 | 规则页/配置页共用编辑器，勾选增删、启停、排序开关及上下移，Workbench保存原对象不重算 | 无新硬约束，未满足顺序仍合法；validator独立复查资格/人数/冲突；无资格第一目标不能被偏好授资格 | Excel第五列按顺序优先+原岗位文字顺序，旧四列同等优先；JSON/SQLite保持ordered和数组顺序，复制航班保持；不重写旧班表 | tests/test_handoff_preferences覆盖反转次序、第一岗无资格时合法回退、旧缺字段/关闭、错误类型拒写、Excel旧新/空库/重启/JSON/实际求解；Chrome两页编辑/排序/导出/空库/重启继续编辑 |

## 2026-10-30 可迁移岗位接续偏好与配置完整性

| 事实/设置 | 唯一owner与输入 | 求解/候选 | 人工/最终校验 | 输出/持久化 | 回归 |
| --- | --- | --- | --- | --- | --- |
| 航班×接机来源岗→优先登机岗集合及启停 | domain偏好合同/匹配/历史建议；Workbench保存handoffPreferences | 同航班已可行衔接变量增加偏好目标；不授资格、不增需求、不影响可选候选 | 手改不满足偏好仍合法；独立validator原人数/资质/冲突/固定不变 | 配置按航班勾选；SQLite/JSON完整保留；完整Excel可见偏好表，有表替换/空表清空/旧表缺失保留；导出用最新长期值 | 求解去向、关闭/覆盖与硬约束优先、Workbench→Excel→新库→重启→求解、JSON恢复/旧格式/非法失败不写 |
| 所有手动维护设置可迁移 | AGENTS永久纪律；当前配置/页面逐项核对 | 计算设置由原domain消费；显示偏好不参与求解 | 迁移不覆盖旧正式历史 | 配置携带长期业务与显示偏好；历史随完整备份；缺字段策略显式 | 人员启停/最新资格与人数/轮换走动/模板星期/岗位性质及接续/显示偏好往返与恢复 |
| 当天与长期配置同时保留 | dayOverrides仅表达当天岗位/资格/轮换走动差异；excel_io.configuration_day统一应用；原领域事实仍ImportedRoleConfig/ImportedDay | Workbench导入新日后沿用原转换与solver，不重复加规则 | 例外只影响新日，正式与历史保持；最终校验原资格/人数/冲突 | 同一完整Excel三张可见当天例外表、JSON原日计划；缺配套表拒绝，旧文件无表保持旧含义 | 长期人数2/当天4、长期有资格/当天取消、轮换85/长期95空库迁移后两者分别保留；旧缺省性质/姓名改名/旧计划不写 |

## 2026-10-29 人工编辑上下文（不改变排班硬规则）

## 2026-10-30 连续归档事务（不改排班硬规则）

| 事实/流程 | owner/输入 | 求解及最终校验 | 页面/存储/输出 | 回归 |
| --- | --- | --- | --- | --- |
| 归档并生成才推进日期 | Workbench.archive-next事务，显式day与参加人员 | 复用_solve与独立validator；当前_adopt仍独立复验 | 失败不_commit、不创建空日，run留原窗及选择；成功新日可接续归档，日期默认+2 | 无人mock不调solve且不写；无解/缺资料保留原草稿和正式；连续三轮+2/恢复；Chrome真实副本连续生成 |
| 确认前显示已选资料 | 准备窗day/selected，只消费人员与航班选择 | 人数显示不是可行性结论，不代替后端资格/冲突校验 | 未选航班/人员禁用归档确认，计数/直达选人；失败可修改重试/取消不写 | Chrome选择计数、禁用、失败后保留全部选择、取消与重试 |

下文允许无出勤归档创建新日草稿的旧流程由本节覆盖；原历史空草稿/完整issues/资格与正式快照保持。

2026-10-29归档准备提示修复：无硬规则改变。无出勤准备保护归档复用Workbench._solve，仍由domain/转换器拥有出勤和任务事实，validator最终拒绝未完成安排。preparationNotice是只读工作流程投影，UI只在此状态收起coverage_shortfall/lock_missing派生显示；旧needsPreparation草稿同时收起由直接重排准备修正的missing_work_time/incomplete_work_time/incomplete_availability旧提示，完整issues和valid不改变；不授资格/减人数/偷带出勤。solver诊断通过manual_editing.issue_data转业务名并限制4条摘要，不改求解事实。test_archive_guidance先失败证明无人不调solver/原正式保留/旧长消息只读/真缺人显示航班岗位；Chrome覆盖0人归档+2、刷新、选人继续、取消不写。

| 事实/规则 | 输入与领域owner | 候选/求解 | 人工与最终校验 | 输出/保存 | 回归 |
| --- | --- | --- | --- | --- | --- |
| 单独替换一人保留搭档 | manual_editing.replace_slot只改指定位置 | 候选slot_issues用同一替换再独立校验；求解不变 | edit-slot/preview-edit同接口；validate_schedule保留重复、资格、出勤、固定和冲突门禁 | SQLite/JSON包含草稿与单步撤销；正式Excel仍只输出采用快照 | test_manual_editor双人保留/重复/不出勤/无资质/正式保留；Chrome真实选人、输入、撤销 |
| 冲突双方可定位 | domain.ValidationIssue.related_task_ids；冲突事实仍task_pair_conflict | 求解逻辑不变 | validator产出双方，manual_editing只翻译名称；所有人工修改后snapshot独立校验 | 界面双方红格、格旁原因与保存待处理提示；无效草稿不采用 | 合法安排后加重叠反例、双方关联/拒绝采用/撤销 |
| 每周航班为可编辑模板 | domain.validate_weekly_flights；日0至六6，只存明确航班号和窗口 | 不带姓名/资质/出勤/固定/0910；选择后仍走原日表转换与求解 | 应用待保存，现有正式不变；恢复校验范围、重复、时间、禁止夹带分配 | 可选JSON/SQLite、完整Excel可见每周航班表；缺表导入保留既有预设 | test_weekly_flights非法/往返/取消只读/空预设/缺表保留；Chrome维护与重启 |

## 2026-10-29 环节与岗位性质改造（已实现）

| 事实/规则 | 输入与领域owner | 候选/求解 | 人工与最终校验 | 输出/保存 | 回归 |
| --- | --- | --- | --- | --- | --- |
| 同航班接机后登机，无逐格分钟 | domain工作方式/任务事实，角色配置显式选择 | 共用domain任务冲突判断 | 同阶段仍拒绝复用 | 明示环节，Excel/JSON保留 | 合法衔接、手改同阶段冲突 |
| 跨航班释放按本人任务完成 | 实际任务结束；环节需确认航班衔接 | 不凭开始早放行，不忽略其他任务 | 未确认/其他工作冲突不得采用 | 日计划/Excel保留衔接，快照自含 | 原航班未结束可支援；删除衔接拒绝 |
| 岗位常规/兼任可配置，许可非传递 | domain岗位性质；Owner确认督导允许兼任 | 每对任务消费性质 | 两常规岗位冲突不能靠第三兼任岗消除 | 基础/当天/Excel/恢复保存 | 三岗反例、关开关反例 |
| 无分钟不假称走动/占用已验证 | domain范围与输入规则 | 不能忽略明确走动/占用 | 不确定须补事实，保留草稿 | 候选/诊断说明缺项 | 走动及额外占用反例 |

# 硬规则传播矩阵


2026-10-29交互消费补充：无新增/放宽硬规则。打字edit-cell、临时configure-cell、拖拽move-assignment均经Workbench同一_scenario及snapshot独立validate_schedule；资格仍domain.has_role_qualification，人数/时段仍imported_required_count及日表转换。草稿可有冲突但标红，_adopt为采用/归档共用门禁；archive-next单事务归档当前与创建未来方案，准备窗+2仅工作流程默认且可手改。cellDrafts为未完成输入，pending问题阻止自动/采用，并由_validate_document校验引用和姓名；JSON备份/恢复保留。UI只对应任务/人员身份，不另写时间/固定/出勤判断。输出沿用原正式快照/Excel，候选查询不改规则。反向回归test_schedule_editing覆盖未知姓名、资质不符、未排齐、移走固定、复杂轮换不猜、错误引用/版本/磁盘失败和正式快照保留；既有规则逐项测试继续全量执行。

2026-10-29历史资质模板：Owner授权从历史生成建议，确认本批同音拼写统一（李扬→李杨）；来源及原文保留在工作簿。历史整理不自动授予资格，经资质表预览确认后，Workbench一次事务补充既有Personnel资格；求解、人工候选及独立最终校验仍用domain.has_role_qualification。无新增硬约束；导入不修改任何已保存计划/正式快照，不猜人数/时段/出勤。测试见test_qualification_import：历史去重/标签排除、格式回退、预览只读、幂等、停用保持、旧计划/采用快照隔离、API往返；真实Chrome覆盖15人/115项导入取消/确认/重启。


2026-10-29界面对齐：资格页面改为岗位→人员勾选，`save-role`一次事务更新既有Personnel资格，domain资格事实、求解/候选/独立校验不变；新基础岗位人数/轮换/走动配置仅作新计划默认输入，已有计划须明确`apply-config`，采用快照不变。无新增排班硬规则；反向资格伪造、已采用快照隔离及配置失败回退继续回归，见tests/test_workspace.py的岗位配置、旧v2、无效选择/恢复、apply-config快照隔离及浏览器完整流程。

本文件记录已实现硬规则、已确认但待实现的规则和导入结构规则的事实 owner 与消费路径。手工保存接口必须和求解结果一样经过独立最终校验；不存在或尚未接入的阶段不能被当作已覆盖。

2026-10-29完整修复消费链路：连续时间、独立人员与资格、当天计划版本、草稿/预览/采用和正式输出已贯通。既有接手评估记录开工时缺陷，当前实现以spec和本矩阵为准。

| 硬规则 | 输入事实 owner | 候选池/求解 | 人工或后置修改 | 最终校验 | 输出 | 回归 |
| --- | --- | --- | --- | --- | --- | --- |
| 服务日连续时间与次日 | `domain.parse_minutes/format_minutes`；输入显式当天/次日，历史晚班后TW616次日已确认；航班原文保留 | 日表转换、轮换、支援释放统一连续分钟轴；不增加最大值勤时长 | 当天/次日可明确录入并修改 | 既有重叠/资格/出勤/释放校验消费连续分钟；无效钟点拒绝 | API、SQLite、备份、页面、Excel保留日期含义 | `tests/test_day_time.py`、工作台跨日回归、完整30天回放 |
| 草稿与正式采用分离 | `workbench.Workbench`计划输入版本、草稿和采用快照；Owner已允许保存未排齐草稿 | 自动只生成预览；人工逐人填草稿 | 不完整/冲突明确标红，正式采用重新校验 | `validate_schedule`为正式采用唯一合法性入口；草稿结构检查不冒充正式通过 | 草稿可恢复，正式Excel仅采用快照 | `tests/test_workspace.py`：未排齐恢复/拒绝采用、预览不覆盖正式、写失败和旧版本拒绝 |
| 独立人员与当天选择 | `Personnel`稳定身份；日表显式roster与当天资格/出勤快照 | 现有转换器消费明确roster，不依赖旧格姓名 | 新增人员可手排，改名不改历史快照 | 现有资格、出勤、固定、人数、冲突最终复查 | 页面/备份/结果快照含稳定身份 | 工作台空白计划/新增人员与反向篡改 |
| 日表显式固定人员 | `ImportedCell.fixed_member_names`；原名单默认不固定 | 转换为`Task.locked_member_ids`；求解器按锁定任务建立等式约束 | 日表勾选；采用结果重新转换并校验 | `validate_imported_day`拒绝未知/重复/不在本格人员；`validate_schedule`拒绝删改锁定 | 页面、SQLite/备份、Excel“固定人员” | `tests/test_fixed_members.py`、`tests/test_imported_schedule.py`：合法结果后置换人必须拒绝 |
| 真实日表转换与自动排班 | `ImportedDay`显式人数、工作方式、特殊实际时段、人员资格、出勤、固定、额外占用、备注核对及走动分钟；大件取航班窗口 | `imported_day_to_scenario`消费明示事实及所选环节，不假设实际分钟；大件合并有需求窗口、空档分开并切轮换段 | 自动结果预览后由页面确认；接受API重新转换，不信任客户端状态 | `validate_scenario`、`validate_schedule`独立检查任务数、资格、时间、锁定与轮换 | 保存至SQLite/备份；Excel附“自动排班结果”页 | `tests/test_imported_schedule.py`、`tests/test_work_phases.py`和`tests/test_fixed_members.py`：缺项、固定篡改、窗口/轮换、走动占用与环节 |
| 额外占用与0910备注 | `ImportedDay.extra_occupancies/remarks_reviewed`；没有明示时不解析备注为任务 | 显式占用转换为锁定任务；未核对的支援备注阻止求解 | 占用按人员和时段录入 | 独立验证覆盖重叠、锁定和起止 | 页面、备份、Excel“额外占用”和“自动排班设置” | `tests/test_imported_schedule.py`、`tests/test_fixed_members.py`：占用重叠及Excel往返 |
| 跨航班走动预留 | `ImportedDay.transition_minutes`转为`Scenario.minimum_transition_minutes`；默认0，范围0至180分钟 | CP-SAT禁止不同航班任务间隔短于输入分钟数 | 人工修改最终仍经过校验 | `validate_schedule`独立复查间隔 | 页面、SQLite/备份、Excel“自动排班设置” | `tests/test_imported_schedule.py`：间隔不足的合法结果反向拒绝 |
| 特殊航班岗位人数例外 | `ImportedCell.required_count_override`；`imported_required_count`统一解析例外优先、空值沿用岗位配置 | 日表转换按解析后人数生成或略过任务 | 日表例外输入和confirm接口 | `validate_imported_day`检查非负整数，空值保留继承 | 日表有效人数、SQLite/备份、Excel“岗位人数例外” | `tests/test_staffing_override.py`：0、清空继承、往返重启、坏备份拒绝；新一天不沿用例外 |
| 导入日表当天出勤输入 | `ImportedDay.member_availability/attendance_mode`与`domain.attendance_bounds`；名单由`imported_member_names`提供 | duty参加者无特殊时间可转换；显式限制必须完整，旧bounded保留原合同；0需求格不排除参加者，未参加者不可安排 | 新日准备带入人员选择供确认，不偷带特殊限制；人员输入复用同一边界 | 日表检查布尔值、已知且唯一人员和完整有效时间限制；validator复查任务范围 | SQLite/备份/完整Excel保留出勤方式与限制；每个新日仍须确认 | `test_member_availability`、`test_imported_conversion_regressions`、`test_work_phases`及日常出勤回归：新旧合同、半填/越界、0需求/备注人员及恢复 |
| 导入人员可做岗位配置 | `ImportedDay.member_configs`；日表名单只提供候选人员，不推导资格 | `imported_day_to_scenario`只允许显式勾选的资格 | 人员勾选与日表确认接口 | `validate_imported_day`拒绝未知人员/岗位、重复人员/岗位；空选不赋予资格 | 人员配置UI、SQLite、备份、Excel“人员配置”工作表 | `tests/test_member_config.py`：不推导、空选、往返恢复、非法配置与坏备份拒绝 |
| 岗位配置同时需要人数 | `ImportedDay.role_configs`和`ImportedCell.required_count_override` | 日表转换按统一解析的同时人数生成任务；空白不推导、0跳过 | 导入页岗位配置与逐航班例外 | `validate_imported_day`检查空白、0、非负整数、重复和未知岗位 | 日表、SQLite、备份、Excel“岗位配置”工作表 | 岗位配置空白/0、例外沿用、往返、重启和非法输入回归 |
| 岗位资格与到岗时间 | `Member`、`Task` | `solver._eligible` | `POST /api/manual-assignments` | `validate_schedule` | CLI、页面校验结果 | 晚到岗、手工保存反向测试 |
| 时间重叠与支援占用 | `Task.start/end` | CP-SAT 重叠约束 | `POST /api/manual-assignments` | `validate_schedule` | 人员路径、页面红色问题 | 支援冲突与篡改测试 |
| 接机后同航班优先与跨航班释放时间 | `Task.service_stage/timing_mode`、`Scenario.release_policy/phase_handoffs`及明确时段，`domain.task_pair_conflict`拥有兼容判断 | 新task方案按本人任务完成，重叠环节跨机须确认并检查走动/其他任务；同航班优先为软目标；旧flight合同继续由io标准化保留结束边界拆段 | 所有人工入口按同一场景与目标位置检查；采用重新转换并独立校验 | `validate_schedule`逐对复查，不能因接机可衔接而忽略另一普通岗/显式占用；旧正式仍按其flight合同 | 环节输出名称、不伪造分钟；旧拆段身份和正式快照随SQLite/备份保留 | `tests/test_work_phases.py`、`tests/test_candidate_availability.py`与旧`test_flight_handoff.py/test_imported_conversion_regressions.py`：新旧合同正反向、确认/走动/其他任务与恢复 |
| 固定岗锁定 | `Task.locked_member_ids` | CP-SAT 等式约束 | `POST /api/manual-assignments` | `validate_schedule` | assignments、页面红色问题 | 固定岗测试 |
| 轮换连续与候选池 | `Rotation` | `solver._eligible`、任务覆盖 | `POST /api/manual-assignments` | `validate_schedule` | 任务矩阵保留子段 | 连续覆盖、候选池外手工修改测试 |
| 外援不作为内部员工 | `ExternalSupportRequirement` | 内部任务变量不创建外援员工 | 页面只显示外援名额，不提供人员选项 | `validate_scenario`、`validate_schedule` | 外援标签、人员路径 | 外援分账测试 |
| Excel斜杠名单与外援标签 | `excel_io._cell_semantics`、`ImportedCell.internal_names/external_labels` | 名单只形成已具资格人员的候选；未配置人数或资格时不自行安排；外援标签不作为内部人员 | `POST /api/imported-day/confirm`及自动结果确认 | `validate_imported_day`与`validate_schedule` | 导入矩阵、Excel导出、自动结果 | 20260802、20260814解析回归；未确认斜杠拒绝保存，原名单不自动锁定 |
| Excel 0910/到岗备注 | `ImportedRemark`、`excel_io._remarks` | 不适用：未确认的 0910 时段不生成任务 | 导入页只展示原始备注 | `validate_imported_day` 不推导时段 | 导入矩阵备注、Excel 导出 | 20260802 支援和到岗备注回归 |
| 大件岗覆盖与轮换 | `ImportedFlight.window_start/window_end`、岗位人数例外、人员资格/出勤/固定人员、`BaggageRotationSettings` | `imported_day_to_scenario`合并有需求航班窗口、切开空档并按设置切段；`solver.solve`在轮换边界优先换人，同一轮换编号跨航班边界优先留人；换人是软目标，人手不足允许续用 | 页面固定勾选按来源航班窗口锁定；确认结果重新转换并验证 | `validate_scenario`、`validate_schedule`拒绝缺人、重叠、资格/出勤不符和锁定被破坏 | 预览显示每段人员/时间；保存、备份和Excel“自动排班结果” | `tests/test_imported_schedule.py`：航班时间优先于岗位时间、窗口并集/空档、边界稳定、轮换偏好、固定人篡改拒绝 |
| Excel 接机后登机支援候选（提示，不是硬规则） | `ImportedDay` 的航班窗口、`接机1/接机2` 名单及 `登机*` 单元格 | 不适用：仅投影候选，不进入 `Scenario`、候选池或求解 | 不生成分配，因此没有手工修改入口 | 不适用：不改变任务或排班合法性 | 导入校验 API 与页面显示来源航班、目标航班、候选人员、岗位和按航班结束时间计算的核对范围 | `tests/test_excel_io.py`：需有重叠且目标结束较晚、结束边界、排除已列入目标登机的人、API 输出 |
| 导入岗位时间完整性（输入校验） | `domain.imported_work_mode`拥有工作方式；特殊实际时段的`ImportedCell.work_start/work_end`由页面或Excel录入；大件按航班窗口 | 接机/登机/全程保障无需逐格分钟，以环节范围生成任务；timed需完整真实起止，大件不消费该格时间 | 页面修改复用日表校验/转换；不将环节范围当真实起止 | `validate_imported_day`检查已填写时间成对有效；`validate_schedule`继续检查环节兼容及实际占用 | Excel/JSON/SQLite保留方式和时间例外；正式环节显示名称 | `tests/test_work_phases.py`及旧Excel/日表回归；`test_cell_candidates`证明未知timed不伪称可用 |

Excel日表资料齐全时转换为一次性`Scenario`并走求解、独立校验、预览、确认、保存/恢复和导出。普通岗位按所选环节生成范围，不要求逐格分钟；特殊实际时段只消费明确填写的值，大件按有需求航班窗口并集。0910只有明确人员与占用时段才约束求解；旧导入接机支援提示仍只是人工参考，不等于本轮按具体任务试排的人选结论。
# 阶段 3、4 传播补充（2026-09-21）

| 规则或边界 | 自动排班 | 手工保存 | SQLite / 备份恢复 | 回归 |
| --- | --- | --- | --- | --- |
| 既有硬约束 | `solver.solve` | 不适用 | 只保存通过校验的分配 | 自动解经独立校验 |
| 最终合法性 | 求解结果先过 `validate_schedule` | `POST /api/manual-assignments` 先过 `validate_schedule` | 恢复前重建后再过 `validate_schedule` | 自动、手工、恢复测试 |
| Excel 日表名单与备注 | 名单只作候选；未核对的0910备注阻止求解，不推导默认占用 | 只保存已通过 `validate_imported_day` 的日表 | Excel备份恢复测试 |
| 0910 与到岗备注 | 不生成默认占用 | 不适用 | 保留原文 | Excel 回归 |

## 本轮工作台消费补充

| 输入/结构事实 | 候选与求解 | 人工/后置变更 | 最终校验 | 输出与恢复 | 反向证据 |
| --- | --- | --- | --- | --- | --- |
| 明确岗位资格：`Member.eligible_role_ids`、`has_role_qualification` | 求解器与API `qualifiedMemberIds`消费同一事实；UI按岗筛选 | UI保留已存在的不合格草稿标识，接口仍可存违规草稿供修正 | 独立校验拒绝无资格正式采用，按钮筛选不能替代校验 | 基础人员/当天资格分别保存，采用快照不被改名或停用污染 | `test_workspace.py`按岗选人及后置伪造；真实Chrome中只有登机资格的丁不出现在接机下拉 |
| `Task.covered_flight_ids`共享覆盖元数据 | 日表转换仅从有需求航班生成一次任务；求解不因投影列数重复占用 | 输入变化重新生成；人工改人作用于原任务 | 共享航班引用结构校验；原需求、人数、冲突与轮换独立复查 | 页面与正式Excel共用task_matrix；0需求航班不贡献覆盖列；人员路径只计一次 | `test_workspace.py`共享大件与0需求；`test_workspace_recovery.py`破坏覆盖引用 |
| 日表岗位格完整性与同日期输入身份 | 缺少某航班岗位格列为缺项，不默认为0 | 允许保存待补草稿，不能生成/采用 | `validate_imported_day`及工作台采用复查 | 新导入另建计划；迁移仅在重建相等时关联原结果 | 缺格回归；旧无关日表同日期迁移分别保留；旧未拆任务映射与锁定/重复分配拒绝 |
| `stateVersion/timeVersion/revision/inputRevision` | 自动结果只存预览；输入变更清除旧预览 | 所有工作台命令以期望版本事务写入 | 恢复检查预览版本/正式快照，正式再验 | 空/损坏状态不回退演示；未来版本拒绝；失败原数据不变 | 模拟磁盘失败、实际SQLite只读拒写、两写入线程同版本仅一成功、坏库下载原文件 |

本表前文中的旧手工接口消费点继续保留供旧版/演示回归；新工作台采用、恢复和导出均经Workbench复查。手工草稿可未完成，只有正式采用要求完整合法。

## 完整试用配置边界（2026-10-29）

本轮没有新增或降级业务硬规则。build-trial-workspace只生成明确标为试用的输入，资格来自历史建议，人数/普通岗位时段/出勤在该配置中显式填写；不写入domain默认，不为0910猜占用。日表转换→候选→求解→草稿投影→人工修改→独立校验采用/输出/恢复仍消费原事实owner。trialInfo仅显示来源与假设，不参与任何约束。test_trial_workspace与真实Chrome覆盖单文件独立恢复、再求解/取消、清空一人拒绝采用、补齐采用/正式导出和重启；说明字段类型损坏拒绝恢复且原数据不变。

## 完整Excel配置消费边界（2026-10-29）

本轮不新增排班硬规则。Owner确认表内1改0撤销资格，是配置更新合同：excel_io.import_complete_configuration以岗位矩阵生成唯一当天memberConfigs；Workbench在表内人员×岗位范围替换基础资格，保留稳定身份/表外资料/旧计划及正式快照，新增当天计划。不从旧原名单赋予资质或固定。预览复制状态、确认以revision同一SQLite事务更新。

候选池、自动求解、人工下拉、最终校验都继续消费domain资格；正式采用/输出/恢复仍独立复查。Excel不猜实际分钟；普通环节与日常出勤无需时间，特殊实际时段/时间限制缺项及旧必填合同仍允许留未完成资料但不能采用。测试test_complete_excel覆盖撤销后的新计划/旧正式隔离、失效版本、磁盘失败、坏格式/公式/重复/未来版本、停用/缺资料、表外资料保护、新人新岗与API往返；真实Chrome及真实EXE验证一文件导入、再排、导出改0重导与重启。

2026-10-29落实消费点：domain.imported_work_mode/phase_role_config/task_pair_conflict拥有方式、初始选择与兼容关系；imported_schedule只转换事实。solver逐对限制，validator逐对独立复查，Workbench.candidates由validator解释，打字/拖动/临时岗位/configure-cell/归档都复用_scenario和_adopt。io对旧flight保留拆段，对新task不再按整体结束拆段；normalize_saved_scenario消费同合同。projections与schedule_data提供timing_label，UI/正式Excel不私算实际分钟。excel_io提供四张可见新表和单格例外，完整-2校验字段/表、旧-1继续读；Workbench正式快照重建一致性、SQLite/JSON恢复验证保留新事实。tests/test_work_phases.py的10项含新规则正反向；全量148项及真实EXE浏览器链路验证，不从历史推许可。
# 2026-10-29 日常重排入口修复补充

| 事实 | 输入owner | 候选池/求解 | 人工/后置 | 最终校验 | 输出/恢复 | 回归 |
|---|---|---|---|---|---|---|
| 日常出勤无需时间；显式起止限制仍生效 | domain.attendance_bounds；ImportedDay.attendance_mode（旧bounded、新duty） | 转换Member边界后沿用候选/solver | 同一Member边界检查 | validator出勤限制；不绕过正式门禁 | Excel可见规则行、JSON可选字段、SQLite；旧快照缺字段保留bounded | 未填可排、半填拒绝、限定后手改越界拒绝、旧合同仍拒绝缺时 |
| 直接重排旧草稿自动准备环节 | domain.prepare_regular_day；Workbench准备适配 | 只转换隐式旧方式，保留显式特殊时段、手工岗位性质与所有资格/人数 | 新方案预览取消返回旧方案 | 原独立validator不变 | 原方案/快照保留；新资料完整保存 | 真实8月4日副本入口、取消、归档+2、旧督导关兼任仍保留 |
# 2026-10-30：缺岗草稿、历史偏好与后台停止

Owner确认缺人先出其余安全安排；仅草稿允许coverage_shortfall，正式人数约束不变。domain.draft_blocking_issues拥有安全草稿边界；输入仍显式人数/资格/出勤；候选与人工编辑仍消费独立validator；solver允许缺口并以填满人数为最高目标；Workbench独立复查只允许人数不足，采用/归档/正式Excel仍全量拒绝缺口；输出逐岗显示缺人数；tests/test_checkin_parity.py覆盖安全局部结果、后置破坏资格/冲突/人数和正式拒绝。固定人员、走动、特殊时间与轮换不放松。

历史轮岗为软偏好：仅上一份较早日期的已采用班表，按稳定人员ID和岗位匹配，不从姓名推断；不以历史授资格、不因此留空，不加入最终合法性硬约束。后台停止不采用不写；停止并保留仅接收独立校验安全结果为草稿，CAS版本冲突拒绝覆盖；API、UI均消费运行module的状态。备份恢复/正式快照仍走原完整校验。


## 2026-11-03：末班范围及轻松班次识别（统计规则）

| 传播点 | 消费/验证 |
| --- | --- |
| 输入事实 | domain统一默认/校验relaxedShiftSettings；日事实统一relaxed_shift_facts |
| 候选/求解/最终合法性 | 不适用：仅名单识别和计次，无新增派人硬约束 |
| 人工/后置 | 已保存草稿实时替换同日正式，清空/撤销重算，不双计 |
| 输出 | 排班常显两名单，统计月/姓名筛选、日期/航班/结束时间；末班去重次数 |
| 持久化 | save-rules一次CAS；SQLite/JSON/备份；完整Excel两可维护表，旧缺字段保留/默认 |
| 验收 | 严格节点/端点/跨日/值班/零任务/手选末班/去重/手改与采用；迁移空库重启实际统计、最终EXE/Chrome |


## 2026-11-03：完整备份Excel文件合同

| 传播点 | 消费/验证 |
| --- | --- |
| 输入/事实owner | Workbench.backup/restore保留完整文档，backup_excel只做类型无损转换 |
| 候选/求解/后置 | 不适用，备份格式不改变排班规则 |
| 最终校验与写入 | 原_validate_document与compare_and_save统一处理，xlsx/旧JSON均先完整解析后CAS恢复 |
| 界面与输出 | 侧栏/历史页下载xlsx；导入接受xlsx与旧json，明确整库恢复 |
| 存储/迁移 | SQLite合同不变，所有配置/草稿/正式/撤销/修正随完整xlsx；保留旧JSON输入兼容 |
| 验收 | 真xlsx/长文本/Unicode/公式样文本/数字精度与空值类型；损坏/缺行/公式/错误文件/CAS失败不写；空库重启使用、真实副本/最终EXE与浏览器下载恢复 |

---

# 2026-11-05 浏览器本地运行与文件迁移

新增运行与保存方式，不新增硬规则或长期业务设置。

| 环节 | 消费点与失败边界 |
| --- | --- |
| 事实与候选 | 继续由domain、Workbench、imported_schedule、validator拥有；浏览器仅加载相同Python源码，不在JS复制资格/冲突/统计规则 |
| 求解/月度轮值 | cp_backend按运行环境选择；browser_cp/browser-cp串行化现有整数模型，十进制字符串保留int64；不支持操作失败，不放宽约束 |
| 手改/导入/恢复 | browser_runtime复用workspace_api路由、GroupWorkspaces和原输入/历史校验，浏览器只承担传输和文件下载 |
| 最终采用与保存 | BrowserControl复用RunControl独立校验和停止门禁；候选文档通过IndexedDB严格事务提交后才确认成功；写失败返回错误，下一请求重新读原文档 |
| A/B与并发 | 原全组文档和版本保护保持；同源Web Lock跨标签串行，旧版本同组竞争拒绝，任务ID与班组绑定 |
| 导出/换设备 | 统一配置Excel继续拥有两组设置与完整历史；导出→空白浏览器预览→确认→刷新→排班/重点统计/轮值；旧格式策略保持，不增加浏览器独有设置 |
| 静态交付 | build/browser仅程序，普通子路径/无自定义响应头浏览器实测，不将人员和原SQLite放入网站；原桌面入口独立保留 |
| 回归与拒绝 | test_browser_runtime复用路由和新旧文档版本拒绝；browser-local.browser.test覆盖无后台、真实求解、停止、损坏、写入失败、竞争、两组同名、航班筛选、真实下载/空存储导入/刷新/再次求解；实际24方案副本全草稿/正式快照保持 |

---

# 2026-11-05 重点航班＋岗位范围与配置迁移

本次为软目标范围及统计合同调整，无新增硬排班规则。

| 环节 | 消费点与边界 |
| --- | --- |
| 输入事实/默认 | domain.validate_priority_flight_roles验证明确且唯一的航班/岗位组合；Workbench根设置迁移旧字段为新范围空选，旧计划不改写 |
| 页面升级/旧端保存 | server原入口按页面内容版本打开、不缓存静态与实时数据；workspace_api要求save-rules/update-balance-settings显式声明flight-role-v1，旧页面即使回传新字段也拒绝并提示刷新；Workbench保存须有新范围字段；不改变Excel/备份旧字段兼容或业务硬规则；test_frontend_upgrade及真实旧JS/新EXE隔离Chrome回归 |
| 唯一计次事实 | domain.member_flight_role_participation按人员/航班ID/岗位去重，member_role_flight_counts消费该事实聚合；分段一次、共享按实际覆盖航班 |
| 候选池 | 资格、出勤、时段、半休及冲突候选保持；手选重点不扩大资格或强制某人 |
| 求解 | _priority_costs按选中航班号过滤参与变量，priority_counts_before的航班账本与上一归档组合事实供月度/换人软目标；独立稀缺目标保持原岗位口径 |
| 人工/后置 | 规则统一保存所有航班勾选，切换航班不丢选择；修正键为月/人/航班/岗，缺航班、非选中组合、0再减均拒绝且不写 |
| 最终校验/存储 | validator验证新场景组合结构；Workbench完整文档验证新设置及历史快照，原硬规则独立复查/问题封存/身份/CAS不变 |
| 输出/统计 | priority_statistics按组合列出日期与修正，并拥有所有筛选范围合计；界面只筛列及消费对应合计，不计算领域次数；legacyAdjustments单列待归属 |
| 导出/恢复 | 配置Excel范围版本＋航班重点岗位表＋修正航班列；旧缺字段保留本机组合/空库空选，缺新附表拒绝；SQLite和完整备份保留新字段及全部原历史 |
| 验证 | test_priority_flights涵盖双航班同岗隔离、共享分段去重、相反月次数/上班换人、旧库迁移、缺航班修正原子拒绝、缺附表、导出→空库→重启→实际排班；原迁移/统计/人员回归同步；Chrome验证航班切换、范围合计、次数修正、失败/迟到回复及窄屏 |
