"""`render.yaml` and the deploy job are the deployment contract, so they are asserted here.

Each row of the blueprint table in `docs/spec/deployment.md`, and the two invariants behind
them: the service stays one instance (ADR-031), and it names no variable the code does not
read. Model names in the blueprint have to equal `.env.example`, so a model is changed in one
place and the example file stays the single description of the environment.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from port_tariff_agent.settings import Settings

ROOT = Path(__file__).resolve().parents[1]

SETTING_NAMES = {name.upper() for name in Settings.model_fields}
REQUIRED = {name.upper() for name, field in Settings.model_fields.items() if field.is_required()}
SECRETS = {
    name.upper() for name, field in Settings.model_fields.items() if field.annotation is SecretStr
}


def env_example() -> dict[str, str]:
    """Every `NAME=value` line of `.env.example`, comments excluded."""
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    pairs = (m.groups() for line in lines if (m := re.match(r"^([A-Z0-9_]+)=(.*)$", line)))
    return {name: value.strip() for name, value in pairs}


@pytest.fixture(scope="module")
def service() -> dict[str, Any]:
    services = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))["services"]
    assert len(services) == 1, "one service, one instance"
    return services[0]


@pytest.fixture(scope="module")
def deploy_job() -> dict[str, Any]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["deploy"]


class TestBlueprint:
    def test_one_free_docker_web_service(self, service: dict[str, Any]) -> None:
        assert service["type"] == "web"
        assert service["runtime"] == "docker"
        assert service["plan"] == "free"

    def test_it_stays_one_instance_with_one_worker(self, service: dict[str, Any]) -> None:
        assert service.get("numInstances", 1) == 1
        assert "--workers 1" in service["dockerCommand"]

    def test_ci_fires_the_deploy_not_the_push(self, service: dict[str, Any]) -> None:
        assert service["autoDeploy"] is False

    def test_the_probe_is_the_endpoint_that_never_calls_a_model(
        self, service: dict[str, Any]
    ) -> None:
        assert service["healthCheckPath"] == "/health"

    def test_the_command_hands_the_revision_to_the_app(self, service: dict[str, Any]) -> None:
        assert "APP_REVISION=$RENDER_GIT_COMMIT" in service["dockerCommand"]

    def test_the_command_carries_no_quotes(self, service: dict[str, Any]) -> None:
        """Render passes the text after `-c` verbatim; a quote makes the script one word."""
        command = service["dockerCommand"]

        assert command.startswith("/bin/sh -c ")
        assert "'" not in command and '"' not in command

    def test_every_variable_is_a_setting(self, service: dict[str, Any]) -> None:
        names = {var["key"] for var in service["envVars"]}

        assert names - SETTING_NAMES == set()

    def test_every_required_setting_is_provided(self, service: dict[str, Any]) -> None:
        """Otherwise the instance boots unconfigured and `/health` says degraded."""
        names = {var["key"] for var in service["envVars"]}

        assert REQUIRED - names == set()

    def test_secrets_are_set_in_the_dashboard_not_in_the_file(
        self, service: dict[str, Any]
    ) -> None:
        secrets = [var for var in service["envVars"] if var["key"] in SECRETS]

        assert secrets, "the blueprint has to name the secret so the dashboard prompts for it"
        for var in secrets:
            assert var.get("sync") is False
            assert "value" not in var

    def test_other_values_equal_the_env_example(self, service: dict[str, Any]) -> None:
        example = env_example()
        values = {
            var["key"]: str(var["value"]) for var in service["envVars"] if var["key"] not in SECRETS
        }

        assert values
        assert values == {name: example[name] for name in values}


class TestDeployJob:
    def test_it_runs_after_the_checks(self, deploy_job: dict[str, Any]) -> None:
        needs = deploy_job["needs"]

        assert needs == "check" or "check" in needs

    def test_it_is_gated_on_main_and_on_a_configured_url(self, deploy_job: dict[str, Any]) -> None:
        condition = deploy_job["if"]

        assert "refs/heads/main" in condition
        assert "vars.LIVE_URL" in condition

    def test_deploys_queue_rather_than_cancel_each_other(self, deploy_job: dict[str, Any]) -> None:
        assert deploy_job["concurrency"]["cancel-in-progress"] is False

    def test_it_waits_for_the_revision_not_for_a_status_code(
        self, deploy_job: dict[str, Any]
    ) -> None:
        scripts = "\n".join(step.get("run", "") for step in deploy_job["steps"])

        assert "GITHUB_SHA" in scripts
        assert ".revision" in scripts
        assert "/health" in scripts
