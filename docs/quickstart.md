# Quick start

## Install

`````{tab-set}

````{tab-item} pixi (global)
```bash
pixi global install --git https://github.com/jezdez/conda-presto.git
```
````

````{tab-item} conda
```bash
conda install -c conda-forge conda-presto
```
````

````{tab-item} From source
```bash
git clone https://github.com/jezdez/conda-presto.git
cd conda-presto
pixi install
```
````

`````

Requires conda >= 26.5 and Python >= 3.13. The conda install example
uses `conda-forge`, which carries the required stable conda solver
packages.

## First resolve

Resolve a couple of packages for a single platform:

```bash
conda presto -c conda-forge -p linux-64 python=3.12 numpy
```

This prints a JSON array with fully pinned packages including SHA256
hashes, URLs, sizes, and dependency lists.

## From an environment file

```bash
conda presto -f environment.yml -p linux-64 -p osx-arm64
```

Multiple platforms are solved in parallel.

## Output formats

Route the output through conda's exporter plugins:

```bash
conda presto -c conda-forge -p linux-64 --format explicit zlib
conda presto -f environment.yml --format pixi-lock-v6 > pixi.lock
```

See {doc}`reference/output-formats` for the full list of supported
formats.

## Start the HTTP server

```bash
conda presto --serve
```

The API is available at `http://localhost:8000` with interactive docs
at the root URL.

## Next steps

- {doc}`tutorials/cli-resolve` for in-depth CLI usage
- {doc}`tutorials/http-api` for HTTP API workflows
- {doc}`tutorials/ci-pipeline` for GitHub Action integration
- {doc}`reference/environment-variables` for tuning and configuration
