from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from .job_index import collect_key_paths


EXPORT_OPERATORS = ("contains", "starts_with", "ends_with", "equals")


@dataclass
class ExclusionMatch:
    path: str
    operator: str
    value: str
    matched_text: str


def normalize_export_exclusions(value: Any) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    rules: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        operator = str(item.get("operator") or "contains").strip()
        if operator not in EXPORT_OPERATORS:
            operator = "contains"
        rules.append(
            {
                "enabled": bool(item.get("enabled", True)),
                "path": str(item.get("path") or "").strip(),
                "operator": operator,
                "value": str(item.get("value") if item.get("value") is not None else ""),
                "case_sensitive": bool(item.get("case_sensitive", False)),
            }
        )
    return rules


def active_exclusion_rules(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    source = config or {}
    if not source.get("export_exclusions_enabled"):
        return []
    return normalize_export_exclusions(source.get("export_exclusions"))


def normalize_export_exclusion_presets(value: Any) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    presets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())
        if not name or name.casefold() in seen_names:
            continue
        preset_id = str(item.get("id") or "").strip()
        if not preset_id or preset_id in seen_ids:
            preset_id = uuid.uuid4().hex
        seen_ids.add(preset_id)
        seen_names.add(name.casefold())
        presets.append(
            {
                "id": preset_id,
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "rules": normalize_export_exclusions(item.get("rules")),
            }
        )
    return presets


def parse_export_path(path: str) -> list[str]:
    text = str(path or "").strip()
    if not text:
        return []
    if "->" in text:
        parts = [part.strip() for part in text.split("->")]
    else:
        parts = [part.strip() for part in text.split(".")]
    parts = [part for part in parts if part]
    if parts and parts[0] == "clip":
        parts = parts[1:]
    return parts


def arrow_path(dotted: str) -> str:
    parts = [part for part in str(dotted or "").split(".") if part]
    if not parts:
        return ""
    return "clip -> " + " -> ".join(parts)


def collect_export_key_paths(documents: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for document in documents:
        if isinstance(document, dict):
            found.update(collect_key_paths(document))
    return sorted(found, key=lambda item: item.split("."))


def export_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def resolved_values(document: Any, path: str) -> list[Any]:
    parts = parse_export_path(path)
    if not parts:
        return []
    found: list[Any] = []
    _collect(document, parts, found)
    return found


def _collect(node: Any, parts: list[str], found: list[Any]) -> None:
    if not parts:
        found.append(node)
        return
    part = parts[0]
    rest = parts[1:]
    if isinstance(node, dict):
        if part in node:
            _collect(node[part], rest, found)
        return
    if isinstance(node, list):
        if part.isdigit():
            index = int(part)
            if index < len(node):
                _collect(node[index], rest, found)
            return
        for item in node:
            _collect(item, parts, found)


def values_match(haystack: str, needle: str, operator: str, *, case_sensitive: bool) -> bool:
    if needle == "" or operator not in EXPORT_OPERATORS:
        return False
    left = haystack if case_sensitive else haystack.casefold()
    right = needle if case_sensitive else needle.casefold()
    if operator == "contains":
        return right in left
    if operator == "starts_with":
        return left.startswith(right)
    if operator == "ends_with":
        return left.endswith(right)
    return left == right


def first_matching_rule(document: Any, rules: list[dict[str, Any]] | None) -> ExclusionMatch | None:
    for rule in rules or []:
        if not rule.get("enabled", True):
            continue
        path = str(rule.get("path") or "").strip()
        value = str(rule.get("value") if rule.get("value") is not None else "")
        if not path or value == "":
            continue
        operator = str(rule.get("operator") or "contains")
        case_sensitive = bool(rule.get("case_sensitive"))
        for resolved in resolved_values(document, path):
            text = export_value_text(resolved)
            if values_match(text, value, operator, case_sensitive=case_sensitive):
                return ExclusionMatch(path=path, operator=operator, value=value, matched_text=text)
    return None
