import json

from bench.actions import ACTIONS, FEW_SHOT, build_messages, build_schema, build_system_prompt
from bench.catalog import load_catalog
from bench.stt import wer


def test_schema_has_one_variant_per_action():
    variants = build_schema()["anyOf"]
    assert [v["properties"]["action"]["const"] for v in variants] == [a.name for a in ACTIONS]


def test_schema_params_are_required_but_nullable():
    for variant in build_schema()["anyOf"]:
        params = variant["properties"]["params"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])
        for spec in params["properties"].values():
            assert {"type": "null"} in spec["anyOf"]


def test_prompt_mentions_every_action():
    prompt = build_system_prompt()
    for action in ACTIONS:
        assert action.name in prompt


def test_few_shot_examples_are_not_in_the_catalog():
    catalog_texts = {c.text.lower() for c in load_catalog()}
    for text, _answer in FEW_SHOT:
        assert text.lower() not in catalog_texts, text


def test_messages_shape():
    zero = build_messages("cpu", few_shot=False)
    few = build_messages("cpu", few_shot=True)
    assert [m["role"] for m in zero] == ["system", "user"]
    assert len(few) == 2 + 2 * len(FEW_SHOT)
    json.loads(few[2]["content"])  # assistant turns are valid JSON


def test_wer():
    assert wer("open chrome", "open chrome") == 0
    assert wer("open chrome", "Open Chrome.") == 0
    assert wer("open chrome now", "open chrome") == 1 / 3
    assert wer("close notepad", "close note pad") == 1.0
