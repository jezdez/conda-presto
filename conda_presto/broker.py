"""conda-broker provider for the local conda-presto HTTP service."""

from __future__ import annotations

import os
import sys
from urllib.request import urlopen

from conda_broker.hookspec import hookimpl
from conda_broker.models import CondaService, EndpointSpec, HealthCheck, ProcessSpec


@hookimpl
def conda_broker_services():
    """Expose the opt-in local conda-presto service."""
    yield CondaService(
        name="conda-presto.server",
        summary="Broker-managed local conda-presto HTTP API",
        source="conda-presto",
        start_policy="manual",
        restart_policy="on-failure",
        endpoints=(
            EndpointSpec(
                protocol="http",
                path="/",
                port_env="CONDA_PRESTO_PORT",
                url_env="CONDA_PRESTO_URL",
            ),
        ),
        health_check=HealthCheck(
            type="exec",
            command=(sys.executable, "-m", "conda_presto.broker"),
            interval_s=2,
            timeout_s=2,
            start_period_s=120,
        ),
        process=ProcessSpec(
            argv=(sys.executable, "-m", "conda_presto.cli", "--serve"),
            env={
                "CONDA_PRESTO_HOST": "127.0.0.1",
                "CONDA_PRESTO_CONCURRENCY": "1",
                "CONDA_PRESTO_RATE_LIMIT": "0",
                "CONDA_PRESTO_PERSISTENT_WORKER": "1",
            },
        ),
    )


def main() -> None:
    """Exit successfully only when the service health endpoint is ready."""
    url = f"{os.environ['CONDA_PRESTO_URL'].rstrip('/')}/health"
    try:
        with urlopen(url, timeout=2) as response:
            if not 200 <= response.status < 400:
                raise SystemExit(1)
    except (KeyError, OSError, ValueError):
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
