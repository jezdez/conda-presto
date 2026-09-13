# Policy evaluation and admission

Status: historical proposal, outside the current Presto delivery plan. Historical discussion: [#23](https://github.com/jezdez/conda-presto/issues/23).

The proposal asked how teams could apply rules for channels, signing identities and package metadata before accepting a resolved environment. Admission and framework-specific decisions belong to the consuming release workflow. That workflow chooses its policy and handles missing information, using existing advisory tools where needed.

The current optional {doc}`verification endpoint <../../reference/http-api>` checks a supplied artifact and bundle against the recipient's expected identity and issuer, with limited signing-step checks. It does not decide whether packages are suitable for an organization or whether installation should proceed.

Presto exposes conda operations to those consumers. A policy engine, release-approval service and mandatory evidence profile are outside its current delivery plan.
