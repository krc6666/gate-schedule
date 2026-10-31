from __future__ import annotations

import json
import os
from copy import deepcopy
from uuid import uuid4
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

from .domain import Assignment, BaggageRotationSettings, ImportedDay, Scenario, ValidationIssue, imported_required_count, format_minutes
from .excel_io import (
    export_imported_day,
    export_imported_schedule,
    import_day,
    imported_day_from_dict,
    imported_day_to_dict,
    update_imported_day,
    validate_imported_day,
)
from .imported_schedule import imported_day_to_scenario
from .io import scenario_from_dict, scenario_to_dict
from .projections import baggage_rotation_segments, flight_handoff_suggestions, member_paths, task_matrix
from .solver import solve
from .storage import LocalStateStore
from .validator import validate_scenario, validate_schedule


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO_PATH = ROOT / "examples" / "phase-0-scenario.json"
DEFAULT_ASSIGNMENTS_PATH = ROOT / "examples" / "phase-1-assignments.json"


class AssignmentInput(BaseModel):
    taskId: str
    memberId: str


class ManualAssignmentsInput(BaseModel):
    assignments: list[AssignmentInput]


class ImportedCellInput(BaseModel):
    id: str
    rawValue: str
    semantics: str | None = None
    workStart: str | None = None
    workEnd: str | None = None
    workModeOverride: str | None = None
    requiredCountOverride: StrictInt | None = Field(default=None, ge=0)
    fixedMemberNames: list[StrictStr] | None = None


class ImportedRoleConfigInput(BaseModel):
    roleName: str
    requiredCount: StrictInt | None = Field(default=None, ge=0)
    workMode: str = "timed"
    concurrent: StrictBool = False
    exclusiveSameFlight: StrictBool = False


class ImportedMemberConfigInput(BaseModel):
    memberName: StrictStr
    eligibleRoleNames: list[StrictStr]


class ImportedMemberAvailabilityInput(BaseModel):
    memberName: StrictStr
    participating: StrictBool
    availableFrom: str | None = None
    availableUntil: str | None = None


class ImportedOccupancyInput(BaseModel):
    id: StrictStr
    label: StrictStr
    memberNames: list[StrictStr]
    start: StrictStr
    end: StrictStr


class ImportedDayInput(BaseModel):
    cells: list[ImportedCellInput]
    roleConfigs: list[ImportedRoleConfigInput] | None = None
    memberConfigs: list[ImportedMemberConfigInput] | None = None
    memberAvailability: list[ImportedMemberAvailabilityInput] | None = None
    extraOccupancies: list[ImportedOccupancyInput] | None = None
    remarksReviewed: StrictBool | None = None
    transitionMinutes: StrictInt | None = Field(default=None, ge=0, le=180)


class ImportedScheduleInput(BaseModel):
    day: ImportedDayInput
    assignments: list[AssignmentInput]


class BaggageRotationSettingsInput(BaseModel):
    enabled: bool
    interval_minutes: int = Field(alias="intervalMinutes", ge=1, le=24 * 60)


def _format_time(value: int) -> str:
    return format_minutes(value)


