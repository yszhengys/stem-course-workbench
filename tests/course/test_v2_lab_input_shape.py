import json

import pytest
from pydantic import ValidationError

from open_notebook.course.v2_contracts import ReplaceLabOperation


def test_replace_lab_accepts_an_object_but_rejects_a_json_string() -> None:
    payload = {
        "kind": "function_plot",
        "key": "limit-plot",
        "title": "Limit plot",
        "expressions": ["x"],
        "domain": {"x": [-1.0, 1.0]},
        "controls": [],
        "objects": [],
        "anchor_ids": ["anchor:one"],
        "provenance": "adapted",
    }
    operation = ReplaceLabOperation(
        kind="replace_lab", block_key="lab-1", lab_spec=payload
    )
    assert operation.model_dump(mode="json")["lab_spec"] == payload

    with pytest.raises(ValidationError, match="object"):
        ReplaceLabOperation(
            kind="replace_lab",
            block_key="lab-1",
            lab_spec=json.dumps(payload),
        )
