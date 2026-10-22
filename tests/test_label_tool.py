"""Tests for the offline labelling page generator."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import label_tool as LT


def test_embedded_json_cannot_close_the_script_tag():
    out = LT.embed_json({"text": "a</script><script>alert(1)</script>"})
    assert "</script>" not in out


def test_pick_excludes_content_free_and_is_deterministic():
    cases = [{"case_id": f"C{i:03d}", "content_free": i % 5 == 0} for i in range(100)]
    a = LT.pick(cases, 30, 7)
    assert len(a) == 30 and not any(c["content_free"] for c in a)
    assert [c["case_id"] for c in a] == [c["case_id"] for c in LT.pick(cases, 30, 7)]
