"""Turn validated templates + delay overrides into earliest cue tables."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .engine import (
    DeadlineExceededError,
    PositiveCycleError,
    solve_earliest,
)
from .errors import APIError


def id_sort_key(point_id: str) -> Tuple[int, Any]:
    """Canonical integer ids sort numerically first, then string ids
    lexicographically (the two categories never mix within a template)."""
    if point_id.isdigit():
        return (0, int(point_id))
    return (1, point_id)


def run_inference(
    valid: Dict[str, Any], delays: Dict[str, int]
) -> List[Dict[str, Any]]:
    """Compute earliest times.

    Raises ``APIError(422)`` with code ``positive_cycle`` or
    ``deadline_exceeded``; performs no I/O, so failure never persists.
    """
    ids: List[str] = valid["ids"]
    index = {point_id: i for i, point_id in enumerate(ids)}
    releases = [
        delays.get(point_id, valid["release"][point_id]) for point_id in ids
    ]
    edges = [
        (index[src], index[dst], gap) for src, dst, gap in valid["edges"]
    ]
    latest = [valid["latest"][point_id] for point_id in ids]

    try:
        times = solve_earliest(releases, edges, latest)
    except PositiveCycleError as exc:
        raise APIError(
            status_code=422,
            code="positive_cycle",
            detail="the rules contain a directed cycle with strictly positive "
                   "total min_gap; no schedule can satisfy it",
            extra={"cycle": [ids[i] for i in exc.cycle]},
        )
    except DeadlineExceededError as exc:
        offenders = sorted(exc.indices, key=lambda i: id_sort_key(ids[i]))
        raise APIError(
            status_code=422,
            code="deadline_exceeded",
            detail="one or more cue points cannot start at or before 'latest'",
            extra={
                "violations": [
                    {
                        "id": ids[i],
                        "earliest": exc.distances[i],
                        "latest": valid["latest"][ids[i]],
                    }
                    for i in offenders
                ]
            },
        )

    ordered_indexes = sorted(range(len(ids)), key=lambda i: id_sort_key(ids[i]))
    return [{"id": ids[i], "time": times[i]} for i in ordered_indexes]
