"""End-to-end inference behaviour through the HTTP API, incl. persistence."""

import json

import pytest


def register(client, payload):
    resp = client.post("/templates", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def infer(client, tid, delays=None):
    body = {} if delays is None else {"delays": delays}
    return client.post(f"/templates/{tid}/inferences", json=body)


def results_map(resp):
    return {row["id"]: row["time"] for row in resp.json()["results"]}


def test_successful_inference_results_sorted_with_mixed_id_types(client):
    tid = register(
        client,
        {
            "points": [
                {"id": "b", "release": 10},
                {"id": "2", "release": 0},
                {"id": 10, "release": 0},
                {"id": "1", "release": 0},
                {"id": "a", "release": 3},
            ]
        },
    )
    resp = infer(client, tid)
    assert resp.status_code == 201
    ids = [row["id"] for row in resp.json()["results"]]
    # Integer ids first in numeric order, then string ids lexicographically.
    assert ids == ["1", "2", "10", "a", "b"]


def test_branch_merge_with_delay_via_api(client):
    tid = register(
        client,
        {
            "points": [
                {"id": "A", "release": 0},
                {"id": "B", "release": 0},
                {"id": "C", "release": 0},
                {"id": "M", "release": 0},
            ],
            "relations": [
                {"from": "A", "to": "B", "min_gap": 10},
                {"from": "A", "to": "C", "min_gap": 5},
                {"from": "B", "to": "M", "min_gap": 0},
                {"from": "C", "to": "M", "min_gap": 0},
            ],
        },
    )
    resp = infer(client, tid, {"B": 100})
    assert results_map(resp) == {"A": 0, "B": 100, "C": 5, "M": 100}


def test_zero_gap_ring_is_schedulable(client):
    tid = register(
        client,
        {
            "points": [
                {"id": "r1", "release": 3},
                {"id": "r2", "release": 0},
                {"id": "r3", "release": 1},
            ],
            "relations": [
                {"from": "r1", "to": "r2", "min_gap": 0},
                {"from": "r2", "to": "r3", "min_gap": 0},
                {"from": "r3", "to": "r1", "min_gap": 0},
            ],
        },
    )
    resp = infer(client, tid)
    assert resp.status_code == 201
    assert results_map(resp) == {"r1": 3, "r2": 3, "r3": 3}


def test_positive_cycle_returns_stable_code(client):
    tid = register(
        client,
        {
            "points": [
                {"id": "a", "release": 0},
                {"id": "b", "release": 0},
            ],
            "relations": [
                {"from": "a", "to": "b", "min_gap": 2},
                {"from": "b", "to": "a", "min_gap": 1},
            ],
        },
    )
    resp = infer(client, tid)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "positive_cycle"
    assert set(err["cycle"]) == {"a", "b"}


def test_deadline_exceeded_returns_stable_code(client):
    tid = register(
        client,
        {
            "points": [
                {"id": "a", "release": 0},
                {"id": "b", "release": 0, "latest": 5},
            ],
            "relations": [{"from": "a", "to": "b", "min_gap": 6}],
        },
    )
    resp = infer(client, tid)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "deadline_exceeded"
    violations = err["violations"]
    assert len(violations) == 1
    assert violations[0]["id"] == "b"
    assert violations[0]["earliest"] == 6
    assert violations[0]["latest"] == 5


def test_delay_push_causes_deadline_exceeded(client):
    tid = register(
        client,
        {
            "points": [
                {"id": "a", "release": 0},
                {"id": "b", "release": 0, "latest": 10},
            ],
            "relations": [{"from": "a", "to": "b", "min_gap": 2}],
        },
    )
    ok = infer(client, tid)
    assert ok.status_code == 201
    bad = infer(client, tid, {"a": 9})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "deadline_exceeded"


def test_failed_inference_creates_no_record(client):
    tid = register(
        client,
        {
            "points": [{"id": "a", "release": 0}, {"id": "b", "release": 0}],
            "relations": [
                {"from": "a", "to": "b", "min_gap": 1},
                {"from": "b", "to": "a", "min_gap": 1},
            ],
        },
    )
    before = client.app.state.db.count_inferences()
    for _ in range(3):
        resp = infer(client, tid)
        assert resp.status_code == 422
    assert client.app.state.db.count_inferences() == before


def test_successful_result_is_queryable(client):
    tid = register(
        client,
        {
            "points": [{"id": "a", "release": 1}, {"id": "b", "release": 0}],
            "relations": [{"from": "a", "to": "b", "min_gap": 4}],
        },
    )
    created = infer(client, tid).json()
    iid = created["id"]
    fetched = client.get(f"/inferences/{iid}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["id"] == iid
    assert body["template_id"] == tid
    assert body["status"] == "ok"
    assert body["results"] == created["results"]


def test_inference_on_missing_template_404(client):
    resp = client.post("/templates/nope/inferences", json={})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_missing_inference_404(client):
    resp = client.get("/inferences/deadbeef")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_shuffled_relation_order_gives_same_table(client):
    points = [
        {"id": "a", "release": 0},
        {"id": "b", "release": 0},
        {"id": "c", "release": 0},
    ]
    rels1 = [
        {"from": "a", "to": "b", "min_gap": 3},
        {"from": "b", "to": "c", "min_gap": 2},
        {"from": "a", "to": "c", "min_gap": 1},
    ]
    rels2 = list(reversed(rels1))
    t1 = register(client, {"points": points, "relations": rels1})
    t2 = register(client, {"points": list(reversed(points)), "relations": rels2})
    r1 = results_map(infer(client, t1))
    r2 = results_map(infer(client, t2))
    assert r1 == r2 == {"a": 0, "b": 3, "c": 5}


def test_successful_inference_after_failure_does_not_damage_data(client):
    # A failing template first...
    bad_tid = register(
        client,
        {
            "points": [{"id": "x", "release": 0}],
            "relations": [{"from": "x", "to": "x", "min_gap": 1}],
        },
    )
    assert infer(client, bad_tid).status_code == 422

    # ...then a good template stores and returns a result.
    tid = register(
        client,
        {
            "points": [{"id": "y", "release": 42}],
        },
    )
    resp = infer(client, tid)
    assert resp.status_code == 201
    assert results_map(resp) == {"y": 42}
    assert client.get(f"/templates/{tid}").status_code == 200


def test_big_values_fit_integer_range(client):
    tid = register(
        client,
        {
            "points": [
                {"id": "a", "release": 10**9},
                {"id": "b", "release": 0},
            ],
            "relations": [{"from": "a", "to": "b", "min_gap": 10**6}],
        },
    )
    resp = infer(client, tid)
    assert results_map(resp)["b"] == 10**9 + 10**6


def test_result_persists_across_app_restart(tmp_path):
    db_path = str(tmp_path / "persist.db")
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(db_path=db_path)) as c1:
        tid = c1.post(
            "/templates",
            json={
                "points": [
                    {"id": "a", "release": 0},
                    {"id": "b", "release": 0},
                ],
                "relations": [{"from": "a", "to": "b", "min_gap": 7}],
            },
        ).json()["id"]
        body = c1.post(f"/templates/{tid}/inferences", json={}).json()
        iid = body["id"]

    # Simulate container restart: a brand-new app over the same database.
    with TestClient(create_app(db_path=db_path)) as c2:
        resp = c2.get(f"/inferences/{iid}")
        assert resp.status_code == 200
        assert resp.json()["results"] == [
            {"id": "a", "time": 0},
            {"id": "b", "time": 7},
        ]
        tmpl = c2.get(f"/templates/{tid}")
        assert tmpl.status_code == 200
