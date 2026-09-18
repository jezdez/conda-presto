# Security and trust

The service selects packages and renders artifacts without applying a prefix transaction. It still reads channel metadata and processes user-controlled input, so the deployment must control who can spend its resources and access its configured channels.

## HTTP inputs and worker isolation

Request bodies, spec/channel/platform counts and parse/solve durations have limits. Uploaded files are parsed in a terminable process with bounded structure, restricted filenames and no server environment expansion. HTTP lockfile parsing avoids package URL fetches. The restricted transcoder reconstructs temporary export records from embedded metadata and rejects unsupported loss.

Requested channels are compared against exact resolved, credential-free allowed URLs. Wildcard admission permits HTTP and HTTPS, not arbitrary local files. Platform configuration is deliberate because conda context is process-global. Workers isolate solves and are replaced after failure.

## Deployment controls

The application does not authenticate HTTP callers. A reverse proxy or private ingress supplies TLS and caller authentication. CORS is only a browser access control and is disabled unless origins are configured. Bind the service to the intended private listener and trust forwarded headers only from known proxies.

Give the service explicit CPU, memory and storage limits. Metadata inspection cannot always be cancelled safely, so a configured solve timeout does not bound every possible dependency or filesystem stall.

## Storage and logs

Results with detected credentials in requests or package URLs are not retained. That detection is not a general secret classifier. Anyone who can write the persistent store can replace stored results, so cache files and Redis credentials need the same access controls as the deployment. File quotas and Redis memory/eviction settings bound aggregate retention.

HTTP logs omit query parameters, headers and bodies. Error handling and propagated logs redact known URL forms. The CLI server launcher disables uvicorn's separate raw access log.

## SBOMs, signing and verification

SBOMs describe resolved conda records, not package payload contents or a complete released product. Signing authenticates the exact limited statement constructed for a service-produced artifact. It does not establish the deferred detailed solve-construction claims. Verification checks the supplied bytes and bundle against the recipient's expected signer identity and issuer.

Operators choose signing and trust configuration explicitly. Public signing is opt-in, and a server must not fall back to interactive login. See {doc}`../reference/http-api` for supported statements and result fields.

See {doc}`../how-to/deploy-securely` for deployment steps and {doc}`../reference/docker-images` for image verification.
