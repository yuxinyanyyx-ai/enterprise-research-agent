"""Safe, deterministic loading of Agent Eval YAML cases."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from tests.agent_eval.schema import AgentCase, CaseFile


class CaseLoadError(ValueError):
    """Raised when an Agent Eval case file violates the case contract."""


def _case_hint(raw_case: Any, index: int) -> str:
    if isinstance(raw_case, dict) and raw_case.get("id"):
        return str(raw_case["id"])
    return f"case[{index}]"


def _load_file(path: Path) -> list[AgentCase]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CaseLoadError(f"{path}: unable to read YAML: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise CaseLoadError(f"{path}: top-level mapping must contain a 'cases' list")

    raw_cases = payload["cases"]
    try:
        return CaseFile.model_validate(payload).cases
    except ValidationError as exc:
        case_index = next(
            (
                location
                for error in exc.errors()
                for location in error["loc"]
                if isinstance(location, int)
            ),
            0,
        )
        hint = _case_hint(raw_cases[case_index], case_index) if raw_cases else "case[0]"
        raise CaseLoadError(f"{path} [{hint}]: {exc}") from exc


def _requested_case_ids(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def load_cases(
    case_dir: str | Path,
    *,
    case_filter: str | None = None,
) -> list[AgentCase]:
    """Load, validate, de-duplicate, filter, and stably sort YAML cases."""

    root = Path(case_dir)
    paths = sorted(root.rglob("*.yaml"), key=lambda path: path.as_posix())
    requested = _requested_case_ids(
        case_filter if case_filter is not None else os.getenv("AGENT_EVAL_CASE")
    )

    loaded: list[tuple[str, AgentCase]] = []
    owners: dict[str, Path] = {}
    for path in paths:
        for case in _load_file(path):
            if case.id in owners:
                raise CaseLoadError(
                    f"duplicate case id '{case.id}' in {owners[case.id]} and {path}"
                )
            owners[case.id] = path
            loaded.append((path.as_posix(), case))

    if requested is not None:
        unknown = requested.difference(owners)
        if unknown:
            raise CaseLoadError(
                f"AGENT_EVAL_CASE references unknown case(s): {', '.join(sorted(unknown))}"
            )
        loaded = [item for item in loaded if item[1].id in requested]

    return [case for _, case in sorted(loaded, key=lambda item: (item[0], item[1].id))]
