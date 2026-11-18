# 同档保岗软顺序与流动岗位排除专岗（2026-11-18）

同步已验证源提交5cbc61f和48a6dea：保留保岗/正常/优先留空三档，同档可上下调整软顺序；每组全局、A/B独立，求解实际消费，独立校验给黄色软提示，完整Excel/备份迁移，旧配置与正式保持。专岗不能进入流动填写的来源、目标及相关岗位关系新增选项；大件别名跟随真实岗位，取消专岗并保存恢复。旧引用保留并提示，可明确替换/删除，不自动改旧规则或班表。没有新增严格顺序或排班硬规则。

源工作区后端548项通过；全量前端93项中91通过，新增测试及父测试因Excel补空范围对象的测试预期失败，修正断言后完整相关Chrome6项通过；静态WASM、最终EXE Chrome6项、迁移/重启/归档/导出和原入口数据保持均通过。保留原失败证据，不声称单轮全绿。现场、实际手机及干净Windows仍未验证。

Owner明确授权commit与push，沿既有独立源码历史；不上传业务资料、备份、成品、密钥或根仓旧历史。push只触发检查，本次不执行手动网站部署。

---

# 手调响应优化与未推送功能同步（2026-11-17）

同步本机已提交源码1402fea及此前0c1b959、0993efc的产品变更，沿既有筛选Git历史，不带运行资料或原本地历史。新增大件尾段宽限、通用设置与手动特例的当前合同见spec.md；本次手调优化只减少同日默认出勤起点的重复计算，不改变排班、校验、撤销、统计、正式历史或完整返回。

同一隔离资料、实际EXE的HTTP请求各三次：清除人员中位4.33→2.84秒，交换4.37→2.95秒，撤销4.29→2.55秒；六类操作完整响应与保存文档指纹等价。此为本机请求耗时，不含页面渲染，不保证所有设备相同秒数。当前本机程序已备份更新，原资料保持。

源工作区完整后端533项通过。前端完整复跑90/91通过，唯一静态网页超时项在原时限单独184秒通过；覆盖全部91项，不声称单轮全绿。类型检查、两种网页构建、WASM、隔离EXE及备份恢复通过。公开源码副本另做定向回归与构建，结果见plan.md。未操作真实云端；push只运行检查，未触发网站部署。

# 单管理员 Supabase 云端工作区（2026-11-15）

已部署：https://krc6666.github.io/gate-schedule/ 。代码49f2c63经Actions43698684246完整构建、测试及部署成功。真实HTTPS云登录页、1440/390布局、隔离环境、匿名RPC401/42501拒绝及发布Python源码匹配通过；管理员公网账号内恢复仍待本人确认。本机预览接真实Supabase的管理员保存/重登已验证，两者分别报告。

沿用现有 Python/Pyodide/WASM 排班逻辑与 GitHub Pages，Supabase 负责登录及完整 A/B 工作区持久化。顶部显式保存、数据库单管理员权限、原子版本检查及退出保护已实现。网页地址：https://krc6666.github.io/gate-schedule/ 。本次云端部署状态以 GitHub Actions 与 plan.md 为准。

源工作区后端524项、前端88项、类型检查及本机/云端构建通过；真实Supabase管理员保存与重登恢复、匿名拒绝已验证。模拟PostgreSQL/Chrome覆盖版本冲突、失败重试、权限与离开保护。实际普通账号、双设备故障注入、手机计算及现场业务未验证，不以模拟替代。

源码使用既有独立发布历史，不上传原本地 Git 历史、业务数据库、人员配置、历史班表、备份、截图和成品。环境文件被Git忽略，示例仅含占位符；公开项目配置由Actions变量注入。

云端模式登录后由数据库验证管理员，读取A/B全部资料；只有点击顶部“保存到云端”并收到服务器成功回执才确认上传。普通“保存配置”、手工调整、归档和导出仅修改当前工作区或生成文件，不确认云端保存。回执显示本机收到时刻，5秒后简化为“已保存”，悬停看完整时间；重新登录只显示“已读取”。再次修改显示“有修改未上传”。网络/服务、权限、版本冲突和数据校验失败均弹窗、保留修改并可重试。

