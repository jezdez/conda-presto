# Lockfile transcoding

Status: available with format restrictions. Original discussion: [#11](https://github.com/jezdez/conda-presto/issues/11).

The original proposal addressed moving an already resolved environment between tools without running another solve. That workflow is available through the CLI and `POST /transcode`. See {doc}`../../how-to/transcode-lockfiles` for examples and {doc}`../../reference/http-api` for the supported requests.

HTTP conversion supports `conda-lock-v1` and `rattler-lock-v6`, including their registered aliases. It reads metadata from the uploaded file without solving or downloading package archives. It rejects unsupported fields, absent requested platforms and conversions that would change package selection or lose constraints. The CLI has a lockfile fast path, but can fall back to solving when its conditions are not met.

Conda-lockfiles owns conversion behavior. Presto's remaining work is to adopt a suitable released no-fetch conversion API and remove its temporary compatibility adapter. That change must preserve package identity, dependencies, platform selection and rejection of data the target format cannot represent. Format expansion belongs with the provider and is outside this plan.
