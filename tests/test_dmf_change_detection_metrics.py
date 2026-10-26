import json
from collections import Counter
from pathlib import Path

from src.dmf_history.comparison import compare_records
from src.schemas.dmf import DMFRecord


LABELS_PATH = Path(__file__).with_name("dmf_change_labels.json")


def event_signature(event) -> tuple[str, str, tuple[str, ...]]:
    return (
        event.change_type,
        event.business_key,
        tuple(sorted(change.field for change in event.changes)),
    )


def test_labeled_change_detection_precision_and_recall() -> None:
    cases = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    true_positives = false_positives = false_negatives = 0

    for case in cases:
        predicted_events, _, _ = compare_records(
            [DMFRecord.model_validate(row) for row in case["previous"]],
            [DMFRecord.model_validate(row) for row in case["current"]],
        )
        predicted = Counter(event_signature(event) for event in predicted_events)
        expected = Counter(
            (
                event["change_type"],
                event["business_key"],
                tuple(sorted(event.get("fields", []))),
            )
            for event in case["expected_events"]
        )
        true_positives += sum((predicted & expected).values())
        false_positives += sum((predicted - expected).values())
        false_negatives += sum((expected - predicted).values())

    precision = true_positives / (true_positives + false_positives)
    recall = true_positives / (true_positives + false_negatives)

    assert len(cases) == 6
    assert (true_positives, false_positives, false_negatives) == (4, 0, 0)
    assert precision == 1.0
    assert recall == 1.0
    print(
        "DMF annotated change metrics: "
        f"cases={len(cases)}, TP={true_positives}, FP={false_positives}, "
        f"FN={false_negatives}, precision={precision:.3f}, recall={recall:.3f}"
    )