更多操作默认折叠，内含重新读取云端与退出登录。有未上传修改或正在保存才提醒离开；退出可取消，关闭/刷新使用浏览器原生提醒，不在卸载时上传。保存后再修改仍提醒；Excel是否导出不影响云端离开保护。账号会话保存在本次页面内，刷新后重新登录，不伪造上次保存时间。推荐更新的Chrome/Edge；保留原有WASM/SharedArrayBuffer/JSPI要求，手机宽度布局通过不等于已验证每款手机浏览器的计算支持。

## 数据范围与读写路径

云端SQL保存唯一对象：`documents["group-workspaces-v1"]`，即 `GroupWorkspaces.backup()` 的 formatVersion=3 原始根文档，而非界面投影。根含 `revision / activeGroupId / shared / groups.A / groups.B`。云端表自己的 `revision` 与业务文档内部revision独立，前者只由服务器CAS增加。

| 实际保存字段 | 用途与现有入口 |
| --- | --- |
| `shared.flightTemplates / weeklyFlightPresets` | 航班号、保障窗口、每周航班，共享至两组；航班页及既有模板/星期命令 |
| `groups.*.people` | id、name、eligibleRoleNames、active、teamLeader、status、remark、deleted等原字段；配置页人员/资质命令 |
| `configuration.roleConfigs / rotationEnabled / rotationMinutes / transitionMinutes` | 岗位、需求、保障性质、专岗、旧相对设置、轮换及走动；配置/规则命令 |
| `flightLibrary / weeklyFlightPresets / handoffPreferences / workRules` | 各组岗位需求、航班配置、接续与范围、搭配、分人、禁止、大件窗口和轮次；原配置命令 |
| `balanceSettings` | 航班数差距、重点航班岗位、保岗顺序、priorityAdjustments人工次数修正；规则/统计命令 |
| `teamLeaderSettings / halfRestSettings / relaxedShiftSettings` | 分队长、半休、末班及月度均衡开关和目标；规则命令 |
| `dutyRoster` | enabled、anchorDate、资质人员池、overrides；统计页轮值命令 |
| `displayPreferences` | 持久布局、缩放偏好，继续跟随完整配置和工作区迁移 |
| `plans` | 每个id的day（航班、岗位、人员/资格/出勤、半休、占用、固定、当天规则/接岗等）、inputRevision、memberIds、人员状态与规则快照、draft、cellDrafts、preview、undoEdit、lastEdit、message及原字段 |
| `plans.*.adopted` | 正式scenario、assignments、输入/人员/规则快照、归档版本和问题记录；正式历史及月统计事实源，不以当前配置重算替换 |
| `selectedPlanId / migrationReport / stateVersion / timeVersion / revision` | 当前计划与原迁移证据、版本，保持既有业务合同 |

读取：Auth → `gate_read_workspace`（数据库管理员检查）→ 严格完整性检查 → 原 `GroupWorkspaces / Workbench.snapshot` → 六页及统计投影。写入：原表单/自动排班/人工命令 → 原独立校验 → 会话内原始文档 → 顶部保存统一提交待保存编辑 → 验证完整快照 → `gate_save_workspace(expected_revision,new_documents)` → 服务器锁及CAS → 回执确认状态。Python领域校验和正式快照检查不在SQL重写；数据库负责权限、结构、大小与版本，不冒充排班合法性证明。

未提交新增人员/当天准备弹窗、未确认导入预览、尚未保存规则/历史编辑、查询筛选和展开状态不直接进云端。顶部保存先调用原 WorkbenchEdits.save，任何校验失败停止上传；尚未确认的弹窗要求先完成或取消。已保存的业务草稿和带问题归档按原标红合同保存，不把草稿自动升级为正式。清空历史/导入/恢复仍走原确认及校验，之后还需显式云保存。

## 首次迁移、恢复与冲突

