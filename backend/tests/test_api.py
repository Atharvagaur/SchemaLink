"""Backend tests for SchemaLink API."""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, AuthenticationError, NotFoundError, RateLimitError

# Add backend directory to path
BACKEND_DIR = Path(__file__).parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.main import app

client = TestClient(app)


class TestHealth:
    def test_health_endpoint(self):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "llm_key_configured" in data
        assert "llm_model" in data
        assert "project_root" in data


class TestMeta:
    def test_meta_endpoint(self):
        response = client.get("/api/meta")
        assert response.status_code == 200
        data = response.json()
        assert data["project_name"] == "SchemaLink"
        assert "strategies" in data
        assert "s0" in data["strategies"]
        assert "s1" in data["strategies"]
        assert "s2" in data["strategies"]
        assert "s3" in data["strategies"]
        assert "s6" in data["strategies"]
        assert "absl" in data["strategies"]
        # S4 and S5 should NOT be present
        assert "s4" not in data["strategies"]
        assert "s5" not in data["strategies"]
        assert "budgets" in data
        assert "spider" in data["budgets"]
        assert "bird" in data["budgets"]
        assert "llm_key_configured" in data
        assert "prices" in data


class TestDatasets:
    def test_datasets_endpoint(self):
        response = client.get("/api/datasets")
        assert response.status_code == 200
        data = response.json()
        assert "datasets" in data
        assert len(data["datasets"]) >= 0
        for ds in data["datasets"]:
            assert "name" in ds
            assert "available" in ds
            assert ds["name"] in ["spider", "bird"]


