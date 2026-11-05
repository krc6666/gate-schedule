# 浏览器本机版（2026-11-05）

网页版地址：https://krc6666.github.io/gate-schedule/ 。源码仓库公开，排班资料保存在访问者当前浏览器；首次使用或换设备，点击“导入数据”选择之前导出的完整配置Excel。日常结束或换设备前点击“导出配置”，不会自动跨设备同步。部署状态可在本仓Actions的Browser app on Pages查看。

浏览器版使用原有六个页面、自动排班和独立校验，保留A/B隔离、重点航班岗位统计、月度轮值及Excel导入导出。网页自身即可计算；访问者不用安装Python或Node，也不用开通数据库或付费后台。使用更新后的Chrome或Edge，通过HTTPS网站或本机网址打开，不能双击index.html使用。

第一次在新设备打开后，点击“导入数据”，选择旧设备导出的完整配置Excel，核对预览后确认。平时点击保存后资料留在当前浏览器；关闭再打开仍保留。换设备前点击“导出配置”，两组设置、草稿、正式历史和次数修正随文件迁移。两台设备不会自动同步；更换浏览器、网站地址或清理网站数据前，也请先导出最新文件。网页首次打开需要加载运行文件，未提供断网启动保证。

原快捷方式仍打开既有桌面版，其SQLite资料与浏览器版独立。把桌面版资料带入网页，请先从原程序“导出配置”，再到网页“导入数据”。网站构建包只含程序，不含本机人员、配置、班表、数据库或备份。

开发/构建电脑执行：

```powershell
npm.cmd ci --prefix web
npm.cmd run build:browser --prefix web
npm.cmd run preview:browser --prefix web
```

在这台电脑访问 http://127.0.0.1:8787/ 。构建输出为build/browser，整个目录可交给普通静态HTTPS托管。运行文件均随包提供，构建时按scripts/browser-wheels.json校验依赖。`.github/workflows/pages.yml`会构建、跑空白浏览器迁移回归、上传网页成品并部署Pages；仓库Settings → Pages需先选择GitHub Actions，并且账号/仓库须具备Pages资格。当前既有GitHub仓库为私有，免费账户不能直接给私有仓库开通Pages；本轮未修改仓库可见性、未推送或上线。

验证命令：`.venv-local/Scripts/python.exe -m unittest tests.test_browser_runtime -v`，以及构建后`npm.cmd run test:browser --prefix web`。测试浏览器默认使用本机Chrome；可用GATE_TEST_BROWSER和GATE_TEST_PYTHON指定程序。真实配置副本迁移可设置GATE_TEST_CONFIGURATION指向本仓.tmp内的Excel，测试不会把该文件放进网页成品。

---

# 登机口保障排班

独立运行的本地登机口排班系统，支持岗位资质配置、航班维护、自动排班、手动输入与拖动调整、冲突报警、稀缺岗位轮换、半休、值班备勤及所有岗位次数统计。

## 本地运行

需要 Python 3.12+ 和 Node.js。Windows PowerShell：

```powershell
python -m venv .venv-local
.venv-local/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv-local/Scripts/python.exe -m pip install -e . --no-deps
npm.cmd ci --prefix web
npm.cmd run build --prefix web
.venv-local/Scripts/python.exe -B -m gate_schedule.server
```

程序数据保存在本机。侧栏“导出配置”生成可维护的 Excel，包含人员、资质、规则以及所有日期的航班、草稿和归档班表；换设备通过“导入数据”预览后确认导入。前部维护表可小范围修改，历史附表和隐藏快照请完整保留。旧 Excel/JSON 备份继续兼容。首次启动请配置人员与航班，templates 提供不含人员的资质空白模板。

## 当前功能

- 配置、航班、排班、规则、统计、历史六个入口。
- 按岗位维护资质，支持航班独立岗位、接续偏好、重点轮换与航班数平衡。
- 手工填入资格不符或有时间冲突的人员，姓名保留并报警；允许带问题保存、归档、继续排后天和导出 Excel/HTML/图片，原安排、空缺与问题随历史保存。
- 下一班尚未选齐人员、航班或暂时排不开时保留待处理草稿，仍可继续下一班。
- 按有资质人数自动判断稀缺岗位轮换优先级，支持上午/下午半休。
- 重点岗位按“航班＋岗位”设置，规则页先下拉选择航班，再勾选岗位；统计页参照值机布局，支持月份、航班、岗位筛选与独立次数修正。旧版重点初始空选，由使用者重新选择；旧无航班修正保留待归属。
- 统计页支持所有岗位次数及月份、岗位、姓名筛选；手动调整保存后同步计数。
- 规则页可配置末班航班，班表展示下午无航班和提前下班人员并统计次数。
- 航班数差值超限只保留整体提示，实际本格冲突仍标红。
- 月度值班、次日两名备勤自动轮换、手动调整及 Excel 维护。
- SQLite 本地保存、完整备份恢复和 Windows 便携构建。

## 源码与数据范围

本仓库是当前源码的独立快照，不携带原本地 Git 历史。数据库、实际人员配置、历史班表、备份、截图、历史试用配置和便携成品未上传。源码中的小型解析与单元测试示例保留，不代表正式人员资质或现场班表。

部分历史集成测试需要本地保留的历史资料和维护 Excel，仓库未包含这些夹具，因此全量历史回归不能直接在空白克隆中复现。当前源码在原工作区已通过375项后端测试、42项最终程序前端/Chrome测试，以及隔离迁移和便携运行检查。原始现场夹具不在本仓库中，这些全量结果不等于空白克隆全部可复现，也不代替现场或另一台Windows验收。

## 测试与构建

```powershell
.venv-local/Scripts/python.exe -B -m unittest discover -s tests -p test_warning_archive.py
npm.cmd run build --prefix web
powershell -ExecutionPolicy Bypass -File scripts/build-portable.ps1
```

工程规则见 AGENTS.md，产品规则见 spec.md。此源码快照对应本地提交 `31d2398`，已包含截至2026-11-05的功能。远端沿用独立源码历史，不带原本地 Git 历史。

旧页面保存规则时会提示刷新，防止丢失航班重点范围；页面和接口不缓存。原Windows入口可通过scripts/open-workbench.ps1按页面版本打开原便携程序，该脚本要求本机已有对应portable目录；源码克隆仍按上述命令运行。