- 云端确认为空（revision=0、documents=null）才提供“迁入此浏览器原有资料”或“新建空白工作区”，均明确确认；原IndexedDB不覆盖、不清理。旧浏览器数据仅在同一网址来源下可见，换预览地址不会自动读到原Pages缓存。
- 桌面资料：从原程序导出完整配置Excel或完整备份，在云端空白工作区通过既有“导入数据”预览并确认，核对A/B、历史和统计后点击“保存到云端”。不要只导出资质表。原SQLite、Excel、缓存继续保留。
- 云端已有工作区时只读取它，不合并本地缓存；需要恢复/替换资料时先用原导出保留当前资料，再走原导入/恢复预览确认，最后显式云保存。无自动重置或自动首次上传。
- 读取错误或缺失/损坏数据会停止，显示错误及重试/退出入口，不以初始数据替代，不开放保存。旧可选业务字段保持已有版本合同；缺必需字段或需要补齐的数据拒绝自动修复，需显式迁移。
- 两电脑/两页面使用同一服务器版本。保存RPC锁唯一管理员行，首次插入同样受锁保护，旧版本拒绝。冲突后保留当前编辑，可先导出，再通过更多→重新读取云端重新登录；自行核对，系统不合并也不提供强行覆盖。
- 网络超时可能发生在服务器已提交之后：前端仍显示失败，不猜测成功；若重试报冲突，保留当前资料并重新读取核对。没有自动覆盖补偿。

## Supabase与本机配置

1. 创建登机口专用Free项目，保持与值机资料隔离。运行完整 `supabase/workspace.sql`（实施时同样直接在聊天提供可复制版本）。SQL可重复执行，不删除既有工作区、不自动登记管理员。
2. 在Authentication→Users手动创建唯一管理员，自行设置密码并确认邮箱。在SQL Editor执行：

   ```sql
   insert into public.gate_admin(singleton,user_id)
   values(true,'替换为管理员Auth User UUID');
   ```

3. 在Authentication设置关闭“Allow new users to sign up”，保留管理员邮箱密码登录。系统不提供注册页、账号管理、匿名班表、分享或发布功能。其他账号即使登录也无法使用工作区。
4. 复制 `web/.env.example` 为被Git忽略的 `web/.env.local`，只填写 `VITE_SUPABASE_URL` 和 `VITE_SUPABASE_PUBLISHABLE_KEY`。支持publishable或旧anon，构建前拒绝Secret/service_role。不得添加私密密钥、数据库密码或用户密码。浏览器持有公开配置正常，数据权限由数据库承担。

```powershell
$env:TEMP = Join-Path (Get-Location) '.tmp'
$env:TMP = $env:TEMP
npm.cmd ci --prefix web
npm.cmd run build:cloud --prefix web
npm.cmd run preview:cloud --prefix web
```

云端成品在 `build/cloud/`。云端构建缺配置直接失败，浏览器也禁止静默降级；原 `build:browser` 固定为独立本机版本，不受 `.env.local` 的云配置影响。桌面入口不读取Supabase配置，继续独立保存SQLite。

## 托管与授权

沿用现成免费GitHub Pages `https://krc6666.github.io/gate-schedule/`，无需新域名或付费后端计算。源文件仅程序，运行人员、配置、历史和备份不进静态包。Pages需通过既有coi-serviceworker建立隔离，Supabase跨域请求使用CORS；应用仍要求HTTPS。Free项目可能休眠、受额度或平台政策变化影响，不承诺永久免费或始终在线。

授权发布前，在GitHub仓库Actions Variables设置两项同名公开配置；工作流先测本机浏览器，再构建云端成品。缺配置会阻止发布。push仅构建验证，deploy仅允许明确的workflow_dispatch；commit、push、公网部署分别需Owner明确授权，实际执行状态见plan.md。云端数据持久化不等于已有备份策略；继续保留原有完整导出/备份恢复工具，不新增公开链接或发布班表。

验证：`python -B -m unittest discover -s tests -v`、`npm.cmd test --prefix web`、`npm.cmd run build:cloud --prefix web`。新增SQL测试使用PGlite真实PostgreSQL语义模拟Auth角色；新增Chrome测试使用模拟Supabase HTTP端点、真实SDK/Python/WASM工作台，测试成品隔离在build/cloud-mock。上述均须与真实Supabase登录/保存/恢复、真实公网验收分别报告，模拟通过不代表真实服务已验证。
