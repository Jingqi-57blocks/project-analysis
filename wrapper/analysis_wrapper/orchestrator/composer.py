"""Packet composer for the orchestrator (57B-113 / 57B-115, M1).

Turns a caller's (template, instructions, named inputs) into one or more
:class:`~.contracts.TaskPacket` objects, applying two policies before a
packet is ever built:

  - **Redaction first.** Every input's content passes through the wrapper's
    existing ``sanitize_text`` (the same treatment every other artifact
    writer gives raw evidence before it enters model context or persists to
    disk) BEFORE it is measured or embedded in a packet.
  - **Deterministic sharding.** Token cost is estimated with a documented
    chars/4 heuristic (NOT a real tokenizer). When instructions + inputs
    exceed ``context_budget_tokens``, the single LARGEST input is split into
    contiguous shards (a JSON array split by element, or line-oriented text
    split by line) and each shard becomes its own ``<task_id>-shard-<i>``
    packet carrying a ``sharding`` note input identifying its slice — so a
    downstream consumer can always tell it is looking at a partial view. A
    packet that cannot be made to fit (no inputs to shard, or the largest
    input is neither a JSON array nor line-oriented text, or the computed
    shard count exceeds :data:`MAX_SHARD_COUNT` -- a sign the budget is far
    too small for this task's fixed cost, not that the largest input needs
    slicing thinner) fails closed with :class:`ComposerError` rather than
    silently truncating anything or producing hundreds of shard packets.
"""

from __future__ import annotations

import json
import math
from typing import Mapping, Sequence

from ..sanitize import sanitize_text
from .contracts import TaskPacket

CHARS_PER_TOKEN = 4  # a documented ESTIMATE, not a real tokenizer count

# A sane upper bound on how many shards one oversized input may be split
# into. Past this point the real problem is not "the largest input needs
# slicing thinner" -- it is that context_budget_tokens is far too small for
# this task's FIXED cost (instructions + every non-sharded input). A live
# run hit this exactly: plan-lens-finalize's default 96k budget against a
# ~95.9k fixed cost left ~100 tokens per shard, which for a large array/view
# computed out to 428 shard packets (an estimated 39M tokens total) instead
# of failing closed -- the same "fail closed rather than silently produce
# something absurd" spirit as this module's other ComposerError cases.
MAX_SHARD_COUNT = 32


class ComposerError(ValueError):
    """A packet cannot be composed within its context budget."""


def estimate_tokens(text: str) -> int:
    """chars/4 heuristic — see the module docstring; not a real tokenizer."""
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def _sharding_note(index: int, total: int, input_name: str) -> str:
    return f"shard {index} of {total}, split on {input_name}"


def _as_json_list(content: str) -> list | None:
    try:
        value = json.loads(content)
    except ValueError:
        return None
    return value if isinstance(value, list) else None


def _split_json_list(items: list, shards: int) -> list[str]:
    total = len(items)
    chunks: list[str] = []
    start = 0
    for index in range(shards):
        size = total // shards + (1 if index < total % shards else 0)
        chunks.append(json.dumps(items[start:start + size], sort_keys=True, ensure_ascii=False))
        start += size
    return chunks


def _split_lines(content: str, shards: int) -> list[str]:
    lines = content.splitlines()
    total = len(lines)
    chunks: list[str] = []
    start = 0
    for index in range(shards):
        size = total // shards + (1 if index < total % shards else 0)
        chunks.append("\n".join(lines[start:start + size]))
        start += size
    return chunks


