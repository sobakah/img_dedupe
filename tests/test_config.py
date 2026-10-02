"""Loading config.json: defaults, merging, and rejecting invalid values."""

import json

import pytest

from scripts.config import DEFAULT_CONFIG, load_config


def load(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return load_config(path)


def test_no_file_gives_the_defaults(tmp_path):
    config, warnings = load_config(tmp_path / "missing.json")
    assert warnings == []
    assert {k: config[k] for k in DEFAULT_CONFIG} == DEFAULT_CONFIG


def test_uncertain_ratio_defaults_to_0_8():
    assert DEFAULT_CONFIG["uncertain_ratio"] == 0.8


def test_a_partial_file_keeps_the_other_defaults(tmp_path):
    config, warnings = load(tmp_path, {"viewer": "imagecompare"})
    assert warnings == []
    assert config["viewer"] == "imagecompare"
    assert config["delete_mode"] == "trash"


def test_format_ranks_are_merged_not_replaced(tmp_path):
    config, _ = load(tmp_path, {"format_ranks": {"PNG": 9}})
    assert config["format_ranks"]["PNG"] == 9
    assert config["format_ranks"]["JPEG"] == DEFAULT_CONFIG["format_ranks"]["JPEG"]


def test_invalid_choice_warns_and_keeps_the_default(tmp_path):
    config, warnings = load(tmp_path, {"viewer": "bogus"})
    assert config["viewer"] == "auto"
    assert any("viewer" in w for w in warnings)


def test_unknown_and_old_keys_are_reported(tmp_path):
    _, warnings = load(tmp_path, {"delete_mod": "trash", "threshold": 90})
    assert any("delete_mod" in w for w in warnings)
    assert any("threshold" in w for w in warnings)


@pytest.mark.parametrize("key, value", [
    ("compare_size", "big"), ("compare_size", 10), ("compare_size", 512.5),
    ("uncertain_ratio", 3), ("uncertain_ratio", "0.8"), ("hash_size", True),
    ("max_pixel_diff", -1), ("max_aspect_diff", 2),
])
def test_invalid_numbers_warn_and_use_the_default(tmp_path, key, value):
    config, warnings = load(tmp_path, {key: value})
    assert config[key] == DEFAULT_CONFIG[key]
    assert any(key in w for w in warnings)


@pytest.mark.parametrize("key, value", [
    ("compare_size", 768), ("uncertain_ratio", 0.75), ("uncertain_ratio", 1),
    ("max_pixel_diff", 16), ("max_pixel_diff", None), ("hash_max_distance", 36),
])
def test_valid_numbers_are_accepted(tmp_path, key, value):
    config, warnings = load(tmp_path, {key: value})
    assert warnings == []
    assert config[key] == value


def test_hash_distance_is_limited_by_the_hash_size(tmp_path):
    config, warnings = load(tmp_path, {"hash_size": 4, "hash_max_distance": 99})
    assert config["hash_max_distance"] <= 2 * 4 * 4
    assert any("hash_max_distance" in w for w in warnings)


def test_broken_json_warns_and_uses_the_defaults(tmp_path):
    config, warnings = load(tmp_path, "{ not json")
    assert config["delete_mode"] == "trash"
    assert warnings


def test_prefer_jxl_must_be_true_or_false(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"prefer_jxl": "yes"}')
    config, warnings = load_config(path)
    assert config["prefer_jxl"] is False
    assert any("'prefer_jxl' must be true or false" in w for w in warnings)
