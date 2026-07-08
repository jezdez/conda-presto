# Run conda-presto in Streamlit

Streamlit can host a small UI that starts the conda-presto HTTP server on localhost and calls the same `/resolve` API that a separate client would use. This keeps the Streamlit app on the documented HTTP contract instead of duplicating solver behavior in the UI.

Streamlit Community Cloud does not use Pixi environments. Put a supported `environment.yml` in the repository root or next to the Streamlit entrypoint so Community Cloud installs the conda packages it needs.

## Add a Streamlit environment

Use a root-level `environment.yml`:

```yaml
name: conda-presto-streamlit
channels:
  - conda-forge
  - nodefaults
dependencies:
  - python >=3.13,<3.14
  - streamlit
  - conda >=26.5,<27
  - conda-rattler-solver >=0.1.1,<0.2
  - conda-lockfiles >=0.2.0
  - msgspec >=0.19
  - litestar >=2.18
  - pyjwt >=2.0
  - uvicorn >=0.34
  - brotli-python >=1.1
  - httpx >=0.28
  - pip
  - pip:
      - -e .
```

The `nodefaults` channel entry keeps this environment on conda-forge instead of mixing in `defaults` / `main`, which can lag conda-forge for beta solver packages such as `conda-rattler-solver`.

The `pip: -e .` line is intentional. The Streamlit app starts `uvicorn conda_presto.app:app`, and `conda_presto.app` reads `importlib.metadata.version("conda-presto")` during import for the version endpoint and OpenAPI metadata. An editable install gives the app package metadata while still running from the repository checkout.

Do not add a `packages.txt` file unless the app needs Debian packages installed with `apt-get`. conda-presto does not need external services for this Streamlit UI, so Redis is unnecessary unless the app adds Redis-specific caching or queueing code.

## Add a Streamlit entrypoint

Create `streamlit_app.py` in the repository root:

```python
from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time

import httpx
import streamlit as st


API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("CONDA_PRESTO_STREAMLIT_API_PORT", "8765"))
API_URL = f"http://{API_HOST}:{API_PORT}"


@st.cache_resource
def start_conda_presto() -> subprocess.Popen[bytes] | None:
    if api_is_ready():
        return None

    env = os.environ.copy()
    env.setdefault("CONDA_PRESTO_CONCURRENCY", "1")
    env.setdefault("CONDA_PRESTO_WORKERS", "1")
    env.setdefault("CONDA_PRESTO_PLATFORMS", "linux-64")

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
        ],
        env=env,
    )
    atexit.register(process.terminate)

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("conda-presto server exited during startup")
        if api_is_ready():
            return process
        time.sleep(0.5)

    process.terminate()
    raise RuntimeError("conda-presto server did not become ready")


def api_is_ready() -> bool:
    try:
        response = httpx.get(f"{API_URL}/health", timeout=1)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


@st.cache_data(show_spinner=False)
def resolve(
    channels: tuple[str, ...],
    specs: tuple[str, ...],
    platforms: tuple[str, ...],
) -> list[dict[str, object]]:
    response = httpx.post(
        f"{API_URL}/resolve",
        json={
            "channels": list(channels),
            "specs": list(specs),
            "platforms": list(platforms),
        },
        timeout=90,
    )
    response.raise_for_status()
    return response.json()


def response_json(packages: object) -> str:
    import json

    return json.dumps(packages, indent=2)


st.set_page_config(page_title="conda-presto", layout="wide")
st.title("conda-presto")

with st.spinner("Starting conda-presto"):
    start_conda_presto()

with st.form("resolve"):
    specs_text = st.text_area(
        "Package specs",
        value="python=3.13\nnumpy",
        height=140,
    )
    channels_text = st.text_input("Channels", value="conda-forge")
    platforms = st.multiselect(
        "Platforms",
        ["linux-64", "osx-arm64", "osx-64", "linux-aarch64"],
        default=["linux-64"],
    )
    submitted = st.form_submit_button("Resolve")

if submitted:
    specs = tuple(
        line.strip()
        for line in specs_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    channels = tuple(
        channel.strip()
        for channel in channels_text.split(",")
        if channel.strip()
    )

    if not specs:
        st.error("Enter at least one package spec.")
        st.stop()
    if not channels:
        st.error("Enter at least one channel.")
        st.stop()
    if not platforms:
        st.error("Select at least one platform.")
        st.stop()

    with st.spinner("Solving environment"):
        results = resolve(channels, specs, tuple(platforms))

    for result in results:
        platform = result["platform"]
        st.subheader(str(platform))
        if result["error"]:
            st.error(str(result["error"]))
            continue
        packages = result["packages"]
        st.dataframe(packages, use_container_width=True)
        st.download_button(
            f"Download {platform} JSON",
            data=response_json(packages),
            file_name=f"{platform}.json",
            mime="application/json",
        )
```

The form prevents Streamlit from starting a new solve on every widget change. `st.cache_resource` starts one localhost API process per Streamlit runtime, and `st.cache_data` reuses responses for repeated solves with the same channels, specs, and platforms.

The example sets `CONDA_PRESTO_CONCURRENCY=1`, `CONDA_PRESTO_WORKERS=1`, and `CONDA_PRESTO_PLATFORMS=linux-64` for the subprocess. Those defaults keep startup and warmup modest on Community Cloud. Increase them for a larger host.

## Run locally

From the repository root:

```bash
conda env create --file environment.yml
conda activate conda-presto-streamlit
streamlit run streamlit_app.py
```

## Deploy on Community Cloud

Create a Streamlit Community Cloud app from the GitHub repository and select `streamlit_app.py` as the entrypoint. Community Cloud initializes the app from the repository root and installs dependencies from the first supported dependency file it finds. In this repository, `environment.yml` takes precedence over `pyproject.toml`, so the conda runtime is used.

Keep the app modest on Community Cloud. Multi-platform solves build repodata indexes and may use more CPU and memory than a typical Streamlit dashboard. Prefer one or two default platforms and let users opt into larger solves deliberately.

For a production UI, run the Litestar HTTP API as a separate service and make Streamlit call that service instead of starting a localhost subprocess. That keeps server lifecycle, scaling, rate limits, request caps, and timeout handling outside the Streamlit process.
