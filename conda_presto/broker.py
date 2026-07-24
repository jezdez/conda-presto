"""conda-broker provider for the local conda-presto HTTP service."""

from __future__ import annotations

import os
import sys
from contextlib import closing
from http.client import HTTPConnection, HTTPException
from ipaddress import ip_address
from urllib.parse import urlsplit

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
                "CONDA_NO_LOCK": "false",
            },
            grace_period_s=75,
        ),
    )


def main() -> None:
    """Exit successfully only when the service health endpoint is ready."""
    try:
        url = urlsplit(os.environ["CONDA_PRESTO_URL"])
        hostname = url.hostname
        if (
            url.scheme != "http"
            or hostname is None
            or (hostname != "localhost" and not ip_address(hostname).is_loopback)
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid broker service URL")
        with closing(HTTPConnection(hostname, url.port or 80, timeout=2)) as connection:
            connection.request("GET", "/health")
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise SystemExit(1)
    except (HTTPException, KeyError, OSError, ValueError):
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
