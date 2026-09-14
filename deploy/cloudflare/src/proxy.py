from __future__ import annotations

import asyncio
import re

from js import (
    JSON,
    URL,
    AbortController,
    Array,
    Boolean,
    Date,
    FixedLengthStream,
    Headers,
    Number,
    Object,
    Request,
    Response,
    TextDecoder,
    Uint8Array,
)
from pyodide.ffi import to_js

RETENTION_MS = 24 * 60 * 60 * 1000
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_SBOM_RESPONSE_BYTES = 8 * 1024 * 1024
RESULT_PATH = re.compile(r"/r/([0-9a-f]{64})")


def js_options(value):
    return to_js(value, dict_converter=Object.fromEntries)


def unavailable(status, allow=None):
    headers = {"Cache-Control": "no-store"}
    if allow:
        headers["Allow"] = allow
    return Response.new(None, js_options({"status": status, "headers": headers}))


async def handle_request(request, bucket, fetch_container):
    """Forward native requests and publish eligible retained outputs to R2."""
    url = URL.new(request.url)
    if url.pathname == "/r" or url.pathname.startswith("/r/"):
        if request.method not in ("GET", "HEAD"):
            return unavailable(405, "GET, HEAD")
        match = RESULT_PATH.fullmatch(url.pathname)
        if not match:
            return unavailable(404)
        try:
            key = f"results/{match[1]}"
            obj = await (
                bucket.head(key) if request.method == "HEAD" else bucket.get(key)
            )
            if obj is None:
                return unavailable(404)
            metadata = obj.customMetadata or {}
            remaining = (
                min(
                    Number(metadata.get("expiresAt")),
                    obj.uploaded.timestamp() * 1000 + RETENTION_MS,
                )
                - Date.now()
            )
            body = getattr(obj, "body", None)
            if not Number.isFinite(remaining) or remaining <= 0:
                if body is not None:
                    await body.cancel()
                return unavailable(404)
            return Response.new(
                None if request.method == "HEAD" else body,
                js_options(
                    {
                        "headers": {
                            "Content-Type": (obj.httpMetadata or {}).get("contentType")
                            or "application/octet-stream",
                            "Content-Length": str(obj.size),
                            "Cache-Control": (
                                f"public, max-age={int(remaining // 1000)}, immutable"
                            ),
                            "X-Presto-Result-Store": "r2",
                        }
                    }
                ),
            )
        except Exception:
            return unavailable(503)

    try:
        headers = Headers.new(request.headers)
        headers.set("Accept-Encoding", "identity")
        response = await fetch_container(
            Request.new(request, js_options({"headers": headers}))
        )
    except Exception:
        return unavailable(502)
    if not response.ok:
        return response

    async def publish(location):
        match = RESULT_PATH.fullmatch(location)
        if not match:
            return False
        try:
            permalink = await fetch_container(
                Request.new(
                    URL.new(location, url),
                    js_options({"headers": {"Accept-Encoding": "identity"}}),
                )
            )
            raw_length = permalink.headers.get("Content-Length")
            length = Number(raw_length)
            encoding = permalink.headers.get("Content-Encoding")
            has_body = Boolean(permalink.body)
            if not (
                permalink.ok
                and isinstance(raw_length, str)
                and re.fullmatch(r"[0-9]+", raw_length)
                and Number.isSafeInteger(length)
                and length <= MAX_RESULT_BYTES
                and (not Boolean(encoding) or encoding == "identity")
                and (has_body or length == 0)
            ):
                if has_body:
                    await permalink.body.cancel()
                return False
            media_type = permalink.headers.get("Content-Type")
            options = {
                "httpMetadata": {
                    "contentType": media_type
                    if Boolean(media_type)
                    else "application/octet-stream",
                },
                "customMetadata": {"expiresAt": str(Date.now() + RETENTION_MS)},
            }
            key = f"results/{match[1]}"
            if not has_body:
                stored = await bucket.put(key, Uint8Array.new(0), options)
            else:
                stream = FixedLengthStream.new(length)
                abort = AbortController.new()
                transfer = permalink.body.pipeTo(
                    stream.writable,
                    js_options({"signal": abort.signal}),
                )
                try:
                    stored, _ = await asyncio.gather(
                        bucket.put(key, stream.readable, options),
                        transfer,
                    )
                finally:
                    abort.abort()
            return stored is not None
        except Exception:
            return False

    if url.pathname == "/sbom":
        try:
            encoding = response.headers.get("Content-Encoding")
            if (
                Boolean(encoding)
                and encoding != "identity"
                or Number(response.headers.get("Content-Length"))
                > MAX_SBOM_RESPONSE_BYTES
            ):
                if Boolean(response.body):
                    await response.body.cancel()
                return unavailable(502)
            if not Boolean(response.body):
                return unavailable(502)
            reader = response.body.getReader()
            chunks = []
            size = 0
            try:
                while True:
                    chunk = await reader.read()
                    if chunk.done:
                        break
                    size += chunk.value.byteLength
                    if size > MAX_SBOM_RESPONSE_BYTES:
                        await reader.cancel()
                        return unavailable(502)
                    chunks.append(chunk.value)
            finally:
                reader.releaseLock()
            body = Uint8Array.new(size)
            offset = 0
            for chunk in chunks:
                body.set(chunk, offset)
                offset += chunk.byteLength
            payload = JSON.parse(
                TextDecoder.new(
                    "utf-8",
                    js_options({"fatal": True, "ignoreBOM": False}),
                ).decode(body)
            )
            sboms = getattr(payload, "sboms", None)
            if (
                Object.prototype.toString.call(payload) != "[object Object]"
                or not Array.isArray(sboms)
                or any(
                    Object.prototype.toString.call(item) != "[object Object]"
                    for item in sboms
                )
            ):
                return unavailable(502)
            changed = False
            for item in sboms:
                if Object.hasOwn(item, "location") and (
                    not isinstance(item.location, str)
                    or not await publish(item.location)
                ):
                    del item.location
                    changed = True
            headers = Headers.new(response.headers)
            for name in ("Content-Length", "Content-Encoding", "Location"):
                headers.delete(name)
            if changed:
                for name in ("ETag", "Content-MD5"):
                    headers.delete(name)
            return Response.new(
                JSON.stringify(payload) if changed else body,
                js_options(
                    {
                        "status": response.status,
                        "statusText": response.statusText,
                        "headers": headers,
                    }
                ),
            )
        except Exception:
            return unavailable(502)

    location = response.headers.get("Location")
    if not isinstance(location, str) or await publish(location):
        return response
    headers = Headers.new(response.headers)
    headers.delete("Location")
    return Response.new(
        response.body,
        js_options(
            {
                "status": response.status,
                "statusText": response.statusText,
                "headers": headers,
            }
        ),
    )
