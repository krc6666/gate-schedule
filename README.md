# 登机口保障排班

独立运行的本地登机口排班系统，支持岗位资质配置、航班维护、自动排班、手动输入与拖动调整、冲突报警、值班备勤轮换及重点岗位统计。

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

程序数据保存在本机，通过完整备份迁移历史及草稿；完整 Excel 配置可迁移人员、资质和规则。首次启动请配置人员与航班。templates 中提供不含人员的资质空白模板；完整配置模板可在配置页导出。

## 当前功能

- 配置、航班、排班、规则、统计、历史六个入口。
- 按岗位维护资质，支持航班独立岗位、接续偏好、重点轮换与航班数平衡。
- 手工填入资格不符或有时间冲突的人员，姓名保留并报警；问题草稿不能正式归档或导出。
- 月度值班、次日两名备勤自动轮换、手动调整及 Excel 维护。
- SQLite 本地保存、完整备份恢复和 Windows 便携构建。

## 源码与数据范围

本仓库是当前源码的独立快照，不携带原本地 Git 历史。数据库、实际人员配置、历史班表、备份、截图、历史试用配置和便携成品未上传。源码中的小型解析与单元测试示例保留，不代表正式人员资质或现场班表。

部分历史集成测试需要本地保留的历史资料和维护 Excel，仓库未包含这些夹具，因此全量历史回归不能直接在空白克隆中复现。源码在原工作区已通过316项后端测试和前端/Chrome回归；这不代替新设备验收。

## 测试与构建

```powershell
.venv-local/Scripts/python.exe -B -m unittest discover -s tests -p test_manual_editor.py
npm.cmd run build --prefix web
powershell -ExecutionPolicy Bypass -File scripts/build-portable.ps1
```

工程规则见 AGENTS.md，产品规则见 spec.md。此源码快照包含最近完成的手工填人保留与冲突报警修复。
