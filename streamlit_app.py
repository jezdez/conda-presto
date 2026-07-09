from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
from typing import Any

import httpx
import streamlit as st

API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("CONDA_PRESTO_STREAMLIT_API_PORT", "8765"))
API_URL = os.environ.get(
    "CONDA_PRESTO_STREAMLIT_API_URL", f"http://{API_HOST}:{API_PORT}"
)
LOCAL_API = "CONDA_PRESTO_STREAMLIT_API_URL" not in os.environ
DEFAULT_PLATFORMS = ("linux-64",)
PLATFORM_OPTIONS = ("linux-64", "linux-aarch64", "osx-64", "osx-arm64")


def api_is_ready() -> bool:
    try:
        response = httpx.get(f"{API_URL}/health", timeout=1)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()


@st.cache_resource
def start_conda_presto() -> subprocess.Popen[bytes] | None:
    if api_is_ready():
        return None
    if not LOCAL_API:
        raise RuntimeError(f"conda-presto API is not ready at {API_URL}")

    env = os.environ.copy()
    env.setdefault("CONDA_PRESTO_CONCURRENCY", "1")
    env.setdefault("CONDA_PRESTO_WORKERS", "1")
    env.setdefault("CONDA_PRESTO_PLATFORMS", ",".join(DEFAULT_PLATFORMS))

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "conda_presto.app:app",
            "--host",
            API_HOST,
            "--port",
            str(API_PORT),
            "--log-level",
            os.environ.get("CONDA_PRESTO_STREAMLIT_LOG_LEVEL", "warning"),
        ],
        env=env,
    )
    atexit.register(terminate, process)

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("conda-presto server exited during startup")
        if api_is_ready():
            return process
        time.sleep(0.5)

    terminate(process)
    raise RuntimeError("conda-presto server did not become ready")


@st.cache_data(show_spinner=False)
def version_info() -> dict[str, str]:
    response = httpx.get(f"{API_URL}/version", timeout=10)
    response.raise_for_status()
    return response.json()


@st.cache_data(show_spinner=False)
def resolve(
    channels: tuple[str, ...],
    specs: tuple[str, ...],
    platforms: tuple[str, ...],
) -> list[dict[str, Any]]:
    response = httpx.post(
        f"{API_URL}/resolve",
        json={
            "channels": list(channels),
            "specs": list(specs),
            "platforms": list(platforms),
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def split_entries(text: str, *, comma: bool = False) -> tuple[str, ...]:
    entries: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(",") if comma else [line]
        entries.extend(part.strip() for part in parts if part.strip())
    return tuple(entries)


def result_json(result: object) -> str:
    return json.dumps(result, indent=2, sort_keys=True)


def main() -> None:
    st.set_page_config(page_title="conda-presto", layout="wide")
    st.title("conda-presto")

    try:
        with st.spinner("Starting conda-presto"):
            start_conda_presto()
    except RuntimeError as exc:
        st.error(str(exc))
        st.stop()

    try:
        versions = version_info()
    except httpx.HTTPError as exc:
        st.error(f"conda-presto API is unavailable: {exc}")
        st.stop()

    with st.sidebar:
        st.caption("API")
        st.code(API_URL, language="text")
        for name, version in versions.items():
            st.text(f"{name}: {version}")

    with st.form("resolve"):
        specs_text = st.text_area(
            "Package specs",
            value="python=3.13\nnumpy",
            height=140,
        )
        channels_text = st.text_area("Channels", value="conda-forge", height=80)
        platforms = st.multiselect(
            "Platforms",
            PLATFORM_OPTIONS,
            default=list(DEFAULT_PLATFORMS),
        )
        submitted = st.form_submit_button("Resolve")

    if not submitted:
        return

    specs = split_entries(specs_text)
    channels = split_entries(channels_text, comma=True)

    if not specs:
        st.error("Enter at least one package spec.")
        st.stop()
    if not channels:
        st.error("Enter at least one channel.")
        st.stop()
    if not platforms:
        st.error("Select at least one platform.")
        st.stop()

    try:
        with st.spinner("Solving environment"):
            results = resolve(channels, specs, tuple(platforms))
    except httpx.HTTPStatusError as exc:
        st.error(f"Resolve request failed: {exc.response.text}")
        st.stop()
    except httpx.HTTPError as exc:
        st.error(f"Resolve request failed: {exc}")
        st.stop()

    for result in results:
        platform = str(result.get("platform", "unknown"))
        st.subheader(platform)
        if error := result.get("error"):
            st.error(str(error))
            continue

        packages = result.get("packages") or []
        st.dataframe(packages, use_container_width=True)
        st.download_button(
            f"Download {platform} JSON",
            data=result_json(result),
            file_name=f"conda-presto-{platform}.json",
            mime="application/json",
        )


if __name__ == "__main__":
    main()
