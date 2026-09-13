# conda-presto

conda-presto exposes conda solving, input parsing and output rendering through an HTTP service. Other systems can request fully resolved packages for explicit target platforms without installing conda or changing an environment themselves.

Start with the {doc}`quickstart`, then follow the {doc}`tutorials/http-api` tutorial. The package also provides a one-shot CLI and a server launcher. The GitHub Action calls an explicitly configured service.

```{toctree}
:maxdepth: 2

quickstart
tutorials/index
how-to/index
reference/index
explanation/index
proposals
changelog
```

```{toctree}
:caption: Design records
:hidden:
:glob:

adr/*
```
