"""Classify Office Timeline shapes on slide 1 without relying on label text."""
from __future__ import annotations

from .constants import LOWER_TRACK_Y, UPPER_TRACK_Y
from .pptx_utils import detect_track_threshold, is_study_label, normalize_text, track_of

OTL_DATE = "date"
OTL_LABEL = "label"
OTL_NOTE = "note"
OTL_TITLE = "title"
OTL_STUDY = "study"


def otl_kind(shape, threshold: int | None = None) -> str | None:
    """Return shape role on the timeline, or None if unrelated."""
    if not shape.has_text_frame:
        return None
    name = shape.name or ""
    text = normalize_text(shape.text_frame.text)
    if shape.left < 200_000 and (is_study_label(text) or "TextBox" in name):
        return OTL_STUDY
    if "StartDate" in name:
        return OTL_DATE
    if "_Title" in name and "OTLSHAPE" in name:
        return OTL_TITLE
    if "OTLSHAPE" in name and "_Shape" in name:
        if shape.width >= 500_000:
            return OTL_NOTE
        return None
    if "Rectangle" in name:
        assert threshold is not None
        track = track_of(shape, threshold)
        track_y = UPPER_TRACK_Y if track == "upper" else LOWER_TRACK_Y
        if shape.top < track_y:
            return OTL_DATE
        return OTL_LABEL
    return None


def scan_track_clusters(slide) -> tuple[dict[str, object], dict[str, list[dict]]]:
    """Group OTL milestone slots per track, anchored on date shapes."""
    from .constants import COL_PAIR_MAX_DIST

    threshold = detect_track_threshold(slide)
    study: dict[str, object] = {}
    buckets: dict[str, list[dict]] = {"upper": [], "lower": []}

    for shape in slide.shapes:
        kind = otl_kind(shape, threshold)
        if not kind:
            continue
        track = track_of(shape, threshold)
        if kind == OTL_STUDY:
            study[track] = shape
            continue
        if track not in buckets:
            continue
        buckets[track].append({"kind": kind, "shape": shape, "left": shape.left})

    clusters: dict[str, list[dict]] = {"upper": [], "lower": []}
    for track in ("upper", "lower"):
        items = buckets[track]
        anchors = sorted([i for i in items if i["kind"] == OTL_DATE], key=lambda i: i["left"])
        groups: list[dict] = []
        for anchor in anchors:
            groups.append(
                {
                    "anchor": anchor["left"],
                    "date": anchor["shape"],
                    "label": None,
                    "note": None,
                    "title": None,
                }
            )
        for item in items:
            if item["kind"] == OTL_DATE:
                continue
            if not groups:
                continue
            nearest = min(groups, key=lambda g: abs(g["anchor"] - item["left"]))
            if abs(nearest["anchor"] - item["left"]) <= COL_PAIR_MAX_DIST:
                if nearest[item["kind"]] is None:
                    nearest[item["kind"]] = item["shape"]
        clusters[track] = groups

    return study, clusters
