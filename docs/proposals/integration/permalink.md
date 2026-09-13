# Retained result URLs

Status: available in the current checkout. Original tracking issue: [#19](https://github.com/jezdez/conda-presto/issues/19).

Retained result URLs let callers share the exact output of a solve and retrieve it without repeating solver work. They also let clients cache an immutable response while the service continues to check metadata freshness for new resolve requests.

A retained response advertises `Location: /r/<key>`. The key identifies the exact response bytes and media type. A separate request lookup includes solve inputs, dependency versions, exporter identity, and metadata state. Identical inputs therefore do not promise the same output URL after channel updates or a changed render.

Retrieval returns the retained bytes without solving or checking current repodata. Memory capacity, persistent-store expiry, and eviction limit availability. An expired or evicted result returns HTTP 404. Archive the output separately when it must remain available. Requests or outputs containing detected credentials bypass retention.

See {doc}`../../reference/cache` for identity and retention rules and {doc}`../../how-to/configure-result-cache` for memory, file, and Redis configuration.
