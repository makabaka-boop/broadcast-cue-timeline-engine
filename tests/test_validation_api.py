"""Validation rules for templates and delay overrides (via the API)."""

GOOD_TEMPLATE = {
    "points": [
        {"id": "a", "release": 0},
        {"id": "b", "release": 1, "latest": 100},
        {"id": 7, "release": 2},
    ],
    "relations": [
        {"from": "a", "to": "b", "min_gap": 0},
        {"from": 7, "to": "a", "min_gap": 1_000_000},
    ],
}


def register(client, payload):
    return client.post("/templates", json=payload)


def test_valid_template_is_accepted(client):
    resp = register(client, GOOD_TEMPLATE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert body["template"] == GOOD_TEMPLATE


def test_rejects_non_object_body(client):
    resp = register(client, [1, 2, 3])
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_template"


def test_rejects_empty_points(client):
    resp = register(client, {"points": []})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_template"


def test_rejects_too_many_points(client):
    payload = {"points": [{"id": f"p{i}", "release": 0} for i in range(301)]}
    resp = register(client, payload)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_template"


def test_rejects_duplicate_point_ids(client):
    payload = {
        "points": [
            {"id": "x", "release": 0},
            {"id": "x", "release": 1},
        ]
    }
    resp = register(client, payload)
    assert resp.status_code == 400
    assert "duplicate" in resp.json()["error"]["detail"]


def test_rejects_numeric_and_string_same_id(client):
    payload = {
        "points": [
            {"id": 9, "release": 0},
            {"id": "9", "release": 1},
        ]
    }
    resp = register(client, payload)
    assert resp.status_code == 400
    assert "duplicate" in resp.json()["error"]["detail"]


def test_rejects_release_out_of_range(client):
    payload = {"points": [{"id": "a", "release": 10**9 + 1}]}
    resp = register(client, payload)
    assert resp.status_code == 400
    payload = {"points": [{"id": "a", "release": -1}]}
    resp = register(client, payload)
    assert resp.status_code == 400


def test_rejects_boolean_as_number(client):
    payload = {"points": [{"id": "a", "release": True}]}
    resp = register(client, payload)
    assert resp.status_code == 400


def test_rejects_latest_smaller_than_release(client):
    payload = {"points": [{"id": "a", "release": 5, "latest": 4}]}
    resp = register(client, payload)
    assert resp.status_code == 400


def test_rejects_unknown_relation_endpoint(client):
    payload = {
        "points": [{"id": "a", "release": 0}],
        "relations": [{"from": "a", "to": "ghost", "min_gap": 0}],
    }
    resp = register(client, payload)
    assert resp.status_code == 400
    assert "unknown endpoint" in resp.json()["error"]["detail"]


def test_rejects_gap_out_of_range(client):
    payload = {
        "points": [{"id": "a", "release": 0}, {"id": "b", "release": 0}],
        "relations": [{"from": "a", "to": "b", "min_gap": 1_000_001}],
    }
    resp = register(client, payload)
    assert resp.status_code == 400


def test_rejects_too_many_relations(client):
    payload = {
        "points": [
            {"id": "a", "release": 0},
            {"id": "b", "release": 0},
        ],
        "relations": [
            {"from": "a", "to": "b", "min_gap": 0} for _ in range(3001)
        ],
    }
    resp = register(client, payload)
    assert resp.status_code == 400


def test_rejects_unknown_fields(client):
    payload = {"points": [], "nope": 1}
    resp = register(client, payload)
    assert resp.status_code == 400
    payload = {
        "points": [{"id": "a", "release": 0, "extra": 9}],
    }
    resp = register(client, payload)
    assert resp.status_code == 400


def test_malformed_json_body(client):
    resp = client.post(
        "/templates", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_json"


def test_invalid_template_is_not_persisted(client):
    before = client.app.state.db.count_templates()
    register(client, {"points": []})
    register(client, {"points": [{"id": "x", "release": -1}]})
    register(client, "nonsense")
    after = client.app.state.db.count_templates()
    assert before == after == 0


def test_delay_must_be_at_least_release(client):
    tid = register(client, GOOD_TEMPLATE).json()["id"]
    resp = client.post(
        f"/templates/{tid}/inferences", json={"delays": {"b": 0}}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_delay"


def test_delay_unknown_point(client):
    tid = register(client, GOOD_TEMPLATE).json()["id"]
    resp = client.post(
        f"/templates/{tid}/inferences", json={"delays": {"ghost": 5}}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_delay"


def test_numeric_id_delay_uses_canonical_string_key(client):
    resp_tmpl = register(
        client,
        {
            "points": [
                {"id": 7, "release": 0},
                {"id": "8", "release": 0},
            ],
            "relations": [{"from": 7, "to": 8, "min_gap": 1}],
        },
    )
    tid = resp_tmpl.json()["id"]
    resp = client.post(
        f"/templates/{tid}/inferences", json={"delays": {"7": 2}}
    )
    assert resp.status_code == 201, resp.text
    rows = {r["id"]: r["time"] for r in resp.json()["results"]}
    assert rows["7"] == 2
    assert rows["8"] == 3
