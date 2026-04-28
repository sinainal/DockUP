from __future__ import annotations

import re
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "docking_app" / "static" / "app.js"


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"async function {re.escape(name)}\s*\([^)]*\)\s*{{", source)
    assert match, f"Missing function {name}"
    index = match.end()
    depth = 1
    while index < len(source) and depth:
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    assert depth == 0, f"Could not parse function {name}"
    return source[match.end() : index - 1]


def test_build_queue_button_appends_instead_of_updating_selected_batch() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    body = _function_body(source, "buildQueue")

    assert "update_batch_id" not in body
    assert "previousBatchIds" in body
    assert "appendedBatchIds.length === 1" in body


def test_queue_batch_editor_is_the_only_frontend_update_batch_flow() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    body = _function_body(source, "saveQueueBatchModal")

    assert "update_batch_id" in body
    assert "queueBatchModalDraft.batchId" in body
