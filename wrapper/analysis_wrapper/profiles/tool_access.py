"""Executor-backed tool access for bundled capability providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..executor import SignalResult, run_tool
from ..identity import IdentityMap
from ..registry import tool_for
from ..targetspec import RepoTarget, TargetSpec


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
    ) -> SignalResult:
        recorded = self.targets.repo(target.repo_id)
        if recorded.path != target.path:
            raise ValueError("provider target does not match the recorded TargetSpec")
        # Providers name an explicitly reviewed definition; they never supply
        # argv, binaries, network flags, guards, or a constructed ToolDef.
        tooldef = tool_for(tool_id, recorded)
        return run_tool(
            tooldef,
            recorded,
            self.output_dir,
            self.scan_date,
            self.identities.repository(recorded.repo_id),
            signal_id=signal_id or None,
            allow_network=self.network_authorized,
        )
