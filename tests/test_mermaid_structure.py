"""Regression tests for the structural repairs in sanitise_mermaid.

Each case is a real failure from the EA generation batch of 2026-09-17.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generate_rail_wiki import sanitise_mermaid  # noqa: E402


def test_bare_subgraph_gets_id_and_quotes():
    """EA-04 lost all three render attempts to this: an emoji butted against
    unquoted text is a lexical error in mermaid."""
    out = sanitise_mermaid('flowchart TB\n  subgraph \U0001F310External and Interline\n  end')
    assert 'subgraph SG1["\U0001F310External and Interline"]' in out


def test_subgraph_with_id_is_left_alone():
    out = sanitise_mermaid('flowchart TB\n  subgraph EXT["Feeds"]\n  end')
    assert 'subgraph EXT["Feeds"]' in out
    assert "SG1" not in out


def test_class_list_is_comma_separated():
    """EA-09 attempt 1: Expecting COMMA, got SPACE."""
    out = sanitise_mermaid("flowchart TB\n  class ISS CHDX SSDX ext")
    assert "class ISS,CHDX,SSDX ext" in out


def test_classdef_is_not_touched():
    out = sanitise_mermaid("flowchart TB\n  classDef ext fill:#e0f2fe,stroke:#0284c7")
    assert "classDef ext fill:#e0f2fe,stroke:#0284c7" in out


def test_subgraph_renamed_when_a_node_shares_its_id():
    """EA-04 and EA-12: Setting X as parent of X would create a cycle.
    The subgraph is renamed, never the node, because edges reference nodes."""
    out = sanitise_mermaid(
        'flowchart TB\n  subgraph DATA["Data"]\n    DATA["Platform"]\n  end\n  DATA --> X')
    assert 'subgraph DATA_GRP["Data"]' in out
    assert '    DATA["Platform"]' in out
    assert "DATA --> X" in out


def test_citations_still_survive_all_of_it():
    out = sanitise_mermaid('flowchart LR\n  A[Test per 49 CFR 213.9] --> B[GCOR 6.28]')
    assert "49 CFR 213.9" in out and "GCOR 6.28" in out
