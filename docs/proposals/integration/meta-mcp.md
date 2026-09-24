# conda-meta-mcp integration

Status: upstream opportunity, outside Presto's delivery plan.

An agent using conda-meta-mcp for package metadata may also need to check whether requirements solve and retrieve a resolved artifact. Evaluate that workflow in the existing project, using conda-presto's HTTP API.

Start with one concrete consumer request. The integration would need to preserve platform selection, per-platform solve errors, exporter output and service timeouts. Endpoint configuration and authentication should follow the consumer project's conventions. Available formats and operations can be discovered from the service.

Scope and acceptance in conda-meta-mcp remain to be established. This opportunity does not call for another wrapper package or a native `/mcp` endpoint in Presto, and it is separate from the next service milestone.

See {doc}`../../reference/http-api` for existing operations and {doc}`../../how-to/deploy-securely` for deployment considerations.