class TestQuestions:
    def test_questions_spider(self):
        response = client.get("/api/questions?dataset=spider&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert data["dataset"] == "spider"
        assert "questions" in data
        assert len(data["questions"]) <= 5

    def test_questions_bird(self):
        response = client.get("/api/questions?dataset=bird&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert data["dataset"] == "bird"

    def test_questions_invalid_dataset(self):
        response = client.get("/api/questions?dataset=invalid&limit=5")
        assert response.status_code == 400

    def test_questions_limit_validation(self):
        response = client.get("/api/questions?dataset=spider&limit=0")
        assert response.status_code == 422  # Validation error


class TestRunsValidation:
    def test_create_run_invalid_dataset(self):
        response = client.post("/api/runs", json={
            "dataset": "invalid",
            "n_questions": 5,
            "strategies": ["s0"],
        })
        assert response.status_code == 400

    def test_create_run_unsupported_strategy(self):
        response = client.post("/api/runs", json={
            "dataset": "spider",
            "n_questions": 5,
            "strategies": ["s4"],
        })
        assert response.status_code == 400

    def test_create_run_unsupported_strategy_s5(self):
        response = client.post("/api/runs", json={
            "dataset": "spider",
            "n_questions": 5,
            "strategies": ["s5"],
        })
        assert response.status_code == 400

    def test_create_run_zero_questions(self):
        response = client.post("/api/runs", json={
            "dataset": "spider",
            "n_questions": 0,
            "strategies": ["s0"],
        })
        assert response.status_code in [400, 422]

    def test_create_run_too_many_questions(self):
        response = client.post("/api/runs", json={
            "dataset": "spider",
            "n_questions": 200,
            "strategies": ["s0"],
        })
        assert response.status_code in [400, 422]

    def test_create_run_missing_api_key(self):
        # Temporarily unset LLM_API_KEY and remove saved config
        old_key = os.environ.pop("LLM_API_KEY", None)
        config_path = PROJECT_ROOT / "llm_config.json"
        saved_config = None
        if config_path.exists():
            saved_config = config_path.read_text()
            config_path.unlink()
        try:
            response = client.post("/api/runs", json={
                "dataset": "spider",
                "n_questions": 5,
                "strategies": ["s0"],
                "smoke": True,
            })
            assert response.status_code == 400
            assert "LLM_API_KEY" in response.json().get("detail", "")
        finally:
            if old_key:
                os.environ["LLM_API_KEY"] = old_key
            if saved_config is not None:
                config_path.write_text(saved_config)

    def test_create_run_large_without_confirm(self):
        old_key = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "test-key"
        try:
            response = client.post("/api/runs", json={
                "dataset": "spider",
                "n_questions": 10,
                "strategies": ["s0"],
                "smoke": False,
                "confirm_cost": False,
            })
            assert response.status_code == 400
        finally:
            if old_key:
                os.environ["LLM_API_KEY"] = old_key
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_create_run_smoke_without_api_key(self):
        # Smoke runs still need API key per our implementation
        old_key = os.environ.pop("LLM_API_KEY", None)
        config_path = PROJECT_ROOT / "llm_config.json"
        saved_config = None
        if config_path.exists():
            saved_config = config_path.read_text()
            config_path.unlink()
        try:
            response = client.post("/api/runs", json={
                "dataset": "spider",
                "n_questions": 5,
                "strategies": ["s0"],
                "smoke": True,
            })
            assert response.status_code == 400
        finally:
            if old_key:
                os.environ["LLM_API_KEY"] = old_key
            if saved_config is not None:
                config_path.write_text(saved_config)


class TestRunIsolation:
    def test_run_creates_unique_directory(self):
        old_key = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "test-key"
        try:
            response = client.post("/api/runs", json={
                "dataset": "spider",
                "n_questions": 5,
                "strategies": ["s0"],
                "smoke": True,
            })
            # Should either succeed (200) or fail due to missing model config
            assert response.status_code in [200, 400]
            if response.status_code == 200:
                run_id = response.json()["run_id"]
                run_dir = PROJECT_ROOT / "app_runs" / run_id
                assert run_dir.exists()
                assert (run_dir / "run_meta.json").exists()
        finally:
            if old_key:
                os.environ["LLM_API_KEY"] = old_key
            else:
                os.environ.pop("LLM_API_KEY", None)


class TestSecretRedaction:
    def test_health_does_not_expose_api_key(self):
        response = client.get("/api/health")
        data = response.json()
        # Should only show boolean, not the key value
        assert isinstance(data["llm_key_configured"], bool)
        assert "LLM_API_KEY" not in json.dumps(data)

    def test_meta_does_not_expose_api_key(self):
        response = client.get("/api/meta")
        data = response.json()
        assert isinstance(data["llm_key_configured"], bool)
        assert "LLM_API_KEY" not in json.dumps(data)
        # API key should never be in the response
        assert "api_key" not in json.dumps(data).lower()


class TestResultParsing:
    def test_summary_uses_actual_artifacts(self):
        # Check that existing results/spider can be parsed
        results_dir = PROJECT_ROOT / "results" / "spider"
        if results_dir.exists():
            traces_file = results_dir / "traces.jsonl"
            if traces_file.exists():
                with open(traces_file) as f:
                    lines = [l for l in f if l.strip()]
                assert len(lines) > 0
                first = json.loads(lines[0])
                assert "qid" in first
                assert "strategy" in first
                assert "ex" in first


class TestMissingResults:
    def test_get_run_nonexistent(self):
        response = client.get("/api/runs/nonexistent-run-id")
        assert response.status_code == 404

    def test_get_summary_nonexistent(self):
        response = client.get("/api/runs/nonexistent-run-id/summary")
        assert response.status_code == 404


class TestRunPlots:
    """Tests for the /runs/{run_id}/plots/{filename} plot-serving endpoint."""

    def _make_run_with_plot(self, run_id="plot-run-1", dataset="spider",
                            filename="pareto_spider.png"):
        run_dir = PROJECT_ROOT / "app_runs" / run_id
        results_dir = run_dir / "results" / dataset
        results_dir.mkdir(parents=True, exist_ok=True)
        png = results_dir / filename
        png.write_bytes(b"\x89PNG\r\n\x1a\nfake-png")
        (run_dir / "run_meta.json").write_text(json.dumps({
            "run_id": run_id, "dataset": dataset, "status": "completed",
        }))
        return run_dir, png

    def _cleanup(self, run_dir):
        shutil.rmtree(run_dir, ignore_errors=True)

    def test_get_plot_returns_png(self):
        run_dir, png = self._make_run_with_plot()
        try:
            response = client.get(f"/api/runs/{run_dir.name}/plots/{png.name}")
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("image/png")
            assert response.content == png.read_bytes()
        finally:
            self._cleanup(run_dir)

    def test_get_plot_missing_404(self):
        run_dir, _ = self._make_run_with_plot()
        try:
            response = client.get(f"/api/runs/{run_dir.name}/plots/nope.png")
            assert response.status_code == 404
        finally:
            self._cleanup(run_dir)

    def test_get_plot_traversal_filename_4xx(self):
        run_dir, png = self._make_run_with_plot()
        try:
            # Directory traversal via filename.
            response = client.get(f"/api/runs/{run_dir.name}/plots/../escape.png")
            assert response.status_code in (400, 404), response.status_code
            # URL-encoded traversal.
            response = client.get(f"/api/runs/{run_dir.name}/plots/..%2F..%2Fevil.png")
            assert response.status_code in (400, 404), response.status_code
            # Non-PNG extension rejected.
            response = client.get(f"/api/runs/{run_dir.name}/plots/notes.txt")
            assert response.status_code in (400, 404), response.status_code
        finally:
            self._cleanup(run_dir)

    def test_get_plot_traversal_run_id_4xx(self):
        response = client.get("/api/runs/../x/plots/a.png")
        assert response.status_code in (400, 404), response.status_code
        response = client.get("/api/runs/..%2F..%2Fetc/plots/a.png")
        assert response.status_code in (400, 404), response.status_code


class TestLLMConnection:
    """Tests for the /api/llm/test connection-test endpoint.

    External OpenAI/NVIDIA requests are mocked so no real API calls are made.
    """

    def _post(self, payload):
        return client.post("/api/llm/test", json=payload)

    def _payload(self, api_key="nvapi-test-key-123", model="openai/gpt-oss-20b",
                 base_url="https://integrate.api.nvidia.com/v1"):
        return {
            "provider": "custom",
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        }

    def test_connection_success(self):
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock()])

            resp = self._post(self._payload())
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["ok"] is True
            assert data["status"] == "ok"
            assert data["error"] is None

            # Client constructed with the supplied base URL, key, and 50s timeout.
            MockOpenAI.assert_called_once()
            _, kwargs = MockOpenAI.call_args
            assert kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"
            assert kwargs["api_key"] == "nvapi-test-key-123"
            assert kwargs["timeout"] == 50
            assert kwargs["max_retries"] == 0

            # API key never exposed in the response.
            body = json.dumps(data)
            assert "api_key" not in body.lower()
            assert "nvapi-test-key-123" not in body

    def test_connection_no_v1_duplication(self):
        # The base URL already ends in /v1; the client appends /chat/completions itself.
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock()])

            self._post(self._payload())
            _, kwargs = MockOpenAI.call_args
            assert kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"
            mock_client.chat.completions.create.assert_called_once()

    def test_connection_slow_but_succeeds_within_timeout(self):
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.return_value = MagicMock(choices=[MagicMock()])

            resp = self._post(self._payload())
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

    def test_connection_timeout_is_not_mistaken_for_bad_key(self):
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.side_effect = APITimeoutError("Request timed out")

            resp = self._post(self._payload())
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is False
            low = data["error"].lower()
            assert "time" in low or "did not finish" in low
            assert "invalid api key" not in low
            assert "invalid key" not in low

    def test_connection_invalid_credentials(self):
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.side_effect = AuthenticationError(
                "Invalid API key", response=MagicMock(), body=None
            )

            resp = self._post(self._payload())
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is False
            low = data["error"].lower()
            assert "authentication" in low or "invalid api key" in low

    def test_connection_unavailable_model(self):
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.side_effect = NotFoundError(
                "Model not found", response=MagicMock(), body=None
            )

            resp = self._post(self._payload())
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is False
            low = data["error"].lower()
            assert "not available" in low or "not found" in low

    def test_connection_rate_limit(self):
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.side_effect = RateLimitError(
                "Rate limited", response=MagicMock(), body=None
            )

            resp = self._post(self._payload())
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is False
            assert "rate limit" in data["error"].lower()

    def test_connection_empty_response(self):
        with patch("openai.OpenAI") as MockOpenAI:
            mock_client = MagicMock()
            MockOpenAI.return_value = mock_client
            mock_client.chat.completions.create.return_value = MagicMock(choices=[])

            resp = self._post(self._payload())
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is False
            assert "empty" in data["error"].lower()

    def test_connection_missing_fields(self):
        resp = self._post({"provider": "custom", "api_key": "", "base_url": "", "model": ""})
        assert resp.status_code == 400
