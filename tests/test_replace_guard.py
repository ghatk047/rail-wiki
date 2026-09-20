"""Tests for the only-replace-if-better guard.

A --force re-run is three fresh drafts with no memory of what is published, so
without this guard a re-roll of a thin diagram is as likely to make it worse.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generate_rail_wiki import (  # noqa: E402
    challenger_wins, incumbent_quality, diagram_richness,
)


def test_higher_score_wins():
    assert challenger_wins((4, 20), (3, 25)) is True


def test_lower_score_loses_even_with_more_nodes():
    assert challenger_wins((2, 40), (3, 18)) is False


def test_equal_score_more_nodes_wins():
    assert challenger_wins((3, 24), (3, 18)) is True


def test_exact_tie_keeps_the_incumbent():
    """Republishing an identical-quality diagram costs a commit and a Pages
    build for nothing."""
    assert challenger_wins((3, 18), (3, 18)) is False


def test_no_incumbent_always_publishes():
    assert challenger_wins((0, 5), None) is True


def test_incumbent_needs_both_files(tmp_path=None):
    """A .mmd with no .svg is the leftover of a failed render, not a published
    diagram, so it must not block a re-run."""
    import tempfile
    d = Path(tempfile.mkdtemp())
    mmd, svg = d / "x.mmd", d / "x.svg"
    mmd.write_text("flowchart LR\n  A[One] --> B[Two]", encoding="utf-8")
    text, key = incumbent_quality(mmd, svg, diagram_richness)
    assert text is None and key is None
    svg.write_text("<svg/>", encoding="utf-8")
    text, key = incumbent_quality(mmd, svg, diagram_richness)
    assert text is not None and key[1] == 2


def test_missing_incumbent_is_not_an_error():
    d = Path("/nonexistent-dir-for-test")
    assert incumbent_quality(d / "a.mmd", d / "a.svg", diagram_richness) == (None, None)
