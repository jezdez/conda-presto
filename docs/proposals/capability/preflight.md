# Prospective-input diagnostics

Status: upstream opportunity, outside Presto's delivery plan. Original discussion: [#16](https://github.com/jezdez/conda-presto/issues/16).

This combines the original lint and preflight ideas: useful feedback about environment-file mistakes, invalid requirements and unavailable packages before attempting a solve. Reusable diagnostics belong with conda's parsers and environment-specifier plugins, with doctor integration where appropriate. Doctor currently checks installed prefixes. Diagnosing prospective environments would require upstream support.

Presto removed `/preflight`. Its current request validation and `POST /parse` remain documented in {doc}`../../reference/http-api`.

An upstream proposal should distinguish checks on supplied text from checks requiring channel metadata. It should preserve channel order and valid unconstrained requirements. Package availability depends on the selected platform and metadata, and does not prove the full request is solvable. Source locations depend on parser support.

If a consumer needs HTTP access to a future provider operation, Presto could supply execution, configured access restrictions, request limits and response handling.
