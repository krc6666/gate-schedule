"""Qualification import contract; a workbook proposal is applied only explicitly."""
from __future__ import annotations


def validate_qualification_config(data):
    if not isinstance(data, dict) or not isinstance(data.get("people"), list) or not isinstance(data.get("roles"), list):
        raise ValueError("资质配置必须包含人员名单和岗位列表")

    def names(values, label):
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in values):
            raise ValueError(f"{label}必须为非空文字列表")
        if len(values) != len(set(values)):
            raise ValueError(f"{label}重复，请核对姓名和岗位")

    names(data["people"], "人员姓名")
    if any(n in {"值机支援", "支援", "（填写姓名）"} for n in data["people"]):
        raise ValueError("人员表头请填写实际姓名，不要填支援标签或占位文字")
    if any(not isinstance(r, dict) or not isinstance(r.get("roleName"), str) for r in data["roles"]):
        raise ValueError("岗位配置格式无效")
    names([r["roleName"] for r in data["roles"]], "岗位名称")
    for role in data["roles"]:
        names(role.get("memberNames"), f"{role['roleName']}的资质人员")
        if set(role["memberNames"]) - set(data["people"]):
            raise ValueError(f"{role['roleName']}包含未在人员表头列出的姓名")
    if not isinstance(data.get("source", "手动配置"), str):
        raise ValueError("资质来源说明必须为文字")
    return {"people": list(data["people"]), "roles": [{"roleName": r["roleName"], "memberNames": list(r["memberNames"])} for r in data["roles"]], "source": data.get("source", "手动配置")}
