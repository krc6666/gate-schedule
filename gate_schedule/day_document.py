"""Day-plan document adapter, independent of spreadsheet or storage libraries."""
from .domain import (
    ImportedDay, ImportedFlight, ImportedOccupancy, ImportedMemberAvailability,
    ImportedMemberConfig, ImportedRoleConfig, ImportedCell, ImportedRemark,
    imported_member_names, imported_required_count, validate_imported_day,
)


def imported_day_to_dict(day: ImportedDay) -> dict:
    return {
        **({'workRules': day.work_rules} if day.work_rules is not None else {}),
        **({'workRulesOverride': True} if day.work_rules_override else {}),
        **({'taskHandoffs': list(day.task_handoffs)} if day.task_handoffs else {}),
        **({'flowPreferences': list(day.flow_preferences)} if day.flow_preferences is not None else {}),
        "serviceDate": day.service_date,
        "title": day.title,
        "flights": [
            {"id": flight.id, "flightNo": flight.flight_no, "windowStart": flight.window_start, "windowEnd": flight.window_end, "rawHeader": flight.raw_header,
             **({'arrivalTime': flight.arrival_time} if flight.arrival_time is not None else {}),
             **({'departureTime': flight.departure_time} if flight.departure_time is not None else {})}
            for flight in day.flights
        ],
        "roleNames": list(day.role_names),
        "roster": list(day.roster),
        "extraOccupancies": [
            {"id": item.id, "label": item.label, "memberNames": list(item.member_names), "start": item.start, "end": item.end}
            for item in day.extra_occupancies
        ],
        "remarksReviewed": day.remarks_reviewed,
        "transitionMinutes": day.transition_minutes,
        "releasePolicy": day.release_policy,
        **({'dedicatedFlightProtection': True} if day.dedicated_flight_protection else {}),
        **({"attendanceMode": day.attendance_mode} if day.attendance_mode != "bounded" else {}),
        "phaseHandoffs": [list(pair) for pair in day.phase_handoffs],
        "memberNames": list(imported_member_names(day)),
        "memberAvailability": [
            {
                "memberName": entry.member_name, "participating": entry.participating,
                "availableFrom": entry.available_from, "availableUntil": entry.available_until,
                **({"halfRest": entry.half_rest} if entry.half_rest is not None else {}),
            }
            for entry in day.member_availability
        ],
        "memberConfigs": [
            {"memberName": config.member_name, "eligibleRoleNames": list(config.eligible_role_names)}
            for config in day.member_configs
        ],
        "roleConfigs": [
            {"roleName": config.role_name, "requiredCount": config.required_count, **({"exclusiveSameFlight": True} if config.exclusive_same_flight else {}), **({"workMode": config.work_mode, "concurrent": config.concurrent} if day.attendance_mode == "duty" or config.work_mode != "timed" or config.concurrent else {})}
            | ({'relativeTiming': config.relative_timing} if config.relative_timing is not None else {})
            for config in day.role_configs
        ],
        "cells": [
            {
                "id": cell.id,
                "roleName": cell.role_name,
                "flightId": cell.flight_id,
                "flightNo": cell.flight_no,
                "rawValue": cell.raw_value,
                "internalNames": list(cell.internal_names),
                "externalLabels": list(cell.external_labels),
                "semantics": cell.semantics,
                "workStart": cell.work_start,
                "workEnd": cell.work_end,
                **({"workModeOverride": cell.work_mode_override} if cell.work_mode_override is not None else {}),
                "requiredCountOverride": cell.required_count_override,
                "fixedMemberNames": list(cell.fixed_member_names),
                "effectiveRequiredCount": imported_required_count(day, cell),
            }
            for cell in day.cells
        ],
        "remarks": [
            {"kind": remark.kind, "rawText": remark.raw_text, "people": list(remark.people), "time": remark.time}
            for remark in day.remarks
        ],
    }



def imported_day_from_dict(data: dict) -> ImportedDay:
    if data.get("releasePolicy", "flight") not in ("flight", "task"):
        raise ValueError("支援释放规则无效")
    return ImportedDay(
        service_date=data["serviceDate"],
        title=data["title"],
        flights=tuple(
            ImportedFlight(item["id"], item["flightNo"], item["windowStart"], item["windowEnd"], item.get("rawHeader", ""), item.get('arrivalTime'), item.get('departureTime'))
            for item in data["flights"]
        ),
        role_names=tuple(data["roleNames"]),
        roster=tuple(data.get("roster", ())),
        extra_occupancies=tuple(
            ImportedOccupancy(item["id"], item["label"], tuple(item["memberNames"]), item["start"], item["end"])
            for item in data.get("extraOccupancies", [])
        ),
        remarks_reviewed=data.get("remarksReviewed", not any(item.get("kind") == "support" for item in data.get("remarks", []))),
        transition_minutes=data.get("transitionMinutes", 0),
        release_policy=data.get("releasePolicy", "flight"),
        attendance_mode=data.get("attendanceMode", "bounded"),
        work_rules=data.get('workRules'), work_rules_override=data.get('workRulesOverride', False),
        task_handoffs=tuple(data.get('taskHandoffs', ())),
        dedicated_flight_protection=data.get('dedicatedFlightProtection', False),
        flow_preferences=tuple(data['flowPreferences']) if 'flowPreferences' in data else None,
        phase_handoffs=_phase_handoffs(data.get("phaseHandoffs", []), {f["id"] for f in data["flights"]}),
        member_availability=tuple(
            ImportedMemberAvailability(item["memberName"], item["participating"], item.get("availableFrom"), item.get("availableUntil"), item.get("halfRest"))
            for item in data.get("memberAvailability", [])
        ),
        member_configs=tuple(
            ImportedMemberConfig(item["memberName"], tuple(item["eligibleRoleNames"]))
            for item in data.get("memberConfigs", [])
        ),
        role_configs=tuple(
            ImportedRoleConfig(item["roleName"], item.get("requiredCount"), item.get("workMode", "timed"), item.get("concurrent", False), item.get("exclusiveSameFlight", False), item.get('relativeTiming'))
            for item in data.get("roleConfigs", [])
        ),
        cells=tuple(
            ImportedCell(
                id=item["id"],
                role_name=item["roleName"],
                flight_id=item["flightId"],
                flight_no=item["flightNo"],
                raw_value=item["rawValue"],
                internal_names=tuple(item["internalNames"]),
                external_labels=tuple(item["externalLabels"]),
                semantics=item["semantics"],
                work_start=item.get("workStart"),
                work_end=item.get("workEnd"),
                work_mode_override=item.get("workModeOverride"),
                required_count_override=item.get("requiredCountOverride"),
                fixed_member_names=tuple(item.get("fixedMemberNames", ())),
            )
            for item in data["cells"]
        ),
        remarks=tuple(
            ImportedRemark(item["kind"], item["rawText"], tuple(item.get("people", ())), item.get("time"))
            for item in data["remarks"]
        ),
    )



def _phase_handoffs(values, known):
    if not isinstance(values, (list, tuple)):
        raise ValueError("航班衔接必须是列表")
    result = []
    for pair in values:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(not isinstance(p, str) or p not in known for p in pair) or pair[0] == pair[1] or tuple(pair) in result:
            raise ValueError("航班衔接引用未知航班、自身或重复关系")
        result.append(tuple(pair))
    return tuple(result)