def _read_data(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assignments_from_data(data: dict) -> tuple[Assignment, ...]:
    return tuple(Assignment(item["taskId"], item["memberId"]) for item in data["assignments"])


def _assignments_data(assignments: tuple[Assignment, ...]) -> list[dict[str, str]]:
    return [{"taskId": assignment.task_id, "memberId": assignment.member_id} for assignment in assignments]


def _issue_data(issue: ValidationIssue) -> dict[str, str | None]:
    return {
        "code": issue.code,
        "message": issue.message,
        "taskId": issue.task_id,
        "memberId": issue.member_id,
    }


def _schedule_data(scenario: Scenario, assignments: tuple[Assignment, ...], assignment_source: str) -> dict:
    return {
        "serviceDate": scenario.service_date,
        "flights": [
            {
                "id": flight.id,
                "flightNo": flight.flight_no,
                "windowStart": _format_time(flight.window_start),
                "windowEnd": _format_time(flight.window_end),
            }
            for flight in scenario.flights
        ],
        "members": [
            {
                "id": member.id,
                "name": member.name,
                "eligibleRoleIds": sorted(member.eligible_role_ids),
            }
            for member in scenario.members
        ],
        "tasks": [
            {
                "id": task.id,
                "flightId": task.flight_id,
                "roleId": task.role_id,
                "start": _format_time(task.start),
                "end": _format_time(task.end),
                "requiredCount": task.required_count,
                "taskKind": task.task_kind,
                "lockedMemberIds": list(task.locked_member_ids),
                "serviceStage": task.service_stage,
            }
            for task in scenario.tasks
        ],
        "externalSupportRequirements": [
            {
                "taskId": requirement.task_id,
                "count": requirement.count,
                "sourceLabel": requirement.source_label,
            }
            for requirement in scenario.external_support_requirements
        ],
        "assignments": [
            {"taskId": assignment.task_id, "memberId": assignment.member_id}
            for assignment in assignments
        ],
        "assignmentSource": assignment_source,
        "taskMatrix": task_matrix(scenario, assignments),
        "memberPaths": {
            member_id: [
                {
                    "memberName": item["member_name"],
                    "taskId": item["task_id"],
                    "roleId": item["role_id"],
                    "start": _format_time(item["start"]),
                    "end": _format_time(item["end"]),
                }
                for item in path
            ]
            for member_id, path in member_paths(scenario, assignments).items()
        },
    }


def _imported_day_data(day: ImportedDay) -> dict:
    return imported_day_to_dict(day)


def _imported_day_validation_data(day: ImportedDay) -> dict:
    issues = validate_imported_day(day)
    return {
        "valid": not issues,
        "issues": [_issue_data(issue) for issue in issues],
        "flightHandoffSuggestions": [
            {
                "sourceFlightNo": suggestion.source_flight_no,
                "targetFlightNo": suggestion.target_flight_no,
                "suggestedFrom": suggestion.suggested_from,
                "suggestedUntil": suggestion.suggested_until,
                "candidateNames": list(suggestion.candidate_names),
                "boardingRoles": list(suggestion.boarding_roles),
            }
            for suggestion in flight_handoff_suggestions(day)
        ],
        **_imported_day_data(day),
    }


def _baggage_rotation_settings_from_data(data: object) -> BaggageRotationSettings:
    if data is None:
        return BaggageRotationSettings()
    if not isinstance(data, dict):
        raise ValueError("大件岗轮换设置无效")
    return BaggageRotationSettings(
        enabled=data.get("enabled", True),
        interval_minutes=data.get("intervalMinutes", 60),
    )


def _baggage_rotation_settings_data(settings: BaggageRotationSettings) -> dict[str, bool | int]:
    return {"enabled": settings.enabled, "intervalMinutes": settings.interval_minutes}


def _runtime_from_state(
    state: dict,
) -> tuple[dict, Scenario, tuple[Assignment, ...], str, ImportedDay | None, BaggageRotationSettings]:
    raw_scenario = state.get("scenario")
    if not isinstance(raw_scenario, dict):
        raise ValueError("备份缺少领域场景")
    scenario = scenario_from_dict(raw_scenario)
    scenario_issues = validate_scenario(scenario)
    if scenario_issues:
        raise ValueError("领域场景无效：" + "；".join(issue.message for issue in scenario_issues))
    assignments = _assignments_from_data({"assignments": state.get("assignments")})
    schedule_issues = validate_schedule(scenario, assignments)
    if schedule_issues:
        raise ValueError("任务分配无效：" + "；".join(issue.message for issue in schedule_issues))
    assignment_source = state.get("assignmentSource", "fixture")
    if assignment_source not in {"fixture", "manual", "automatic"}:
        raise ValueError("任务分配来源无效")
    imported_day_data = state.get("importedDay")
    imported_day = None
    if imported_day_data is not None:
        if not isinstance(imported_day_data, dict):
            raise ValueError("导入日表无效")
        imported_day = imported_day_from_dict(imported_day_data)
        imported_issues = validate_imported_day(imported_day)
        if imported_issues:
            raise ValueError("导入日表无效：" + "；".join(issue.message for issue in imported_issues))
    rotation_settings = _baggage_rotation_settings_from_data(state.get("baggageRotationSettings"))
    return raw_scenario, scenario, assignments, assignment_source, imported_day, rotation_settings


def create_app(
    scenario_path: Path = DEFAULT_SCENARIO_PATH,
    assignments_path: Path = DEFAULT_ASSIGNMENTS_PATH,
    database_path: Path | None = None,
) -> FastAPI:
    default_state = {
        "scenario": _read_data(scenario_path),
        "assignments": _read_data(assignments_path)["assignments"],
        "assignmentSource": "fixture",
        "importedDay": None,
        "baggageRotationSettings": _baggage_rotation_settings_data(BaggageRotationSettings()),
    }
    store = LocalStateStore(database_path or Path(os.environ.get("GATE_SCHEDULE_DATA", ROOT / "data-v2")) / "gate-schedule.sqlite3")
    raw_scenario, scenario, saved_assignments, assignment_source, imported_day, rotation_settings = _runtime_from_state(
        store.load_or_initialize(default_state)
    )

    app = FastAPI(title="登机口保障排班")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.state.store = store
    app.state.raw_scenario = raw_scenario
    app.state.scenario = scenario
    app.state.saved_assignments = saved_assignments
    app.state.assignment_source = assignment_source
    app.state.import_preview = None
    app.state.solve_preview = None
    app.state.imported_day = imported_day
    app.state.baggage_rotation_settings = rotation_settings

    def persist_state() -> None:
        app.state.store.save(
            {
                "scenario": app.state.raw_scenario,
                "assignments": _assignments_data(app.state.saved_assignments),
                "assignmentSource": app.state.assignment_source,
                "importedDay": imported_day_to_dict(app.state.imported_day) if app.state.imported_day else None,
                "baggageRotationSettings": _baggage_rotation_settings_data(app.state.baggage_rotation_settings),
            }
        )

    def baggage_rotation_data() -> dict:
        day = app.state.import_preview or app.state.imported_day
        baggage_flight_ids = {
            cell.flight_id
            for cell in day.cells
            if cell.role_name == "大件行李拦截" and imported_required_count(day, cell) != 0
        } if day else set()
        baggage_flights = tuple(flight for flight in day.flights if flight.id in baggage_flight_ids) if day else ()
        segments = baggage_rotation_segments(
            baggage_flights,
            app.state.baggage_rotation_settings,
        )
        return {
            **_baggage_rotation_settings_data(app.state.baggage_rotation_settings),
            "segments": [{"start": segment.start, "end": segment.end} for segment in segments],
        }

    def candidate_assignments(payload: ManualAssignmentsInput) -> tuple[Assignment, ...]:
        return tuple(Assignment(item.taskId, item.memberId) for item in payload.assignments)

    def candidate_imported_day(payload: ImportedDayInput) -> ImportedDay:
        current = app.state.import_preview or app.state.imported_day
        if current is None:
            raise HTTPException(status_code=404, detail="请先导入 Excel")
        updates = [item.model_dump(exclude_unset=True) for item in payload.cells]
        role_configs = [item.model_dump() for item in payload.roleConfigs] if payload.roleConfigs is not None else None
        member_configs = [item.model_dump() for item in payload.memberConfigs] if payload.memberConfigs is not None else None
        attendance = [item.model_dump() for item in payload.memberAvailability] if payload.memberAvailability is not None else None
        occupancies = [item.model_dump() for item in payload.extraOccupancies] if payload.extraOccupancies is not None else None
        return update_imported_day(
            current, updates, role_configs, member_configs, attendance, occupancies,
            payload.remarksReviewed, payload.transitionMinutes,
        )

    @app.get("/api/schedule")
    def get_schedule() -> dict:
        return _schedule_data(app.state.scenario, app.state.saved_assignments, app.state.assignment_source)

    @app.get("/api/baggage-rotation-settings")
    def get_baggage_rotation_settings() -> dict:
        return baggage_rotation_data()

    @app.post("/api/baggage-rotation-settings")
    def update_baggage_rotation_settings(payload: BaggageRotationSettingsInput) -> dict:
        app.state.baggage_rotation_settings = BaggageRotationSettings(
            enabled=payload.enabled,
            interval_minutes=payload.interval_minutes,
        )
        persist_state()
        return baggage_rotation_data()

    @app.post("/api/validate-manual-assignments")
    def validate_manual_assignments(payload: ManualAssignmentsInput) -> dict:
        assignments = candidate_assignments(payload)
        issues = validate_schedule(app.state.scenario, assignments)
        return {
            "valid": not issues,
            "issues": [_issue_data(issue) for issue in issues],
            "taskMatrix": task_matrix(app.state.scenario, assignments),
            "memberPaths": _schedule_data(app.state.scenario, assignments, app.state.assignment_source)["memberPaths"],
        }

    @app.post("/api/manual-assignments")
    def save_manual_assignments(payload: ManualAssignmentsInput):
        assignments = candidate_assignments(payload)
        issues = validate_schedule(app.state.scenario, assignments)
        if issues:
            return JSONResponse(
                status_code=422,
                content={"valid": False, "issues": [_issue_data(issue) for issue in issues]},
            )
        app.state.saved_assignments = assignments
        app.state.assignment_source = "manual"
        persist_state()
        return {"valid": True, **_schedule_data(app.state.scenario, assignments, app.state.assignment_source)}

    @app.post("/api/solve")
    def solve_schedule() -> dict:
        result = solve(app.state.scenario)
        issues = validate_schedule(app.state.scenario, result.assignments)
        accepted = result.status in {"OPTIMAL", "FEASIBLE"} and not issues
        preview_id = None
        if accepted:
            preview_id = str(uuid4())
            app.state.solve_preview = (preview_id, deepcopy(app.state.raw_scenario), app.state.saved_assignments, result.assignments)
        return {
            "status": result.status,
            "diagnostics": list(result.diagnostics),
            "externalSupportByTask": result.external_support_by_task,
            "validationIssues": [_issue_data(issue) for issue in issues],
            "accepted": accepted,
            "previewId": preview_id,
            **_schedule_data(app.state.scenario, result.assignments if accepted else app.state.saved_assignments, "automatic" if accepted else app.state.assignment_source),
        }

    @app.post("/api/solve/confirm")
    async def confirm_solution(request: Request):
        payload = await request.json()
        preview = app.state.solve_preview
        if not preview or payload.get("previewId") != preview[0] or preview[1] != app.state.raw_scenario or preview[2] != app.state.saved_assignments:
            raise HTTPException(409, "预览已失效，请重新生成")
        issues = validate_schedule(app.state.scenario, preview[3])
        if issues:
            raise HTTPException(422, "预览未通过独立校验")
        app.state.store.save({"scenario": app.state.raw_scenario, "assignments": _assignments_data(preview[3]), "assignmentSource": "automatic", "importedDay": imported_day_to_dict(app.state.imported_day) if app.state.imported_day else None, "baggageRotationSettings": _baggage_rotation_settings_data(app.state.baggage_rotation_settings)})
        app.state.saved_assignments = preview[3]
        app.state.assignment_source = "automatic"
        app.state.solve_preview = None
        return _schedule_data(app.state.scenario, app.state.saved_assignments, "automatic")

    @app.post("/api/import-excel")
    async def import_excel(request: Request, serviceDate: str):
        try:
            candidate = import_day(await request.body(), serviceDate, app.state.imported_day)
            app.state.import_preview = candidate
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _imported_day_validation_data(app.state.import_preview)

    @app.post("/api/imported-day/validate")
    def validate_imported_day_changes(payload: ImportedDayInput) -> dict:
        candidate = candidate_imported_day(payload)
        app.state.import_preview = candidate
        return _imported_day_validation_data(candidate)

    @app.post("/api/imported-day/confirm")
    def confirm_imported_day(payload: ImportedDayInput):
        candidate = candidate_imported_day(payload)
        result = _imported_day_validation_data(candidate)
        if not result["valid"]:
            return JSONResponse(status_code=422, content=result)
        app.state.import_preview = candidate
        app.state.imported_day = candidate
        persist_state()
        return result

    @app.post("/api/imported-day/solve")
    def solve_imported_day() -> dict:
        day = app.state.import_preview or app.state.imported_day
        if day is None:
            raise HTTPException(status_code=404, detail="请先导入或打开日表")
        imported_issues = validate_imported_day(day)
        scenario, conversion_issues = imported_day_to_scenario(
            day,
            baggage_interval_minutes=app.state.baggage_rotation_settings.interval_minutes,
            baggage_rotation_enabled=app.state.baggage_rotation_settings.enabled,
        )
        all_issues = imported_issues + conversion_issues
        if all_issues:
            return JSONResponse(status_code=422, content={"valid": False, "issues": [_issue_data(issue) for issue in all_issues]})
        result = solve(scenario)
        schedule_issues = validate_schedule(scenario, result.assignments)
        accepted = result.status in {"OPTIMAL", "FEASIBLE"} and not schedule_issues
        return {
            "valid": accepted,
            "status": result.status,
            "diagnostics": list(result.diagnostics),
            "validationIssues": [_issue_data(issue) for issue in schedule_issues],
            "accepted": accepted,
            **_schedule_data(scenario, result.assignments, "automatic"),
        }

    @app.post("/api/imported-day/confirm-schedule")
    def confirm_imported_schedule(payload: ImportedScheduleInput) -> dict:
        candidate = candidate_imported_day(payload.day)
        imported_result = _imported_day_validation_data(candidate)
        if not imported_result["valid"]:
            return JSONResponse(status_code=422, content={"valid": False, "issues": imported_result["issues"]})
        scenario, conversion_issues = imported_day_to_scenario(
            candidate,
            baggage_interval_minutes=app.state.baggage_rotation_settings.interval_minutes,
            baggage_rotation_enabled=app.state.baggage_rotation_settings.enabled,
        )
        if conversion_issues:
            return JSONResponse(status_code=422, content={"valid": False, "issues": [_issue_data(issue) for issue in conversion_issues]})
        assignments = tuple(Assignment(item.taskId, item.memberId) for item in payload.assignments)
        issues = validate_schedule(scenario, assignments)
        if issues:
            return JSONResponse(status_code=422, content={"valid": False, "issues": [_issue_data(issue) for issue in issues]})
        app.state.import_preview = candidate
        app.state.imported_day = candidate
        app.state.raw_scenario = scenario_to_dict(scenario)
        app.state.scenario = scenario
        app.state.saved_assignments = assignments
        app.state.assignment_source = "automatic"
        persist_state()
        return {"valid": True, **_schedule_data(scenario, assignments, "automatic")}

    @app.get("/api/imported-day")
    def get_imported_day() -> dict:
        if app.state.imported_day is None:
            raise HTTPException(status_code=404, detail="当前没有已保存导入日表")
        return _imported_day_validation_data(app.state.imported_day)

    @app.post("/api/imported-day/open")
    def open_saved_imported_day() -> dict:
        if app.state.imported_day is None:
            raise HTTPException(status_code=404, detail="当前没有已保存导入日表")
        app.state.import_preview = app.state.imported_day
        return _imported_day_validation_data(app.state.imported_day)

    @app.get("/api/export-excel")
    def export_excel():
        if app.state.imported_day is None:
            raise HTTPException(status_code=404, detail="请先确认并保存导入日表")
        filename = quote(f"登机口保障-{app.state.imported_day.service_date}.xlsx")
        if (
            app.state.assignment_source in ("automatic", "manual")
            and app.state.scenario.model_version == "imported-day-v1"
            and app.state.scenario.service_date == app.state.imported_day.service_date
        ):
            content = export_imported_schedule(app.state.imported_day, app.state.scenario, app.state.saved_assignments)
        else:
            content = export_imported_day(app.state.imported_day)
        return StreamingResponse(
            BytesIO(content),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )

    @app.get("/api/backup")
    def export_backup() -> dict:
        backup = app.state.store.backup()
        backup["state"]["baggageRotationSettings"] = _baggage_rotation_settings_data(
            app.state.baggage_rotation_settings
        )
        return backup

    @app.post("/api/restore-backup")
    async def restore_backup(request: Request):
        try:
            backup = await request.json()
            if backup.get("formatVersion") != 1 or not isinstance(backup.get("state"), dict):
                raise ValueError("备份格式版本不支持")
            (
                restored_raw,
                restored_scenario,
                restored_assignments,
                restored_source,
                restored_imported_day,
                restored_rotation_settings,
            ) = _runtime_from_state(backup["state"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            return JSONResponse(status_code=422, content={"detail": f"备份无效：{error}"})
        app.state.raw_scenario = restored_raw
        app.state.scenario = restored_scenario
        app.state.saved_assignments = restored_assignments
        app.state.assignment_source = restored_source
        app.state.import_preview = restored_imported_day
        app.state.imported_day = restored_imported_day
        app.state.baggage_rotation_settings = restored_rotation_settings
        persist_state()
        return {"valid": True, **_schedule_data(restored_scenario, restored_assignments, restored_source)}

    return app


_runtime_app = None


async def app(scope, receive, send):
    """Keep module imports read-only; legacy uvicorn entry initializes on demand."""
    global _runtime_app
    if _runtime_app is None:
        _runtime_app = create_app()
    await _runtime_app(scope, receive, send)
