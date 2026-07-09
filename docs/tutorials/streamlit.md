# Run conda-presto in Streamlit

The repository includes a deployable Streamlit test app at `streamlit_app.py`. The app starts the real conda-presto HTTP server on localhost and calls the same `/resolve` API that other clients use, so the UI stays on the documented HTTP contract instead of duplicating solver behavior.

Streamlit Community Cloud does not use Pixi environments. The root `environment.yml` gives Community Cloud the conda packages it needs, including `streamlit`, `uvicorn`, conda's solver packages, and an editable install of this checkout.

## Run locally with Pixi

From the repository root:

```bash
pixi run -e streamlit streamlit
```

This uses the `streamlit` Pixi environment, which combines the server dependencies with Streamlit and `httpx`.

## Run locally with conda

From the repository root:

```bash
conda env create --file environment.yml
conda activate conda-presto-streamlit
streamlit run streamlit_app.py
```

The `pip: -e .` line in `environment.yml` is intentional. The Streamlit app starts `uvicorn conda_presto.app:app`, and `conda_presto.app` reads `importlib.metadata.version("conda-presto")` during import for the version endpoint and OpenAPI metadata. An editable install gives the app package metadata while still running from the repository checkout.

## Deploy on Community Cloud

Create a Streamlit Community Cloud app from the GitHub repository and select `streamlit_app.py` as the entrypoint. Community Cloud initializes the app from the repository root and installs dependencies from the first supported dependency file it finds. In this repository, `environment.yml` takes precedence over `pyproject.toml`, so the conda runtime is used.

Do not add a `packages.txt` file unless the app needs Debian packages installed with `apt-get`. conda-presto does not need external services for this Streamlit UI, so Redis is unnecessary unless the app adds Redis-specific caching or queueing code.

## Runtime behavior

`streamlit_app.py` starts one localhost API process per Streamlit runtime with `st.cache_resource`. It defaults to:

- `CONDA_PRESTO_CONCURRENCY=1`
- `CONDA_PRESTO_WORKERS=1`
- `CONDA_PRESTO_PLATFORMS=linux-64`

Those defaults keep startup and warmup modest on Community Cloud. Increase them for a larger host.

Set `CONDA_PRESTO_STREAMLIT_API_URL` to point the UI at an already-running conda-presto API instead of starting a localhost subprocess.

For a production UI, run the Litestar HTTP API as a separate service and make Streamlit call that service. That keeps server lifecycle, scaling, rate limits, request caps, and timeout handling outside the Streamlit process.
