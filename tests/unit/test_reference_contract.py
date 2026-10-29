"""Offline regression of actual captures, not re-execution of clients/server."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tools.capture_reference import HEADERS, headers, summarize

ROOT = Path(__file__).resolve().parents[1] / "fixtures/torrserver-reference"
CAPTURE = json.loads((ROOT / "matrix147.1-lampa3.4.4.json").read_text())
SCHEMA = json.loads((ROOT / "schema.json").read_text())
CASES = CAPTURE["cases"]


def case(name):
    return next(value for value in reversed(CASES) if value["name"] == name)


def value(name):
    return case(name)["response"]["body"]["value"]


def validate(name, body):
    Draft202012Validator(
        {"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"}
    ).validate(body)


def test_provenance_does_not_claim_num_plugin_or_player_acceptance():
    origin = CAPTURE["provenance"]
    assert origin["kind"] == "reference-runtime-capture"
    assert origin["reference"] == "MatriX.147.1"
    assert origin["lampa"]["version"] == "3.4.4"
    assert origin["client_captured"] is True
    assert origin["num_verified"] is origin["plugin_verified"] is False
    assert origin["full_ui_playback_verified"] is False
    assert origin["fixture"]["media_decodable"] is False
    assert origin["fixture"]["public_swarm"] is False
    assert {c["requester"] for c in CASES} == {
        "scripted-reference-probe",
        "lampa-module-browser",
        "browser-scripted-fetch",
    }
    assert case("browser-seek")["requester"] == "browser-scripted-fetch"
    assert case("lampa-add")["requester"] == "lampa-module-browser"


@pytest.mark.parametrize("entry", CASES, ids=lambda entry: entry["name"])
def test_captured_dtos_and_safe_headers(entry):
    Draft202012Validator.check_schema(SCHEMA)
    request, response = entry["request"], entry["response"]
    assert set(request["headers"]) <= HEADERS
    assert set(response["headers"]) <= HEADERS
    if request["path"] == "/viewed":
        validate("ViewedRequest", request["body"]["value"])
        if request["body"]["value"]["action"] == "list":
            validate("ViewedList", response["body"]["value"])
        else:
            assert response["body"] == {"kind": "empty", "bytes": 0}
    body = response["body"]
    if body["kind"] == "json" and isinstance(body["value"], dict):
        if "stat" in body["value"]:
            validate("Torrent", body["value"])
        if request["path"] == "/cache":
            validate("Cache", body["value"])


def test_real_stat_is_numeric_and_file_size_is_not_required():
    status = value("get-files")
    assert type(status["stat"]) is int
    assert status["file_stats"] == [{"id": 1, "path": "fixture.mp4", "length": 4194304}]
    status = copy.deepcopy(status)
    status["stat"] = "Downloading"
    with pytest.raises(ValidationError):
        validate("Torrent", status)


def test_viewed_roundtrip_restart_and_actual_empty_map_quirk():
    # Earlier stream requests can already create a viewed mark in this release.
    validate("ViewedList", value("viewed-before-set"))
    assert value("viewed-list") == value("viewed-after-restart")
    assert value("viewed-list")[0]["file_index"] == 1
    assert value("settings-get")["TrackTimecode"] is False
    assert value("viewed-list")[0]["timecode"] == 0
    assert value("viewed-after-rem") is None
    assert value("lampa-viewed")[0]["file_index"] == 1


@pytest.mark.parametrize(
    "name,start,end",
    [
        ("range-first", 0, 1),
        ("range-seek", 65530, 65551),
        ("range-suffix", 4194304 - 17, 4194304),
        ("browser-seek", 65530, 65551),
    ],
)
def test_captured_ranges_match_local_payload(name, start, end):
    response = case(name)["response"]
    expected = bytes(i % 256 for i in range(start, end))
    assert response["status"] == 206
    assert response["body"]["bytes"] == len(expected)
    assert response["body"]["sha256"] == hashlib.sha256(expected).hexdigest()
    assert response["headers"]["content-range"] == f"bytes {start}-{end - 1}/4194304"


def test_wire_body_and_error_differences_are_not_lost():
    assert value("echo") == "MatriX.147.1"
    assert case("preload")["response"]["body"] == {"kind": "empty", "bytes": 0}
    assert case("get-missing")["response"]["status"] == 404
    assert case("get-missing")["response"]["body"]["kind"] == "empty"
    assert case("unknown-action")["response"]["status"] == 400
    assert case("lampa-drop")["response"]["body"]["kind"] == "empty"
    assert value("lampa-add")["data"] == '{"fixture":true}'


def test_recorder_omits_credentials_and_summarizes_without_payload_retention():
    assert headers(
        [
            ("Authorization", "secret"),
            ("Cookie", "secret"),
            ("Content-Type", "application/json"),
        ]
    ) == {
        "content-type": "application/json",
    }
    assert summarize(b"null", "application/json") == {"kind": "json", "value": None}
    assert summarize(b"", "application/json") == {"kind": "empty", "bytes": 0}
    binary = summarize(b"abc", "application/octet-stream")
    assert binary["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert "value" not in binary
