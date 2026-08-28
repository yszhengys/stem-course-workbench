from typing import Any, cast

from open_notebook.course.v2_contracts import ReplaceLabOperation


def test_frozen_lab_preserves_the_discriminated_object_wire_schema() -> None:
    schema = ReplaceLabOperation.model_json_schema()
    definitions = cast(dict[str, dict[str, Any]], schema["$defs"])
    lab_schema = definitions["FrozenLabSpec"]

    assert lab_schema.get("type") != "string"
    assert lab_schema["discriminator"]["propertyName"] == "kind"
    assert "mapping" not in lab_schema["discriminator"]
    assert len(lab_schema["oneOf"]) == 5
