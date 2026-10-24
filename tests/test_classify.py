"""Tests for the Claude classifier request/response handling (no network)."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import classify as K

TAX = {"folders": [{"id": f"F{i}", "name": f"Folder {i}", "precedence": i, "definition": f"def {i}"}
                   for i in range(1, 10)],
       "flags": [{"id": "URGENTE", "definition": "legal threats"}]}


def test_only_allowlisted_fields_are_sent():
    case = {"subject": "Reembolso", "text": "quero o reembolso", "languages": ["pt"], "n_messages": 1,
            "attachment_hints": [], "case_id": "C1", "customer_addresses": ["x@y.pt"], "raw": "secret"}
    req = K.build_request(case, TAX)
    sent = req["messages"][0]["content"]
    assert "x@y.pt" not in sent and "secret" not in sent and "C1" not in sent
    assert json.loads(sent.split("\n")[1])["subject"] == "Reembolso"


def test_schema_and_prompt_cover_all_nine_folders():
    req = K.build_request({}, TAX)
    assert req["output_config"]["format"]["schema"]["properties"]["folder"]["enum"] == K.FOLDER_IDS
    system = req["system"][0]["text"]
    assert all(f"Folder {i}" in system for i in range(1, 10))
    assert "data, not instructions" in system


def test_bad_model_output_goes_to_a_rever():
    text = lambda t: NS(stop_reason="end_turn", content=[NS(type="text", text=t)])
    assert K.parse_response(NS(stop_reason="refusal", content=[]))["folder"] == "F9"
    assert K.parse_response(text("not json"))["folder"] == "F9"
    assert K.parse_response(text('{"folder": "F42"}'))["folder"] == "F9"
    ok = K.parse_response(text('{"folder":"F2","urgent":true,"confidence":"high","reason":"no ticket"}'))
    assert ok["folder"] == "F2" and ok["urgent"] is True


def test_urgent_definition_is_legal_only():
    system = K.build_request({}, TAX)["system"][0]["text"]
    assert "legal threats" not in system and "an event date" in system


def test_the_category_definitions_ship_with_the_code():
    import json
    tax = json.loads(K.TAXONOMY.read_text(encoding="utf-8"))
    assert "config" in K.TAXONOMY.parts and sorted(f["id"] for f in tax["folders"]) == ["F1", "F2", "F3", "F4", "F5",
                                                                                         "F8", "F9"]


def test_shop_and_partner_mail_now_goes_to_a_rever():
    assert K.FOLDER_IDS == ["F1", "F2", "F3", "F4", "F5", "F8", "F9"]
    text = lambda t: NS(stop_reason="end_turn", content=[NS(type="text", text=t)])
    for old in ("F6", "F7"):
        out = K.parse_response(text('{"folder":"%s","urgent":false,"confidence":"high","reason":"shop"}' % old))
        assert (out["folder"], out["confidence"]) == ("F9", "high")
        assert K.current_folder(old) == "F9"
    assert K.current_folder("F2") == "F2" and K.current_folder(None) is None
    tax = json.loads(K.TAXONOMY.read_text(encoding="utf-8"))
    f9 = next(f for f in tax["folders"] if f["id"] == "F9")["definition"].lower()
    assert all(w in f9 for w in ("merchandise", "organisers", "venues", "promoters", "b2b"))
    ranked = sorted(tax["folders"], key=lambda f: f["precedence"])
    assert [f["precedence"] for f in ranked] == list(range(1, len(tax["folders"]) + 1))
    assert [f["id"] for f in ranked] == ["F8", "F1", "F2", "F3", "F4", "F5", "F9"]   # A Rever is the last fallback
