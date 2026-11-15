import json

import tasks.MapTest.script_task as map_test
from tasks.GameUi.world.map_coordinate.calibration import MAPS_FILE
from tasks.GameUi.world.map_coordinate.recorder import RecordResult
from tasks.GameUi.world.map_coordinate.viewport import Viewport


def test_record_result_writes_map_name_and_timestamp_without_touching_maps_json(
        tmp_path, monkeypatch):
    monkeypatch.setattr(map_test, "RECORD_OUTPUT_DIR", str(tmp_path))
    result = RecordResult(
        map_name="沼泽",
        views={"top_left": [{"screen": [542, 208], "coord": [223, 61]}]},
        viewport=Viewport(161, 105, 756, 510, 1.0),
        plan={"top_left": [(542, 208)]},
    )

    task = object.__new__(map_test.ScriptTask)
    maps_before = MAPS_FILE.read_bytes()
    task.map_record_save(result)
    assert MAPS_FILE.read_bytes() == maps_before

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0].name.startswith("沼泽_")
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["map"] == "沼泽"
    assert payload["plan"] == {"top_left": [[542, 208]]}
    assert payload["maps_json"] == {
        "沼泽": {
            "views": {"top_left": [{"screen": [542, 208], "coord": [223, 61]}]},
        }
    }
    assert payload["views"] == {
        "top_left": [{"screen": [542, 208], "coord": [223, 61]}],
    }
    assert payload["viewport"] == [161, 105, 756, 510]
    assert payload["failed"] == []
    text = files[0].read_text(encoding="utf-8")
    assert '"screen": [542, 208]' in text
    assert '"coord": [223, 61]' in text
