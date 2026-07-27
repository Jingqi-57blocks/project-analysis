"""Packet composer tests (57B-113 / 57B-115, M1): budget fit, deterministic
shard boundaries (JSON-array and line-oriented text), and redaction."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # wrapper/ on path

from analysis_wrapper.orchestrator.composer import (
    MAX_SHARD_COUNT, ComposerError, compose, estimate_tokens,
)


def _compose(**overrides):
    kwargs = dict(
        task_id="t1", template_id="tpl", template_version="1.0.0",
        task_type="lens-findings", instructions="short instructions",
        inputs={"a": "small content"}, output_schema_id="lens-findings.v1",
        context_budget_tokens=1000,
    )
    kwargs.update(overrides)
    return compose(**kwargs)


def test_estimate_tokens_is_the_documented_chars_over_four_heuristic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2  # ceil(5/4)


def test_a_packet_within_budget_is_not_sharded():
    packets = _compose()
    assert len(packets) == 1
    assert packets[0].task_id == "t1"
    assert packets[0].inputs["a"].content == "small content"


def test_json_array_input_is_split_into_contiguous_shards_that_still_parse():
    items = [{"id": i, "text": "x" * 50} for i in range(40)]
    big_json = json.dumps(items)
    packets = _compose(inputs={"candidates": big_json}, context_budget_tokens=300)
    assert len(packets) >= 2
    assert [p.task_id for p in packets] == [f"t1-shard-{i + 1}" for i in range(len(packets))]

    # Shards are contiguous and cover every element exactly once.
    reassembled: list = []
    for index, packet in enumerate(packets, start=1):
        chunk = json.loads(packet.inputs["candidates"].content)
        reassembled.extend(chunk)
        assert packet.inputs["sharding"].content == (
            f"shard {index} of {len(packets)}, split on candidates")
    assert reassembled == items

    # Every shard fits the budget.
    for packet in packets:
        total = estimate_tokens(packet.instructions) + sum(
            estimate_tokens(item.content) for item in packet.inputs.values())
        assert total <= 300


def test_line_oriented_text_input_is_split_into_contiguous_line_shards():
    lines = [f"line {i} " + "y" * 40 for i in range(30)]
    text = "\n".join(lines)
    packets = _compose(inputs={"signals": text}, context_budget_tokens=250)
    assert len(packets) >= 2

    reassembled_lines: list[str] = []
    for index, packet in enumerate(packets, start=1):
        reassembled_lines.extend(packet.inputs["signals"].content.splitlines())
        assert packet.inputs["sharding"].content == (
            f"shard {index} of {len(packets)}, split on signals")
    assert reassembled_lines == lines


def test_unsplittable_oversized_input_fails_closed():
    with pytest.raises(ComposerError, match="cannot be split deterministically"):
        _compose(inputs={"blob": "x" * 5000}, context_budget_tokens=100)


def test_no_inputs_to_shard_fails_closed():
    with pytest.raises(ComposerError, match="no inputs to shard"):
        _compose(instructions="x" * 5000, inputs={}, context_budget_tokens=100)


def test_fixed_cost_alone_exceeding_budget_fails_closed_even_with_a_shardable_input():
    # "candidates" must be the LARGEST input (so it's the one picked for
    # sharding) yet the instructions + the smaller "other" input alone
    # already meet the budget -- sharding candidates can never help.
    items = [{"id": i, "text": "z" * 20} for i in range(100)]
    with pytest.raises(ComposerError, match="non-sharded inputs alone"):
        _compose(instructions="x" * 400,
                inputs={"other": "y" * 400, "candidates": json.dumps(items)},
                context_budget_tokens=150)


def test_pathological_shard_count_fails_closed_instead_of_exploding():
    """The exact live-run hazard: a fixed cost close to the budget leaves
    only a sliver per shard, which for a large splittable input computes
    out to hundreds of shards (a real run hit 428) instead of failing
    closed. "other" leaves only ~100 tokens/shard against a "candidates"
    array whose per-element size forces a shard count far past
    MAX_SHARD_COUNT."""
    items = [{"id": i, "text": "z" * 96} for i in range(200)]  # ~25 tokens/item
    with pytest.raises(ComposerError, match=f"exceeds the sane shard-count bound "
                                            f"\\({MAX_SHARD_COUNT}\\)"):
        _compose(instructions="x" * 4,
                inputs={"other": "y" * 3600, "candidates": json.dumps(items)},
                context_budget_tokens=1000)


def test_shard_count_at_or_under_the_bound_still_composes_normally():
    # A much larger budget than the pathological case above -> a small,
    # reasonable shard count, well under MAX_SHARD_COUNT -- the new guard
    # must never reject an ordinary sharding scenario.
    items = [{"id": i, "text": "z" * 20} for i in range(60)]
    packets = _compose(inputs={"candidates": json.dumps(items)}, context_budget_tokens=250)
    assert 2 <= len(packets) <= MAX_SHARD_COUNT


def test_redaction_is_applied_to_every_input_before_packet_assembly():
    packets = _compose(inputs={"secret": "DB_PASSWORD=hunter2 lives here"})
    assert "hunter2" not in packets[0].inputs["secret"].content
    assert "<REDACTED>" in packets[0].inputs["secret"].content


def test_redaction_is_also_applied_to_each_shard():
    # A "filler" field keeps each item large even AFTER the secret itself
    # collapses down to "<REDACTED>" -- otherwise redaction alone could
    # shrink the whole input back under budget and no shard would ever exist
    # to check.
    items = [{"id": i, "secret": "AWS_SECRET_ACCESS_KEY=topsecretvalue" + "x" * 30,
             "filler": "z" * 80} for i in range(60)]
    packets = _compose(inputs={"candidates": json.dumps(items)}, context_budget_tokens=250)
    assert len(packets) >= 2
    for packet in packets:
        assert "topsecretvalue" not in packet.inputs["candidates"].content
