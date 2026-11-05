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
