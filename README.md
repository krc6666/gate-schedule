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
- 统计页支持所有岗位次数及月份、岗位、姓名筛选；手动调整保存后同步计数。
- 规则页可配置末班航班，班表展示下午无航班和提前下班人员并统计次数。
- 航班数差值超限只保留整体提示，实际本格冲突仍标红。
- 月度值班、次日两名备勤自动轮换、手动调整及 Excel 维护。
- SQLite 本地保存、完整备份恢复和 Windows 便携构建。

## 源码与数据范围

本仓库是当前源码的独立快照，不携带原本地 Git 历史。数据库、实际人员配置、历史班表、备份、截图、历史试用配置和便携成品未上传。源码中的小型解析与单元测试示例保留，不代表正式人员资质或现场班表。

部分历史集成测试需要本地保留的历史资料和维护 Excel，仓库未包含这些夹具，因此全量历史回归不能直接在空白克隆中复现。当前源码在原工作区已通过364项后端测试、38项最终程序前端/Chrome测试，以及隔离迁移和便携运行检查。原始现场夹具不在本仓库中，这些全量结果不等于空白克隆全部可复现，也不代替现场或另一台Windows验收。

## 测试与构建

```powershell
.venv-local/Scripts/python.exe -B -m unittest discover -s tests -p test_warning_archive.py
npm.cmd run build --prefix web
powershell -ExecutionPolicy Bypass -File scripts/build-portable.ps1
```

工程规则见 AGENTS.md，产品规则见 spec.md。此源码快照对应本地提交 `7b87a82`，已包含截至2026-11-03的功能。远端沿用独立源码历史，不带原本地 Git 历史。
