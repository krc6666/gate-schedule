from __future__ import annotations

from pathlib import Path
import sqlite3
from zipfile import BadZipFile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .workbench import Workbench, WorkspaceError
from .excel_io import export_qualifications, import_qualifications, import_complete_configuration
from .backup_excel import export_backup_excel, import_backup
from io import BytesIO
from openpyxl import load_workbook


def attach_workspace(app, database_path: Path, *, groups=None, execute_task=run_in_threadpool):
    from .group_workspaces import GroupWorkspaces
    try:
        groups = groups if groups is not None else GroupWorkspaces(database_path)
        groups.initialize()
        recovery_error = None
    except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
        groups = None
        recovery_error = str(error)

    @app.get('/api/groups')
    def group_info():
        if groups is None:
            raise HTTPException(503, recovery_error)
        return groups.info()

    @app.post('/api/groups/select')
    async def select_group(request: Request):
        if groups is None:
            raise HTTPException(503, recovery_error)
        try:
            return groups.select((await request.json()).get('groupId'))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    from .schedule_runs import ScheduleRuns
    runs_by_group = {g: ScheduleRuns(groups.workbench(g)) for g in ('A', 'B')} if groups else {}
    for group_id in ('A', 'B'):
        router = APIRouter(prefix=f'/api/groups/{group_id}')
        _attach_scoped_workspace(router, database_path, groups, group_id, recovery_error, runs=runs_by_group.get(group_id), execute_task=execute_task)
        app.include_router(router)
    # Existing clients always refer to A, regardless of another tab's selection.
    router = APIRouter(prefix='/api')
    _attach_scoped_workspace(router, database_path, groups, 'A', recovery_error, legacy=True, runs=runs_by_group.get('A'), execute_task=execute_task)
    app.include_router(router)


