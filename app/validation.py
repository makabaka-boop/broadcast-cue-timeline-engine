"""Strict validation for template documents and delay overrides.

Nothing validated here is persisted on failure: callers only receive a
``ValidationFailure`` carrying the stable error code ``invalid_template``
(or ``invalid_delay``) plus a human-readable detail.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MAX_POINTS = 300
MAX_RELATIONS = 3000
MAX_RELEASE = 10**9
MAX_GAP = 10**6
MAX_ID_LENGTH = 128

ALLOWED_TOP_FIELDS = {"points", "relations"}
ALLOWED_POINT_FIELDS = {"id", "release", "latest"}
ALLOWED_RELATION_FIELDS = {"from", "to", "min_gap"}
ALLOWED_DELAY_FIELDS = {"delays"}


class ValidationFailure(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _is_int(value: Any) -> bool:
    # bool is a subtype of int in Python but is never an accepted number.
    return isinstance(value, int) and not isinstance(value, bool)


def _id_repr(raw_id: Any) -> str:
    return str(raw_id) if isinstance(raw_id, str) else repr(raw_id)


def _validate_point_id(value: Any, where: str) -> str:
    if isinstance(value, str):
        if value == "":
            raise ValidationFailure(
                "invalid_template", f"{where}: point id must be non-empty"
            )
        if len(value) > MAX_ID_LENGTH:
            raise ValidationFailure(
                "invalid_template",
                f"{where}: point id exceeds {MAX_ID_LENGTH} characters",
            )
        return value
    if _is_int(value):
        # Normalize numeric ids to their canonical decimal spelling so that
        # 1 and "1" can never coexist; results sort numerically too.
        return str(value)
    raise ValidationFailure(
        "invalid_template",
        f"{where}: point id must be a string or integer, got {type(value).__name__}",
    )


def validate_template(payload: Any) -> Dict[str, Any]:
    """Validate an incoming template document.

    Returns a normalized representation::

        {"ids": [str, ...],
         "release": {"id": int},
         "latest":  {"id": int | None},
         "edges":   [(from_id, to_id, gap), ...],
         "document": storage-ready JSON object}
    """
    if not isinstance(payload, dict):
        raise ValidationFailure(
            "invalid_template", "template body must be a JSON object"
        )
    unknown = set(payload) - ALLOWED_TOP_FIELDS
    if unknown:
        raise ValidationFailure(
            "invalid_template", f"unexpected field(s): {sorted(unknown)}"
        )
    if "points" not in payload:
        raise ValidationFailure("invalid_template", "field 'points' is required")

    raw_points = payload["points"]
    if not isinstance(raw_points, list):
        raise ValidationFailure(
            "invalid_template", "'points' must be an array of point objects"
        )
    if not (1 <= len(raw_points) <= MAX_POINTS):
        raise ValidationFailure(
            "invalid_template",
            f"number of points must be between 1 and {MAX_POINTS}",
        )

    raw_relations = payload.get("relations", [])
    if not isinstance(raw_relations, list):
        raise ValidationFailure(
            "invalid_template", "'relations' must be an array"
        )
    if len(raw_relations) > MAX_RELATIONS:
        raise ValidationFailure(
            "invalid_template",
            f"number of relations must not exceed {MAX_RELATIONS}",
        )

    ids: List[str] = []
    release: Dict[str, int] = {}
    latest: Dict[str, Optional[int]] = {}
    seen: set = set()

    for pos, point in enumerate(raw_points):
        where = f"points[{pos}]"
        if not isinstance(point, dict):
            raise ValidationFailure(
                "invalid_template", f"{where}: point must be an object"
            )
        unknown = set(point) - ALLOWED_POINT_FIELDS
        if unknown:
            raise ValidationFailure(
                "invalid_template", f"{where}: unexpected field(s): {sorted(unknown)}"
            )
        if "id" not in point:
            raise ValidationFailure("invalid_template", f"{where}: 'id' is required")
        point_id = _validate_point_id(point["id"], where)
        if point_id in seen:
            raise ValidationFailure(
                "invalid_template", f"duplicate point id: {point['id']!r}"
            )
        seen.add(point_id)

        if "release" not in point:
            raise ValidationFailure(
                "invalid_template", f"{where}: 'release' is required"
            )
        rel = point["release"]
        if not _is_int(rel):
            raise ValidationFailure(
                "invalid_template", f"{where}: 'release' must be an integer"
            )
        if not (0 <= rel <= MAX_RELEASE):
            raise ValidationFailure(
                "invalid_template",
                f"{where}: 'release' must be between 0 and {MAX_RELEASE}",
            )

        bound: Optional[int] = None
        if "latest" in point:
            bound = point["latest"]
            if not _is_int(bound):
                raise ValidationFailure(
                    "invalid_template", f"{where}: 'latest' must be an integer"
                )
            if bound < rel:
                raise ValidationFailure(
                    "invalid_template",
                    f"{where}: 'latest' must not be smaller than 'release'",
                )

        ids.append(point_id)
        release[point_id] = rel
        latest[point_id] = bound

    edges: List[Tuple[str, str, int]] = []
    for pos, relation in enumerate(raw_relations):
        where = f"relations[{pos}]"
        if not isinstance(relation, dict):
            raise ValidationFailure(
                "invalid_template", f"{where}: relation must be an object"
            )
        unknown = set(relation) - ALLOWED_RELATION_FIELDS
        if unknown:
            raise ValidationFailure(
                "invalid_template",
                f"{where}: unexpected field(s): {sorted(unknown)}",
            )
        for field in ("from", "to", "min_gap"):
            if field not in relation:
                raise ValidationFailure(
                    "invalid_template", f"{where}: '{field}' is required"
                )
        src = _validate_point_id(relation["from"], f"{where}.from")
        dst = _validate_point_id(relation["to"], f"{where}.to")
        gap = relation["min_gap"]
        if not _is_int(gap):
            raise ValidationFailure(
                "invalid_template", f"{where}: 'min_gap' must be an integer"
            )
        if not (0 <= gap <= MAX_GAP):
            raise ValidationFailure(
                "invalid_template",
                f"{where}: 'min_gap' must be between 0 and {MAX_GAP}",
            )
        if src not in seen:
            raise ValidationFailure(
                "invalid_template",
                f"{where}: unknown endpoint 'from'={_id_repr(relation['from'])}",
            )
        if dst not in seen:
            raise ValidationFailure(
                "invalid_template",
                f"{where}: unknown endpoint 'to'={_id_repr(relation['to'])}",
            )
        edges.append((src, dst, gap))

    # Rebuild the canonical document (preserves the submitted ordering).
    document: Dict[str, Any] = {
        "points": [
            {
                "id": raw_points[i]["id"],
                "release": release[ids[i]],
                **(
                    {"latest": latest[ids[i]]}
                    if latest[ids[i]] is not None
                    else {}
                ),
            }
            for i in range(len(ids))
        ],
        "relations": [
            {"from": raw_relations[i]["from"], "to": raw_relations[i]["to"],
             "min_gap": edges[i][2]}
            for i in range(len(edges))
        ],
    }

    return {
        "ids": ids,
        "release": release,
        "latest": latest,
        "edges": edges,
        "document": document,
    }


def validate_delay_overrides(
    payload: Any, valid: Dict[str, Any]
) -> Dict[str, int]:
    """Validate the body of an inference request against a valid template."""
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValidationFailure("invalid_delay", "request body must be a JSON object")
    unknown = set(payload) - ALLOWED_DELAY_FIELDS
    if unknown:
        raise ValidationFailure(
            "invalid_delay", f"unexpected field(s): {sorted(unknown)}"
        )
    raw = payload.get("delays", {})
    if not isinstance(raw, dict):
        raise ValidationFailure("invalid_delay", "'delays' must be a JSON object")

    known = set(valid["ids"])
    resolved: Dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValidationFailure(
                "invalid_delay", f"delay key {key!r} must be a point id"
            )
        if key not in known:
            # JSON object keys are always strings; numeric ids are spelled
            # canonically (e.g. "12") in that representation.
            raise ValidationFailure(
                "invalid_delay", f"unknown point id in delays: {key!r}"
            )
        if not _is_int(value):
            raise ValidationFailure(
                "invalid_delay", f"delays[{key!r}] must be an integer"
            )
        if value < valid["release"][key]:
            raise ValidationFailure(
                "invalid_delay",
                f"delays[{key!r}] must be >= the original release "
                f"{valid['release'][key]}",
            )
        if key in resolved:
            raise ValidationFailure("invalid_delay", f"duplicate delay for {key!r}")
        resolved[key] = value
    return resolved
