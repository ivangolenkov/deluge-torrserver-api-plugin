"""Validate source-derived contract artifacts, NOT unimplemented HTTP handlers."""

import copy
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from jsonschema import Draft202012Validator, ValidationError

DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures/torrserver-emulation"
SCHEMA = json.loads((DIRECTORY / "schema.json").read_text())
MANIFEST = json.loads((DIRECTORY / "manifest.json").read_text())
EXAMPLES = json.loads((DIRECTORY / "examples.json").read_text())


def validator(name):
    return Draft202012Validator(
        {
            "$schema": SCHEMA["$schema"],
            "$defs": SCHEMA["$defs"],
            "$ref": f"#/$defs/{name}",
        }
    )


def status_example():
    return copy.deepcopy(EXAMPLES["cases"][0]["response"]["body"])


def test_schema_is_valid_and_provenance_is_not_mislabelled_as_capture():
    Draft202012Validator.check_schema(SCHEMA)
    origin = MANIFEST["provenance"]
    assert origin["kind"] == "source-derived"
    assert origin["runtime_verified"] is origin["client_captured"] is False
    assert re.fullmatch(r"[0-9a-f]{40}", origin["commit"])
    assert len(origin["files_sha256"]) >= 4
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in origin["files_sha256"].values()
    )
    assert EXAMPLES["kind"] == "source-derived-examples-not-captures"


@pytest.mark.parametrize("case", EXAMPLES["cases"], ids=lambda case: case["name"])
def test_examples_match_declared_source_dtos(case):
    request = case["request"]
    path = urlsplit(request["path"]).path
    assert any(
        request["method"] == route["method"]
        and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", route["path"]), path)
        for route in MANIFEST["routes"]
    )
    if "schema" in request:
        if case.get("request_valid", True):
            validator(request["schema"]).validate(request["body"])
        else:
            with pytest.raises(ValidationError):
                validator(request["schema"]).validate(request["body"])
    if "response" in case:
        response = case["response"]
        assert type(response["status"]) is int
        validator(response["schema"]).validate(response["body"])


def test_all_torrent_actions_are_represented_and_form_json_is_preserved():
    actions = {
        case["request"]["body"]["action"]
        for case in EXAMPLES["cases"]
        if case["request"]["path"] == "/torrents"
    }
    assert set(MANIFEST["torrents_actions"]) <= actions
    form_cases = [
        case
        for case in EXAMPLES["cases"]
        if case["request"]
        .get("headers", {})
        .get("Content-Type", "")
        .startswith("application/x-www-form-urlencoded")
    ]
    assert {case["request"]["path"] for case in form_cases} == {"/torrents", "/cache"}
    assert all(isinstance(case["request"]["body"], dict) for case in form_cases)


def test_data_is_text_not_a_nested_json_object():
    body = status_example()
    assert isinstance(json.loads(body["data"]), dict)
    body["data"] = {"id": "fixture"}
    with pytest.raises(ValidationError):
        validator("TorrentStatus").validate(body)


def test_legacy_stat_string_is_explicitly_not_a_final_torrserver_type():
    body = status_example()
    validator("TorrentStatus").validate(body)
    body["stat"] = 3
    with pytest.raises(ValidationError):
        validator("TorrentStatus").validate(body)
    assert any(
        "stat is an internal string" in text for text in MANIFEST["do_not_copy_blindly"]
    )


def test_cache_capitalization_and_empty_fallback():
    body = {
        "Hash": "fixture",
        "Capacity": 20971520,
        "Filled": 0,
        "PiecesLength": 0,
        "PiecesCount": 0,
        "Torrent": status_example(),
        "Pieces": {},
        "Readers": [],
    }
    validator("CacheResponse").validate(body)
    validator("CacheResponse").validate({})
    with pytest.raises(ValidationError):
        validator("CacheResponse").validate({"hash": "fixture", "filled": 0})


@pytest.mark.parametrize("mutation", ["file_id_zero", "size_text", "missing_hash"])
def test_invalid_wire_types_are_not_accepted(mutation):
    body = status_example()
    if mutation == "file_id_zero":
        body["file_stats"][0]["id"] = 0
    elif mutation == "size_text":
        body["size"] = "2048"
    else:
        del body["hash"]
    with pytest.raises(ValidationError):
        validator("TorrentStatus").validate(body)
