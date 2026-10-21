from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MilestoneEvent:
    date: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"date": self.date, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MilestoneEvent:
        if not data:
            return cls()
        return cls(date=str(data.get("date") or ""), note=str(data.get("note") or ""))


@dataclass
class TrackData:
    study_label: str = ""
    events: dict[str, MilestoneEvent] = field(default_factory=dict)
    event_order: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_label": self.study_label,
            "event_order": list(self.event_order),
            "events": {name: ev.to_dict() for name, ev in self.events.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TrackData:
        if not data:
            return cls()
        events = {
            str(name): MilestoneEvent.from_dict(ev)
            for name, ev in (data.get("events") or {}).items()
        }
        order = [str(x) for x in (data.get("event_order") or []) if str(x) in events]
        for name in events:
            if name not in order:
                order.append(name)
        return cls(
            study_label=str(data.get("study_label") or ""),
            events=events,
            event_order=order,
        )


@dataclass
class BottomTables:
    global_status: str = ""
    global_next: str = ""
    china_status: str = ""
    china_next: str = ""
    risks: list[tuple[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([self.global_status, self.global_next, self.china_status, self.china_next, self.risks])

    def to_dict(self) -> dict[str, Any]:
        return {
            "global_status": self.global_status,
            "global_next": self.global_next,
            "china_status": self.china_status,
            "china_next": self.china_next,
            "risks": [[left, right] for left, right in self.risks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BottomTables:
        if not data:
            return cls()
        risks: list[tuple[str, str]] = []
        for item in data.get("risks") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                risks.append((str(item[0]), str(item[1])))
        return cls(
            global_status=str(data.get("global_status") or ""),
            global_next=str(data.get("global_next") or ""),
            china_status=str(data.get("china_status") or ""),
            china_next=str(data.get("china_next") or ""),
            risks=risks,
        )


@dataclass
class FillConfig:
    template: str
    data: str
    output: str
    data_lpt_slide: int = 2
    data_milestone_slide: int = 3
    slide_title: str | None = None
    upper: TrackData | None = None
    lower: TrackData | None = None
    bottom: BottomTables | None = None


@dataclass
class FillResult:
    output_path: str
    upper_milestones: list[str]
    lower_milestones: list[str]
    bottom_source: str
    bottom_risks_count: int = 0
    skipped_milestones: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "upper_milestones": self.upper_milestones,
            "lower_milestones": self.lower_milestones,
            "bottom_source": self.bottom_source,
            "bottom_risks_count": self.bottom_risks_count,
            "skipped_milestones": self.skipped_milestones,
        }
