from __future__ import annotations

import csv
import json

from scripts.import_place_preference_tags import load_payloads


def test_load_payloads_flattens_tags_and_counts_documents(tmp_path) -> None:
    path = tmp_path / "cards.csv"
    details = [
        {
            "code": "quiet",
            "label": "조용히 머물기 좋은",
            "confidence": 0.75,
            "positive_documents": 3,
            "negative_documents": 1,
            "source_count": 2,
        }
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["content_id", "extraction_version", "preference_details_json"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "content_id": "126499",
                "extraction_version": "preference-v1",
                "preference_details_json": json.dumps(details, ensure_ascii=False),
            }
        )

    assert load_payloads(path) == [
        {
            "content_id": "126499",
            "preference_code": "quiet",
            "preference_label": "조용히 머물기 좋은",
            "display_rank": 1,
            "mention_count": 4,
            "positive_document_count": 3,
            "negative_document_count": 1,
            "source_count": 2,
            "confidence": 0.75,
            "extraction_version": "preference-v1",
        }
    ]
