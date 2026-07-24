"""Executor-backed tool access for bundled capability providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..executor import SignalResult, run_tool
from ..identity import IdentityMap
from ..registry import tool_for
from ..targetspec import RepoTarget, TargetSpec
from ..tooldefs import ToolDef


@dataclass(frozen=True)
class ExecutorToolAccess:
    """Narrow adapter that preserves every existing executor safety check."""

    targets: TargetSpec
    identities: IdentityMap
    output_dir: Path
    scan_date: str
    network_authorized: bool = False

    def execute(
        self,
        tool_id: str,
        target: RepoTarget,
        *,
        signal_id: str = "",
        tooldef: ToolDef | None = None,
    ) -> SignalResult:
        recorded = self.targets.repo(target.repo_id)
        if recorded.path != target.path:
            raise ValueError("provider target does not match the recorded TargetSpec")
        # ``tooldef`` (57B-82 A2), when given, MUST still be that same named
        # tool's own definition — never a mismatched swap — and exists only
        # for run-bound construction (e.g. git-history's since/cap) that the
        # default ``tool_for`` resolution below has no way to supply. This
        # name check is the ONLY validation performed here: it does not
        # verify the definition came from a registry.py constructor, nor
        # inspect argv/binary/network — see ToolAccess.execute's own
        # docstring (contracts.py) for the full trust-boundary explanation.
        if tooldef is not None:
            if tooldef.name != tool_id:
                raise ValueError(
                    f"tooldef.name {tooldef.name!r} does not match requested "
                    f"tool_id {tool_id!r}"
                )
            resolved = tooldef
        else:
            resolved = tool_for(tool_id, recorded)
        return run_tool(
            resolved,
            recorded,
            self.output_dir,
            self.scan_date,
            self.identities.repository(recorded.repo_id),
            signal_id=signal_id or None,
            allow_network=self.network_authorized,
        )