def compose(*, task_id: str, template_id: str, template_version: str, task_type: str,
           instructions: str, inputs: Mapping[str, str], output_schema_id: str,
           context_budget_tokens: int, depends_on: Sequence[str] = ()) -> list[TaskPacket]:
    """Build one packet, or (when it would not fit) a deterministic set of
    ``<task_id>-shard-1..K`` packets. Always returns a non-empty list."""
    clean_inputs = {name: sanitize_text(content) for name, content in inputs.items()}
    depends_on = tuple(depends_on)

    def total_tokens(candidate_inputs: Mapping[str, str]) -> int:
        return (estimate_tokens(instructions)
                + sum(estimate_tokens(value) for value in candidate_inputs.values()))

    if total_tokens(clean_inputs) <= context_budget_tokens:
        return [TaskPacket.create(
            task_id=task_id, task_type=task_type, template_id=template_id,
            template_version=template_version, instructions=instructions,
            inputs=clean_inputs, output_schema_id=output_schema_id,
            context_budget_tokens=context_budget_tokens, depends_on=depends_on)]

    if not clean_inputs:
        raise ComposerError(
            f"task {task_id!r}: instructions alone ({estimate_tokens(instructions)} "
            f"estimated tokens) exceed the context budget ({context_budget_tokens}) "
            "and there are no inputs to shard")

    largest_name = max(clean_inputs, key=lambda name: estimate_tokens(clean_inputs[name]))
    largest_content = clean_inputs[largest_name]
    json_list = _as_json_list(largest_content)
    is_line_oriented = "\n" in largest_content.strip()
    if json_list is None and not is_line_oriented:
        raise ComposerError(
            f"task {task_id!r}: input {largest_name!r} is the largest input but is "
            "neither a JSON array nor line-oriented text -- cannot be split "
            "deterministically; reduce its size or raise context_budget_tokens")

    splittable_units = len(json_list) if json_list is not None else len(largest_content.splitlines())
    if splittable_units < 2:
        raise ComposerError(
            f"task {task_id!r}: input {largest_name!r} has only {splittable_units} "
            "splittable unit(s) -- cannot be sharded further")

    other_inputs = {name: value for name, value in clean_inputs.items() if name != largest_name}
    fixed_cost = estimate_tokens(instructions) + sum(
        estimate_tokens(value) for value in other_inputs.values())
    if fixed_cost >= context_budget_tokens:
        raise ComposerError(
            f"task {task_id!r}: instructions + non-sharded inputs alone "
            f"({fixed_cost} estimated tokens) already meet or exceed the context "
            f"budget ({context_budget_tokens}) -- sharding the largest input cannot help")

    remaining_for_shard = context_budget_tokens - fixed_cost
    largest_tokens = estimate_tokens(largest_content)
    shard_count = min(max(2, math.ceil(largest_tokens / remaining_for_shard)), splittable_units)

    def build(shard_count: int) -> list[TaskPacket] | None:
        chunks = (_split_json_list(json_list, shard_count) if json_list is not None
                 else _split_lines(largest_content, shard_count))
        packets: list[TaskPacket] = []
        for index, chunk in enumerate(chunks, start=1):
            shard_inputs = dict(other_inputs)
            shard_inputs[largest_name] = chunk
            shard_inputs["sharding"] = _sharding_note(index, shard_count, largest_name)
            if total_tokens(shard_inputs) > context_budget_tokens:
                return None
            packets.append(TaskPacket.create(
                task_id=f"{task_id}-shard-{index}", task_type=task_type,
                template_id=template_id, template_version=template_version,
                instructions=instructions, inputs=shard_inputs,
                output_schema_id=output_schema_id,
                context_budget_tokens=context_budget_tokens, depends_on=depends_on))
        return packets

    while shard_count <= splittable_units:
        if shard_count > MAX_SHARD_COUNT:
            raise ComposerError(
                f"task {task_id!r}: sharding {largest_name!r} to fit within the context "
                f"budget would need {shard_count} shard(s) -- fixed cost (instructions + "
                f"non-sharded inputs) is {fixed_cost} of {context_budget_tokens} estimated "
                f"tokens, leaving only {remaining_for_shard} per shard for {largest_name!r} "
                f"({largest_tokens} estimated tokens); exceeds the sane shard-count bound "
                f"({MAX_SHARD_COUNT}) -- raise context_budget_tokens or reduce this task's "
                "fixed-cost inputs instead of sharding around it")
        packets = build(shard_count)
        if packets is not None:
            return packets
        shard_count += 1
    raise ComposerError(
        f"task {task_id!r}: input {largest_name!r} cannot be sharded to fit within "
        f"the context budget ({context_budget_tokens}) even at {splittable_units} shard(s)")
