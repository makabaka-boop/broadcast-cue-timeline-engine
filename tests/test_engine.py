"""Tests for the earliest-time engine (no web layer).

Covers the scenarios required by the spec: branch re-convergence, shuffled
input ordering, delay propagation, zero-gap rings, and positive-weight
rings, plus deadline checks and determinism.
"""

import random

import pytest

from app.engine import (
    DeadlineExceededError,
    PositiveCycleError,
    solve_earliest,
)


def times_by_id(ids, releases, edges_id, latest=None, delays=None):
    """Helper expressed in string ids and (from, to, gap) edges."""
    index = {point_id: i for i, point_id in enumerate(ids)}
    rel = [(delays or {}).get(pid, releases[pid]) for pid in ids]
    edges = [(index[a], index[b], g) for a, b, g in edges_id]
    bounds = None
    if latest is not None:
        bounds = [latest[pid] for pid in ids]
    values = solve_earliest(rel, edges, bounds)
    return dict(zip(ids, values))


def test_branch_reconvergence_takes_latest_predecessor():
    # After rehearsal one cue on a parallel branch is delayed; the merge
    # point must be driven by the latest of its predecessors, not the one
    # that happens to be listed first.
    ids = ["A", "B", "C", "MERGE"]
    releases = {"A": 0, "B": 0, "C": 0, "MERGE": 0}
    edges = [
        ("A", "B", 10),
        ("A", "C", 5),
        # Branch B is pushed late by a rehearsal delay override below.
        ("B", "MERGE", 0),
        ("C", "MERGE", 0),
    ]
    out = times_by_id(ids, releases, edges, delays={"B": 100})
    assert out == {"A": 0, "B": 100, "C": 5, "MERGE": 100}


def test_branch_reconvergence_with_gaps_uses_max_of_paths():
    ids = ["s", "a", "b", "m"]
    releases = {pid: 0 for pid in ids}
    edges = [("s", "a", 2), ("s", "b", 7), ("a", "m", 3), ("b", "m", 1)]
    out = times_by_id(ids, releases, edges)
    # s->a->m = 5, s->b->m = 8
    assert out["m"] == 8


def test_shuffled_relations_and_points_give_identical_table():
    releases = {"p1": 0, "p2": 1, "p3": 4, "p4": 0, "p5": 9}
    edges = [
        ("p1", "p2", 3),
        ("p2", "p3", 2),
        ("p3", "p5", 1),
        ("p1", "p4", 6),
        ("p4", "p5", 0),
        ("p2", "p4", 1),
    ]
    ids = list(releases)
    expected = times_by_id(ids, releases, edges)

    rng = random.Random(20260922)
    for trial in range(20):
        shuffled_ids = ids[:]
        shuffled_edges = edges[:]
        rng.shuffle(shuffled_ids)
        rng.shuffle(shuffled_edges)
        got = times_by_id(shuffled_ids, releases, shuffled_edges)
        assert got == expected, f"ordering changed result on trial {trial}"


def test_delay_propagates_through_entire_chain():
    ids = ["d0", "d1", "d2", "d3"]
    releases = {pid: 0 for pid in ids}
    edges = [
        ("d0", "d1", 4),
        ("d1", "d2", 4),
        ("d2", "d3", 4),
    ]
    out = times_by_id(ids, releases, edges, delays={"d0": 100})
    assert list(out[k] for k in ids) == [100, 104, 108, 112]


def test_delay_propagation_chooses_longest_of_two_routes():
    ids = ["x", "y", "z"]
    releases = {pid: 0 for pid in ids}
    edges = [("x", "z", 50), ("x", "y", 10), ("y", "z", 10)]
    out = times_by_id(ids, releases, edges, delays={"x": 7})
    assert out["z"] == 57  # direct route 7+50 beats 7+10+10


def test_zero_gap_ring_schedules_at_release():
    ids = ["r1", "r2", "r3"]
    releases = {"r1": 3, "r2": 0, "r3": 1}
    edges = [
        ("r1", "r2", 0),
        ("r2", "r3", 0),
        ("r3", "r1", 0),
    ]
    out = times_by_id(ids, releases, edges)
    assert out == {"r1": 3, "r2": 3, "r3": 3}


def test_zero_gap_self_loop_is_fine():
    out = times_by_id(["solo"], {"solo": 5}, [("solo", "solo", 0)])
    assert out == {"solo": 5}


def test_positive_self_loop_raises_positive_cycle():
    with pytest.raises(PositiveCycleError) as exc:
        times_by_id(["solo"], {"solo": 5}, [("solo", "solo", 1)])
    assert exc.value.cycle == [0]


def test_positive_cycle_raises_even_when_embedded():
    ids = ["a", "b", "c", "d"]
    releases = {pid: 0 for pid in ids}
    edges = [
        ("a", "b", 2),
        ("b", "c", 0),
        ("c", "b", 3),   # b -> c -> b has total weight 3 > 0
        ("b", "d", 1),
    ]
    with pytest.raises(PositiveCycleError) as exc:
        times_by_id(ids, releases, edges)
    cyc = set(exc.value.cycle)
    assert cyc == {ids.index("b"), ids.index("c")}


def test_positive_cycle_takes_priority_and_terminates():
    # A graph that also blows the deadline must still terminate and report
    # the cycle (the algorithm never spins forever).
    ids = ["a", "b"]
    releases = {"a": 0, "b": 0}
    edges = [("a", "b", 1), ("b", "a", 1)]
    latest = {"a": 0, "b": 0}
    with pytest.raises(PositiveCycleError):
        times_by_id(ids, releases, edges, latest=latest)


def test_deadline_exceeded_without_cycle():
    ids = ["a", "b"]
    releases = {"a": 0, "b": 0}
    latest = {"a": None, "b": 5}
    edges = [("a", "b", 6)]
    with pytest.raises(DeadlineExceededError) as exc:
        times_by_id(ids, releases, edges, latest=latest)
    assert exc.value.indices == [1]
    assert exc.value.distances[1] == 6


def test_deadline_equal_is_accepted():
    out = times_by_id(
        ["a"],
        {"a": 0},
        [],
        latest={"a": 10},
    )
    assert out == {"a": 0}
    out = times_by_id(
        ["a", "b"],
        {"a": 0, "b": 0},
        [("a", "b", 10)],
        latest={"a": None, "b": 10},
    )
    assert out["b"] == 10


def test_release_values_act_as_floor():
    out = times_by_id(
        ["x", "y"],
        {"x": 100, "y": 200},
        [("x", "y", 1)],
    )
    assert out == {"x": 100, "y": 200}


def test_diamond_with_mixed_zero_and_positive_gaps():
    ids = ["s", "l", "r", "m"]
    releases = {pid: 0 for pid in ids}
    edges = [
        ("s", "l", 10),
        ("s", "r", 0),
        ("l", "m", 0),
        ("r", "m", 5),
    ]
    out = times_by_id(ids, releases, edges)
    assert out["m"] == 10


def test_large_chain_completes_without_enumerating_times():
    n = 300
    ids = [f"n{i}" for i in range(n)]
    releases = {pid: 0 for pid in ids}
    edges = [(ids[i], ids[i + 1], 1) for i in range(n - 1)]
    out = times_by_id(ids, releases, edges)
    assert out[ids[-1]] == n - 1
