"""Earliest-time scheduling core.

Each cue point ``i`` has a lower bound ``release[i]`` and, optionally, an
upper bound ``latest[i]``. Every relation ``(f, t, g)`` requires::

    time[t] >= time[f] + g

The earliest feasible times are the smallest values satisfying every lower
bound at once. Equivalently this is the longest-path problem in a weighted
digraph with an implicit source linked to every node by an edge whose weight
is that node's release. Candidate times are never enumerated: distances are
closed by edge relaxation (Bellman-Ford longest-path form), which is exact
for the maximum-300-node / maximum-3000-edge graphs the templates allow.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


class PositiveCycleError(Exception):
    """Raised when the graph contains a directed cycle of strictly
    positive total gap. No feasible schedule exists in that case."""

    def __init__(self, cycle: List[int]) -> None:
        self.cycle = cycle
        super().__init__(f"positive cycle through indices: {cycle}")


def solve_earliest(
    releases: Sequence[int],
    edges: Sequence[Tuple[int, int, int]],
    latest: Optional[Sequence[Optional[int]]] = None,
) -> List[int]:
    """Return the earliest feasible time of every point.

    ``releases[i]`` is the delay-overridden release of point ``i``.
    ``edges`` are ``(from_index, to_index, min_gap)`` triples.
    When the resulting earliest time of any point is above its ``latest``
    value, a ``DeadlineExceededError`` is raised.
    """
    n = len(releases)
    distance = list(releases)

    # Implicit source edges (weight = release) are represented by the
    # initial distances. Each sweep relaxes all explicit relations; an
    # nth successful sweep implies a strictly positive cycle.
    predecessor: List[Optional[int]] = [None] * n
    last_relaxed: Optional[int] = None
    for sweep in range(n):
        last_relaxed = None
        changed = False
        for src, dst, gap in edges:
            candidate = distance[src] + gap
            if candidate > distance[dst]:
                distance[dst] = candidate
                predecessor[dst] = src
                last_relaxed = dst
                changed = True
        if not changed:
            break
    else:
        # The nth sweep still tightened a value: walk predecessors to
        # recover a node on the positive cycle.
        node = last_relaxed
        assert node is not None
        seen_order: List[int] = []
        seen_at = {}
        while node is not None and node not in seen_at:
            seen_at[node] = len(seen_order)
            seen_order.append(node)
            node = predecessor[node]
        cycle = seen_order[seen_at[node]:] if node is not None else seen_order
        raise PositiveCycleError(cycle)

    if latest is not None:
        offenders = [
            i
            for i, bound in enumerate(latest)
            if bound is not None and distance[i] > bound
        ]
        if offenders:
            raise DeadlineExceededError(offenders, distance)

    return distance


class DeadlineExceededError(Exception):
    """Raised when no positive cycle exists but one or more points cannot
    be scheduled at or before their ``latest`` time."""

    def __init__(self, indices: List[int], distances: List[int]) -> None:
        self.indices = indices
        self.distances = distances
        super().__init__(f"latest exceeded for indices: {indices}")
