"""Cumulative BQ scan-budget tracker with soft cap + append-only JSONL log.

Usage:

    from scripts.bq_budget import BQBudget

    budget = BQBudget.for_owner(
        state_dir=Path("docs/overnight/2026-04-17/state"),
        owner="track_c",
        cap_tb=1.0,
    )

    # Before every BQ call, dry-run first and check:
    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    dry = bq.query(SQL, job_config=job_config)
    budget.check_before(next_scan_bytes=dry.total_bytes_processed)

    # Then run for real and record:
    df = bq.query(SQL).to_dataframe()
    budget.record_scan(bytes_scanned=dry.total_bytes_processed, query_ref="my_scan.py")

The log format (one JSON object per line, append-only):
    {"ts": "...", "owner": "track_c", "bytes_scanned": N,
     "query_ref": "scan_funnel_leak.py", "note": "...",
     "cumulative_tb_after": 0.487}
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BYTES_PER_TB = 1024 ** 4
SAFETY_MARGIN_TB = 0.1  # abort if next scan would push within this of cap


class BudgetExceeded(Exception):
    """Raised when a projected scan would exceed the soft cap."""
    pass


@dataclass
class BQBudget:
    log_path: Path
    cap_tb: float
    owner: str  # "track_b", "track_c", "phase_0", "review", "consolidation"

    def __post_init__(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()

    def cumulative_bytes(self) -> int:
        """Sum bytes_scanned for this owner across the log history."""
        if not self.log_path.exists():
            return 0
        total = 0
        for line in self.log_path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("owner") == self.owner:
                total += entry.get("bytes_scanned", 0)
        return total

    def cumulative_tb(self) -> float:
        return self.cumulative_bytes() / BYTES_PER_TB

    def remaining_tb(self) -> float:
        return max(0.0, self.cap_tb - self.cumulative_tb())

    def check_before(self, next_scan_bytes: int) -> None:
        """Raises BudgetExceeded if the next scan would cross the soft cap."""
        projected_tb = (self.cumulative_bytes() + next_scan_bytes) / BYTES_PER_TB
        soft_cap = self.cap_tb - SAFETY_MARGIN_TB
        if projected_tb > soft_cap:
            raise BudgetExceeded(
                f"{self.owner}: next scan ({next_scan_bytes / BYTES_PER_TB:.3f} TB) "
                f"would push cumulative to {projected_tb:.3f} TB, exceeding "
                f"soft cap {soft_cap:.3f} TB (hard cap {self.cap_tb} TB - "
                f"{SAFETY_MARGIN_TB} TB safety margin). "
                f"Current cumulative: {self.cumulative_tb():.3f} TB."
            )

    def record_scan(
        self, *,
        bytes_scanned: int,
        query_ref: str,
        note: str = "",
    ) -> None:
        """Append a scan record to the log."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "owner": self.owner,
            "bytes_scanned": bytes_scanned,
            "query_ref": query_ref,
            "note": note,
            "cumulative_tb_after": (self.cumulative_bytes() + bytes_scanned) / BYTES_PER_TB,
        }
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    @classmethod
    def for_owner(cls, state_dir: Path, owner: str, cap_tb: float) -> "BQBudget":
        """Convenience constructor. Log file is `<state_dir>/budget.jsonl`."""
        return cls(log_path=state_dir / "budget.jsonl", cap_tb=cap_tb, owner=owner)


def summarize_log(log_path: Path) -> dict:
    """Aggregate stats across all owners. Used by morning_summary writer."""
    if not log_path.exists():
        return {"owners": {}, "total_tb": 0.0, "n_scans": 0}
    by_owner: dict[str, dict] = {}
    total_bytes = 0
    n_scans = 0
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        owner = entry.get("owner", "unknown")
        if owner not in by_owner:
            by_owner[owner] = {"bytes": 0, "n_scans": 0}
        by_owner[owner]["bytes"] += entry.get("bytes_scanned", 0)
        by_owner[owner]["n_scans"] += 1
        total_bytes += entry.get("bytes_scanned", 0)
        n_scans += 1
    for owner, stats in by_owner.items():
        stats["tb"] = stats["bytes"] / BYTES_PER_TB
    return {
        "owners": by_owner,
        "total_tb": total_bytes / BYTES_PER_TB,
        "n_scans": n_scans,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(json.dumps(summarize_log(Path(sys.argv[1])), indent=2))
    else:
        print("Usage: python bq_budget.py <path-to-budget.jsonl>")
        sys.exit(1)
