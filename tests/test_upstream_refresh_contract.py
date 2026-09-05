"""Source-controlled contract for the manual P5.72 upstream refresh cadence."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_roadmap_cadence_and_current_refresh_are_linked() -> None:
    roadmap = (DOCS / "ROADMAP.md").read_text()
    cadence = (DOCS / "UPSTREAM-RESEARCH-CADENCE.md").read_text()
    normalized_cadence = " ".join(cadence.split())
    sync = (DOCS / "UPSTREAM-SYNC.md").read_text()
    refresh = DOCS / "UPSTREAM-REFRESH-2026-08-30.md"

    assert refresh.is_file()
    for document in (roadmap, sync):
        assert "UPSTREAM-RESEARCH-CADENCE.md" in document
        assert "UPSTREAM-REFRESH-2026-08-30.md" in document

    assert "one review every 30 calendar days" in normalized_cadence
    assert "limited to 90 minutes" in normalized_cadence
    assert "may not be deferred beyond 45 calendar days" in normalized_cadence
    assert "scheduled workflow" in normalized_cadence
    assert "background automation" in normalized_cadence


def test_upstream_cadence_contains_required_safety_and_ledger_contract() -> None:
    cadence = (DOCS / "UPSTREAM-RESEARCH-CADENCE.md").read_text()

    for required_source in (
        "original [SidePulse repository]",
        "Relevant forks",
        "[CodexBar]",
        "[T3 Code]",
        "JR-Bar checkout",
    ):
        assert required_source in cadence

    for safety_statement in (
        "untrusted reports and data",
        "must not merge, push, release, deploy",
        "change credentials",
        "mutate hardware/system state",
        "STALE — refresh required",
    ):
        assert safety_statement in cadence

    for field in (
        "source_url",
        "source_ref_or_release",
        "checked_at",
        "reachability",
        "disposition",
        "evidence_kind",
        "safety_privacy_notes",
        "next_action",
    ):
        assert f"`{field}`" in cadence

    assert "`adopt`" in cadence
    assert "`adapt`" in cadence
    assert "`adopted/surpassed`" in cadence
    assert "`waiting on evidence`" in cadence
    assert "`reject`" in cadence