def _attach_scoped_workspace(app, database_path, groups, group_id, recovery_error, legacy=False, runs=None, execute_task=run_in_threadpool):
    workspace = groups.workbench(group_id) if groups else None

    @app.post("/workspace/runs")
    async def start_run(request: Request):
        if runs is None:
            raise HTTPException(503, "请先恢复数据")
        try:
            data = await request.json()
            return runs.start(data["expectedRevision"], data["action"], data.get("payload", {}), data.get("planId"))
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/workspace/runs/{run_id}")
    def run_status(run_id: str):
        if runs is None:
            raise HTTPException(503, "请先恢复数据")
        try:
            return runs.get(run_id)
        except WorkspaceError as error:
            raise HTTPException(404, str(error)) from error

    @app.post("/workspace/runs/{run_id}/stop")
    async def stop_run(run_id: str, request: Request):
        if runs is None:
            raise HTTPException(503, "请先恢复数据")
        try:
            return runs.stop(run_id, (await request.json()).get("keep", False) is True)
        except WorkspaceError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/workspace")
    def get_workspace():
        if recovery_error:
            return JSONResponse(status_code=503, content={"recoveryRequired": True, "message": f"数据需要恢复，原文件保持不变：{recovery_error}"})
        return workspace.snapshot()

    @app.get("/priority-statistics")
    def get_priority_statistics(month: str | None = None):
        if workspace is None:
            raise HTTPException(503, "请先恢复数据")
        try:
            return workspace.priority_statistics(month)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get('/relaxed-shift-statistics')
    def get_relaxed_shift_statistics(month: str | None = None):
        if workspace is None:
            raise HTTPException(503, '请先恢复数据')
        try:
            return workspace.relaxed_shift_statistics(month)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get('/role-statistics')
    def get_role_statistics(month: str | None = None):
        if workspace is None:
            raise HTTPException(503, '请先恢复数据')
        try:
            return workspace.role_statistics(month)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get('/duty-statistics')
    def get_duty_statistics(month: str | None = None):
        if workspace is None: raise HTTPException(503, '请先恢复数据')
        try: return workspace.duty_statistics(month)
        except (ValueError, KeyError, TypeError) as error: raise HTTPException(422, str(error)) from error

    @app.get('/workspace/duty-roster/export')
    def export_duty_roster(month: str):
        if workspace is None: raise HTTPException(503, '请先恢复数据')
        try: content = workspace.export_duty_roster(month)
        except (ValueError, KeyError, TypeError) as error: raise HTTPException(422, str(error)) from error
        return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition':'attachment; filename="gate-duty-roster.xlsx"'})

    @app.post('/workspace/duty-roster/preview')
    async def preview_duty_roster(request: Request):
        if workspace is None: raise HTTPException(503, '请先恢复数据')
        try: return workspace.preview_duty_roster(await request.body())
        except (ValueError, KeyError, TypeError) as error: raise HTTPException(422, str(error)) from error

    @app.post("/workspace/command")
    async def command(request: Request):
        if workspace is None:
            raise HTTPException(503, "请先保留原始文件并在独立目录恢复备份")
        try:
            data = await request.json()
            if data["action"] in {"save-rules", "update-balance-settings"} and request.headers.get("X-Gate-Rule-Schema") != "flight-role-v1":
                return JSONResponse(status_code=409, content={"detail": "页面版本已更新，请按 Ctrl+F5 刷新，选择航班后重新勾选重点岗位；本次未保存，原配置和班表保留"})
            if data['action'] == 'import-complete-configuration' and data.get('payload', {}).get('groupTransfer') is not None:
                return await execute_task(groups.import_configuration, group_id, data['expectedRevision'], data['payload'])
            return await execute_task(workspace.execute, data["expectedRevision"], data["action"], data.get("payload", {}), data.get("planId"))
        except WorkspaceError as error:
            return JSONResponse(status_code=409, content={"detail": str(error)})
        except (ValueError, KeyError, TypeError) as error:
            return JSONResponse(status_code=422, content={"detail": f"输入无效：{error}"})
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"detail": "保存失败，原正式结果未改变；请检查磁盘空间与数据目录"})

    @app.post("/workspace/import")
    async def import_excel(request: Request, serviceDate: str, expectedRevision: int):
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式，请保留原文件并在独立目录恢复")
        try:
            return workspace.execute(expectedRevision, "import-plan", {"serviceDate": serviceDate, "content": await request.body()})
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(503, "导入写入失败，原数据未改变") from error

    @app.get("/workspace/qualifications/template")
    def qualification_template():
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式")
        content = export_qualifications(workspace.qualification_configuration())
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="gate-qualifications.xlsx"'})

    @app.post("/workspace/qualifications/preview")
    async def qualification_preview(request: Request):
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式")
        try:
            return workspace.preview_qualifications(import_qualifications(await request.body()))
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, f"资质表未导入：{error}") from error

    @app.get("/workspace/backup")
    def backup():
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式，请下载原始数据库")
        return workspace.backup() if legacy else groups.backup()

    @app.get('/workspace/backup.xlsx')
    def backup_excel():
        if workspace is None:
            raise HTTPException(503, '当前处于恢复模式，请下载原始数据库')
        try:
            content = export_backup_excel(workspace.backup() if legacy else groups.backup())
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, f'备份未导出：{error}') from error
        return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': 'attachment; filename="gate-workspace-backup.xlsx"'})

    @app.post("/workspace/configuration/preview")
    async def complete_configuration_preview(request: Request):
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式")
        try:
            return groups.preview_configuration(group_id, import_complete_configuration(await request.body()))
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, f"完整配置未导入：{error}") from error

    @app.post('/workspace/data/preview')
    async def data_preview(request: Request):
        if workspace is None:
            raise HTTPException(503, '当前处于恢复模式')
        content = await request.body()
        try:
            is_configuration = False
            if content.startswith(b'PK'):
                book = load_workbook(BytesIO(content), read_only=True, data_only=False)
                try:
                    is_configuration = {'使用说明', '岗位资质'} <= set(book.sheetnames)
                finally:
                    book.close()
            if is_configuration:
                return {'kind': 'configuration', **groups.preview_configuration(group_id, import_complete_configuration(content))}
            return {'kind': 'backup', **groups.preview_restore(group_id, import_backup(content))}
        except (ValueError, KeyError, TypeError, OSError, BadZipFile) as error:
            raise HTTPException(422, f'文件未导入，当前数据保持：{error}') from error

    @app.get('/workspace/configuration/export')
    def export_configuration(serviceDate: str | None = None):
        if workspace is None:
            raise HTTPException(503, '当前处于恢复模式')
        try:
            content = groups.export_configuration(group_id, service_date=serviceDate) if not legacy else workspace.export_complete_configuration(service_date=serviceDate)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
        return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': 'attachment; filename="gate-configuration.xlsx"'})

    @app.get("/workspace/configuration/export/{plan_id}")
    def complete_configuration_export(plan_id: str):
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式")
        try:
            content = groups.export_configuration(group_id, plan_id) if not legacy else workspace.export_complete_configuration(plan_id)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="gate-complete-configuration.xlsx"'})

    @app.post("/workspace/restore")
    async def restore(request: Request, expectedRevision: int, expectedGroupsRevision: int | None = None):
        if workspace is None:
            raise HTTPException(503, "请在独立数据目录恢复此备份，保留损坏原文件")
        try:
            content = import_backup(await request.body())
            if isinstance(content, dict) and content.get('formatVersion') == 3 and expectedGroupsRevision is None:
                raise ValueError('请先预览两组完整备份，再确认恢复')
            return groups.restore(group_id, expectedRevision, content, expectedGroupsRevision)
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, f"备份未采用，当前数据不变：{error}") from error
        except (OSError, sqlite3.Error) as error:
            raise HTTPException(503, "恢复写入失败，当前数据不变") from error

    @app.get("/workspace/export/{plan_id}")
    def export(plan_id: str):
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式")
        try:
            content = workspace.export(plan_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="gate-formal.xlsx"'})

    @app.get("/workspace/candidates/{plan_id}/{task_id}")
    def candidates(plan_id: str, task_id: str, slot: int = 0):
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式")
        try:
            return workspace.candidates(plan_id, task_id, slot)
        except (KeyError, StopIteration) as error:
            raise HTTPException(404, "任务不存在") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/workspace/cell-candidates/{plan_id}/{cell_id}")
    def cell_candidates(plan_id: str, cell_id: str, slot: int = 0):
        if workspace is None:
            raise HTTPException(503, "当前处于恢复模式")
        try:
            return workspace.cell_candidates(plan_id, cell_id, slot)
        except (KeyError, StopIteration) as error:
            raise HTTPException(404, "岗位格不存在") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/workspace/preview-edit/{plan_id}")
    async def preview_edit(plan_id: str, request: Request):
        if workspace is None:
            raise HTTPException(503, "请先恢复数据")
        try:
            return workspace.preview_edit(plan_id, await request.json())
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/workspace/raw-database")
    def raw_database():
        if not database_path.exists():
            raise HTTPException(404, "无数据文件")
        # SQLite backup guarantees a consistent file when a valid database is open.
        if workspace:
            import tempfile
            temporary_root = database_path.parent / ".tmp"
            temporary_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
                copy = Path(directory) / "copy.sqlite3"
                with sqlite3.connect(database_path) as source, sqlite3.connect(copy) as destination:
                    source.backup(destination)
                content = copy.read_bytes()
        else:
            content = database_path.read_bytes()
        return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="gate-original.sqlite3"'})
