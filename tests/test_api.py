"""The HTTP journey uses real solver output and a separate validation request."""

from fastapi.testclient import TestClient

from heatshield.api import app


def test_optimize_then_independently_validate_and_reject_tampering():
    with TestClient(app) as client:
        assert client.get("/healthz").json()["mode"] == "synthetic"
        response = client.get("/api/v1/demo/scenario")
        assert response.status_code == 200
        scenario = response.json()
        optimized = client.post("/api/v1/scenarios/optimize", json=scenario)
        assert optimized.status_code == 200
        result = optimized.json()
        assert result["status"] in {"OPTIMAL", "FEASIBLE"}
        assert result["validation"]["valid"] is True
        assert result["validation"]["review_required"] is True
        assert result["validation"]["operational_approval_allowed"] is False
        assert result["metadata"]["seed"] == scenario["seed"]
        assert result["metadata"]["solver_version"]
        schedule = result["schedule"]
        assert result["metadata"]["scenario_hash"] == schedule["scenario_hash"]
        assert result["review_required"] is True
        assert result["operational_approval_allowed"] is False
        checked = client.post(
            "/api/v1/scenarios/validate", json={"scenario": scenario, "schedule": schedule}
        )
        assert checked.status_code == 200
        assert checked.json()["valid"] is True
        schedule["assignments"].pop()
        tampered = client.post(
            "/api/v1/scenarios/validate", json={"scenario": scenario, "schedule": schedule}
        )
        assert tampered.status_code == 200
        assert tampered.json()["valid"] is False
        assert tampered.json()["violations"]


def test_unknown_fields_and_non_synthetic_requests_fail_before_solver():
    with TestClient(app) as client:
        scenario = client.get("/api/v1/demo/scenario").json()
        scenario["allow_unsafe_override"] = True
        response = client.post("/api/v1/scenarios/optimize", json=scenario)
        assert response.status_code == 422
        del scenario["allow_unsafe_override"]
        scenario["mode"] = "production"
        assert client.post("/api/v1/scenarios/optimize", json=scenario).status_code == 422


def test_openapi_exposes_no_approval_or_publish_commands():
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        assert "/api/v1/scenarios/optimize" in schema["paths"]
        assert "/api/v1/scenarios/validate" in schema["paths"]
        assert not any("approve" in path or "publish" in path for path in schema["paths"])


def test_extreme_timestamp_returns_input_error_instead_of_server_error():
    with TestClient(app) as client:
        scenario = client.get("/api/v1/demo/scenario").json()
        scenario["observations"][0]["observed_at"] = "9999-12-31T23:00:00-02:00"
        assert client.post("/api/v1/scenarios/optimize", json=scenario).status_code == 422


def test_infeasible_run_retains_provenance_without_a_schedule():
    with TestClient(app) as client:
        scenario = client.get("/api/v1/demo/scenario").json()
        scenario["tasks"][0]["required_skill"] = "missing-qualification"
        result = client.post("/api/v1/scenarios/optimize", json=scenario).json()
        assert result["status"] == "INFEASIBLE"
        assert result["schedule"] is None
        assert len(result["metadata"]["scenario_hash"]) == 64
        assert len(result["metadata"]["policy_hash"]) == 64
        assert result["metadata"]["schema_version"] == "1"
        assert result["operational_approval_allowed"] is False
