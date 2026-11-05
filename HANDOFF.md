# 浏览器本机版（2026-11-05，网页实现已完成，上线待仓库决策）

最新授权已明确：公开此源码仓库并部署Pages，公网入口https://krc6666.github.io/gate-schedule/ 。旧“等待公开决定”状态已被覆盖，当前正在部署与HTTPS验收；本仓只含筛选后的源码和空白模板，不含原业务资料。

本轮目标为打开网页、导入完整配置即可在新设备排班，不付费、不租后台。浏览器通过Pyodide复用本仓Python领域/Workbench/validator/Excel，用CP-SAT WASM执行同一整数模型；SQLite桌面版保留。浏览器文档在同源Web Lock内读取、计算/校验、IndexedDB严格提交后才确认，A/B隔离和全组Excel迁移沿用原合同。源码变更与当前边界见spec/传播矩阵；禁止在JS另写一套业务规则。

交付目录build/browser，开发电脑可`npm.cmd run preview:browser --prefix web`在http://127.0.0.1:8787/打开。访问者无需安装Python/Node，但需新版Chrome/Edge和HTTPS/localhost；文件双击与断网冷启动未承诺。换设备、更换浏览器或清网站数据前导出配置；数据不云同步。原8765快捷方式仍是独立桌面资料，本轮没有替换原程序或改其班表。

验证：388项后端通过，最终运行接口/班组13项定向回归通过；无后台、无COOP/COEP服务端头的真实静态子路径Chrome完成实际求解、月度轮值、A/B同名隔离、航班筛选、真实下载→清空浏览器存储→文件导入→刷新→再次求解、损坏/写入失败/旧版本竞争和停止不采用。原配置24方案副本全部草稿与正式快照保持，浏览器再次求解76项安排、无校验问题；生产全组backup逐字段不变。证据.tmp/browser-{backend-full,adapter-final,owner-test}.log及.tmp/browser-local-result.json，截图.tmp/browser-local-{1440,390}.png。完整前端结果见本轮history/plan收口。

Pages工作流已准备：build只上传程序build/browser，需先有Pages资格并在Settings选择GitHub Actions。2026-11-05只读GitHub API确认krc6666/gate-schedule仍private，Pages GET返回404；尝试开通实际收到422“Your current plan does not support GitHub Pages for this repository.”，没有创建站点。不能擅自改为公开，未commit/push/上线；完整发布仍待Owner决定仓库是否公开。不得将本地网页可用或Actions文件就绪写成公网已上线。原现场/另一台设备人工验收仍待Owner。

最终交付build/GateSchedule-Browser-20261105.zip，38个纯程序文件，约16.6MB；源码ZIP逐文件与当前源码匹配，未夹带配置/数据库/历史文件，证据.tmp/browser-artifact-check.json。独立的既有远端源码克隆.tmp/browser-github-source已准备146份筛选源码/测试与工作流更新，无原本地Git历史或现场资料；重新npm ci、浏览器构建、2项接口回归和真实静态迁移浏览器29.58秒全部通过，未commit/push。完整44项前端已通过，最后模板下载与窄屏入口改动由静态浏览器/原配置副本及3项定向前端复验。旧依赖Vite/source-map-js的npm audit告警属于既有构建/开发工具，未打入静态应用运行依赖，本轮未扩大到升级构建工具；本机预览绑定127.0.0.1。

---

# 源码交接

当前快照同步本地提交31d2398（2026-11-05）。重点岗位按航班＋岗位配置和计次，统计页对齐值机重点统计布局，提供月份、航班、岗位筛选；含旧页面保存防护及原Windows入口启动脚本。当前功能合同见README.md、spec.md和rule-propagation-matrix.md。

沿用远端独立源码提交链，保留已有两次提交；不包含原本地Git历史、数据库、人员配置、历史班表、备份、截图、历史试用文件和便携成品。原工作区后端375项、前端及真实后端Chrome42项通过；源码副本的定向回归另验。部分历史测试依赖未上传的现场夹具，现场人工及另一台Windows验收仍待。
