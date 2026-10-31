"""Exercise the actual EXE in an independent project temporary directory."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate_schedule.excel_io import export_imported_day, imported_day_from_dict, export_qualifications, import_qualifications


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--workspace", type=Path, help="额外恢复并验证单文件完整试用配置（仅隔离测试库）")
    parser.add_argument("--configuration", type=Path, help="额外验证完整Excel配置维护（仅隔离测试库）")
    args = parser.parse_args()
    exe = args.executable.resolve(strict=True)
    if args.report and args.report.exists():
        parser.error("报告已存在，请使用新文件名")
    temporary_root = Path(__file__).resolve().parents[1] / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gate-portable-acceptance-", dir=temporary_root) as directory:
        directory = Path(directory)
        environment = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        environment["GATE_SCHEDULE_DATA"] = str(directory / "data")
        environment["TEMP"] = environment["TMP"] = str(temporary_root)
        process = None
        address = ""
        log = (directory / "server.log").open("wb")

        def request(path, payload=None, content=None):
            body = json.dumps(payload).encode("utf-8") if payload is not None else content
            with urlopen(Request(address + path, data=body, headers={"Content-Type": "application/json"}), timeout=20) as response:
                data = response.read()
                return json.loads(data) if "json" in response.headers.get("Content-Type", "") else data

        def start():
            nonlocal process, address
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            address = f"http://127.0.0.1:{port}"
            process = subprocess.Popen([str(exe), "--no-browser", "--port", str(port)], cwd=directory, env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            end = time.monotonic() + 30
            while time.monotonic() < end:
                try:
                    return request("/api/workspace")
                except (OSError, HTTPError):
                    if process.poll() is not None:
                        raise RuntimeError((directory / "server.log").read_text(errors="replace"))
                    time.sleep(0.1)
            raise RuntimeError("便携程序启动超时")

        def stop():
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

        def command(action, payload=None, plan_id=None):
            state = request("/api/workspace")
            return request("/api/workspace/command", {"expectedRevision": state["revision"], "action": action, "payload": payload or {}, "planId": plan_id})

        try:
            assert start()["plans"] == {}
            assert "登机口保障排班" in request("/").decode("utf-8")
            qualification_input = {"people": ["甲", "乙", "丙"], "roles": [{"roleName": "接机1", "memberNames": ["甲", "乙", "丙"]}], "source": "手动配置"}
            preview = request("/api/workspace/qualifications/preview", content=export_qualifications(qualification_input))
            assert request("/api/workspace")["people"] == []
            command("import-qualifications", preview["payload"])
            assert import_qualifications(request("/api/workspace/qualifications/template"))["people"] == qualification_input["people"]
            command("save-role", {"roleName": "接机1", "requiredCount": 2})
            state = command("new-plan", {"serviceDate": "2026-08-02", "roleNames": ["接机1"]})
            plan_id = state["selectedPlanId"]
            day = state["plans"][plan_id]["day"]
            day["flights"] = [{"id": "f1", "flightNo": "AK151", "windowStart": "23:05", "windowEnd": "次日00:05"}]
            day["roleConfigs"] = [{"roleName": "接机1", "requiredCount": 2}]
            day["cells"] = [{"id": "c1", "flightId": "f1", "flightNo": "AK151", "roleName": "接机1", "rawValue": "", "internalNames": [], "externalLabels": [], "semantics": "empty", "workStart": "23:05", "workEnd": "次日00:05", "fixedMemberNames": []}]
            day["memberAvailability"] = [{"memberName": p["name"], "participating": True, "availableFrom": "22:00", "availableUntil": "次日02:00"} for p in state["people"]]
            state = command("save-input", {"day": day}, plan_id)
            task_id = state["plans"][plan_id]["schedule"]["tasks"][0]["id"]
            people = state["people"]
            assigned = [{"taskId": task_id, "memberId": people[0]["id"]}]
            command("assign", {"assignments": assigned}, plan_id)
            stop()
            assert start()["plans"][plan_id]["draft"] == assigned
            state = command("solve", {}, plan_id)
            assert state["plans"][plan_id]["adopted"] is None
            command("cancel-preview", {}, plan_id)
            assert request("/api/workspace")["plans"][plan_id]["draft"] == assigned
            assigned = [{"taskId": task_id, "memberId": p["id"]} for p in people[1:]]
            command("assign", {"assignments": assigned}, plan_id)
            command("adopt", {}, plan_id)
            backup = request("/api/workspace/backup")
            workbook = load_workbook(BytesIO(request(f"/api/workspace/export/{plan_id}")))
            assert workbook.worksheets[0].title == "正式保障班表"
            assert set(workbook["正式任务明细"].cell(2, 7).value.split("/")) == {"乙", "丙"}
            content = export_imported_day(imported_day_from_dict(day))
            revision = request("/api/workspace")["revision"]
            imported = request(f"/api/workspace/import?serviceDate=2026-08-02&expectedRevision={revision}", content=content)
            imported_id = imported["selectedPlanId"]
            assert imported_id != plan_id and len(imported["plans"]) == 2
            command("solve", {}, imported_id)
            command("adopt", {}, imported_id)
            assert request(f"/api/workspace/export/{imported_id}").startswith(b"PK")
            stop()
            assert start()["plans"][imported_id]["adopted"]
            revision = request("/api/workspace")["revision"]
            restored = request(f"/api/workspace/restore?expectedRevision={revision}", backup)
            assert restored["plans"][plan_id]["adopted"]["assignments"] == assigned
            assert len(restored["plans"]) == 1
            if args.workspace:
                complete = json.loads(args.workspace.read_text(encoding="utf-8"))
                restored = request(f"/api/workspace/restore?expectedRevision={restored['revision']}", complete)
                trial_id = restored["selectedPlanId"]
                trial = restored["plans"][trial_id]
                assert len(restored["people"]) == 15 and len(trial["day"]["flights"]) == 7
                assert len(trial["day"]["cells"]) == 70 and trial["valid"] and trial["draft"]
                assert trial["preview"] is None and trial["adopted"] is None
                assert restored["trialInfo"]["assumptions"]
                stop()
                assert start()["plans"][trial_id]["draft"] == trial["draft"]
                assert command("solve", {}, trial_id)["plans"][trial_id]["valid"]
                assert command("cancel-preview", {}, trial_id)["plans"][trial_id]["draft"] == trial["draft"]
                formal = command("adopt", {}, trial_id)["plans"][trial_id]["adopted"]
                assert request(f"/api/workspace/export/{trial_id}").startswith(b"PK")
                stop()
                restarted = start()
                assert restarted["plans"][trial_id]["adopted"] == formal and restarted["trialInfo"] == restored["trialInfo"]
            if args.configuration:
                before = request("/api/workspace")
                preview = request("/api/workspace/configuration/preview", content=args.configuration.read_bytes())
                assert request("/api/workspace") == before
                assert preview["peopleCount"] == 15 and preview["flightCount"] == 7
                state = command("import-complete-configuration", preview["payload"])
                config_id = state["selectedPlanId"]
                assert state["plans"][config_id]["draft"] == []
                for prior_id, prior_plan in before["plans"].items():
                    assert state["plans"][prior_id] == prior_plan
                assert command("solve", {}, config_id)["plans"][config_id]["valid"]
                adopted_config = command("adopt", {}, config_id)["plans"][config_id]["adopted"]
                content = request(f"/api/workspace/configuration/export/{config_id}")
                workbook = load_workbook(BytesIO(content))
                matrix = workbook["岗位资质"]
                target = next(c for row in matrix.iter_rows(min_row=2, min_col=2) for c in row if c.value == 1)
                revoked_name, revoked_role = matrix.cell(1, target.column).value, matrix.cell(target.row, 1).value
                target.value = 0
                changed = BytesIO()
                workbook.save(changed)
                preview = request("/api/workspace/configuration/preview", content=changed.getvalue())
                assert preview["removedQualificationCount"] == 1
                state = command("import-complete-configuration", preview["payload"])
                modified_id = state["selectedPlanId"]
                assert revoked_role not in next(p["eligibleRoleNames"] for p in state["people"] if p["name"] == revoked_name)
                assert state["plans"][config_id]["adopted"] == adopted_config
                assert command("solve", {}, modified_id)["plans"][modified_id]["valid"]
                stop()
                state = start()
                assert revoked_role not in next(p["eligibleRoleNames"] for p in state["people"] if p["name"] == revoked_name)
                assert state["plans"][config_id]["adopted"] == adopted_config
                assert request(f"/api/workspace/export/{config_id}").startswith(b"PK")
            report = {"executable": str(exe), "isolatedWorkingDirectory": True, "temporaryFilesInProject": True, "pythonAndNodeRemovedFromPath": True, "isolatedData": True, "firstStart": True, "qualificationImportExport": True, "partialDraftRestart": True, "previewCancellation": True, "manualAdoption": True, "excelImportExport": True, "backupRestore": True, "adoptedRestart": True, "cleanWindows": "待Owner在另一台未安装Python/Node的电脑验收"}
            if args.workspace:
                report["singleFileTrialRestoreResolveCancelAdoptExportRestart"] = True
            if args.configuration:
                report["completeExcelPreviewImportSolveExportRevokeKeepHistoryRestart"] = True
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            stop()
            log.close()


if __name__ == "__main__":
    main()
