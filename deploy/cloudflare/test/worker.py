from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from js import Date, Request, Uint8Array
from js import Response as JSResponse
from workers import Request as WorkerRequest
from workers import Response, WorkerEntrypoint

from entry import Default as Application
from proxy import RETENTION_MS, handle_request, js_options

ORIGIN = "https://edge.example"
PATH = "/r/" + "a" * 64
SECOND = "/r/" + "b" * 64
KEY = "results/" + "a" * 64


def response(body, headers=None, status=200):
    return JSResponse.new(
        body, js_options({"headers": headers or {}, "status": status})
    )


async def content(result):
    return bytes(Uint8Array.new(await result.arrayBuffer()).to_py())


class Bucket:
    def __init__(self, binding):
        self.binding = binding
        self.writes = []
        self.reads = []
        self.fail_key = None
        self.old_upload = False
        self.wait = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def put(self, key, body, options):
        self.writes.append(key)
        if key == self.fail_key:
            raise RuntimeError("Storage write failed")
        if self.wait:
            self.started.set()
            await self.release.wait()
        return await self.binding.put(key, body, options)

    async def read(self, method, key):
        self.reads.append((method, key))
        obj = await getattr(self.binding, method)(key)
        if obj and self.old_upload:
            obj.uploaded = obj.uploaded - timedelta(days=2)
        return obj

    async def get(self, key):
        return await self.read("get", key)

    async def head(self, key):
        return await self.read("head", key)


class Native:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def fetch(self, request):
        self.calls.append(request)
        assert request.headers.get("Accept-Encoding") == "identity"
        return self.responses[urlsplit(request.url).path]


def solve(body="abc", headers=None):
    return Native(
        {
            "/resolve": response(body, {"Location": PATH, "X-Native": "preserved"}),
            PATH: response(
                body,
                headers
                if headers is not None
                else {"Content-Length": str(len(body.encode()))},
            ),
        }
    )


async def run_case(case, binding):
    await binding.delete([KEY, "results/" + "b" * 64])
    bucket = Bucket(binding)
    request = Request.new(ORIGIN + "/resolve")
    if case == "roundtrip":
        for media, body in (
            ("application/json", '[ { "platform": "linux-64", "packages": [] } ]\n'),
            ("application/yaml", "# café\nversion: 6\npackages: []"),
        ):
            native = solve(
                body, {"Content-Type": media, "Content-Length": str(len(body.encode()))}
            )
            result = await handle_request(request, bucket, native.fetch)
            assert result.headers.get("Location") == PATH
            assert await content(result) == body.encode()
            assert len(native.calls) == 2
            for method in ("GET", "HEAD"):
                retained = await handle_request(
                    Request.new(ORIGIN + PATH, js_options({"method": method})),
                    bucket,
                    native.fetch,
                )
                assert retained.status == 200
                assert retained.headers.get("Content-Type") == media
                assert retained.headers.get("Content-Length") == str(len(body.encode()))
                assert retained.headers.get("X-Presto-Result-Store") == "r2"
                assert await content(retained) == (
                    body.encode() if method == "GET" else b""
                )
                assert len(native.calls) == 2
    elif case == "forwarding":
        body = "dependencies:\n  - zlib\n"
        request = Request.new(
            ORIGIN + "/resolve?format=explicit",
            js_options(
                {
                    "method": "POST",
                    "body": body,
                    "headers": {"X-Client": "preserved", "Accept-Encoding": "gzip"},
                }
            ),
        )
        native = Native({"/resolve": response("credential-ineligible output")})
        result = await handle_request(request, bucket, native.fetch)
        forwarded = native.calls[0]
        assert forwarded.url == request.url and forwarded.method == "POST"
        assert forwarded.headers.get("X-Client") == "preserved"
        assert await forwarded.text() == body
        assert await content(result) == b"credential-ineligible output"
        assert not isinstance(result.headers.get("Location"), str)
        assert len(native.calls) == 1 and not bucket.writes
    elif case == "publication_waits":
        bucket.wait = True
        native = solve()
        pending = asyncio.create_task(handle_request(request, bucket, native.fetch))
        await bucket.started.wait()
        assert not pending.done()
        bucket.release.set()
        result = await pending
        assert result.headers.get("Location") == PATH
        assert await binding.head(KEY) is not None
        assert await content(result) == b"abc"
    elif case == "publication_failures":
        for headers in (
            {"Content-Length": "3"},
            {},
            {"Content-Length": "2"},
            {"Content-Length": "4"},
            {"Content-Length": "67108865"},
            {"Content-Length": "3", "Content-Encoding": "gzip"},
        ):
            await binding.delete(KEY)
            bucket.fail_key = KEY if headers == {"Content-Length": "3"} else None
            native = solve(headers=headers)
            result = await handle_request(request, bucket, native.fetch)
            assert result.status == 200
            assert not isinstance(result.headers.get("Location"), str)
            assert result.headers.get("X-Native") == "preserved"
            assert await content(result) == b"abc"
            assert await binding.head(KEY) is None
    elif case == "retention":
        native = Native({})
        for expiry, old in (
            ({}, False),
            ({"expiresAt": "invalid"}, False),
            ({"expiresAt": str(Date.now() - 1)}, False),
            ({"expiresAt": str(Date.now() + RETENTION_MS)}, True),
        ):
            await binding.put(KEY, "abc", {"customMetadata": expiry})
            bucket.old_upload = old
            for method in ("GET", "HEAD"):
                result = await handle_request(
                    Request.new(ORIGIN + PATH, js_options({"method": method})),
                    bucket,
                    native.fetch,
                )
                assert result.status == 404 and await content(result) == b""
        bucket.old_upload = False
        await binding.delete(KEY)
        for path in (PATH, "/r", "/r/no", "/r/" + "A" * 64):
            result = await handle_request(
                Request.new(ORIGIN + path), bucket, native.fetch
            )
            assert result.status == 404
        result = await handle_request(
            Request.new(ORIGIN + PATH, js_options({"method": "DELETE"})),
            bucket,
            native.fetch,
        )
        assert result.status == 405 and result.headers.get("Allow") == "GET, HEAD"
        await binding.put(
            KEY, "abc", {"customMetadata": {"expiresAt": str(Date.now() + 60_000)}}
        )
        result = await handle_request(Request.new(ORIGIN + PATH), bucket, native.fetch)
        age = int(
            result.headers.get("Cache-Control").split("max-age=")[1].split(",")[0]
        )
        assert 0 < age <= 60 and await content(result) == b"abc"
        assert not native.calls
    elif case in ("sbom_mixed", "sbom_unchanged"):
        documents = [{"content": "café\n", "sha256": "first", "location": PATH}]
        if case == "sbom_mixed":
            documents.extend(
                [
                    {"content": "other", "sha256": "second", "location": SECOND},
                    {"content": "ineligible", "sha256": "third"},
                ]
            )
            bucket.fail_key = "results/" + "b" * 64
        envelope = (
            json.dumps({"sboms": documents, "extra": "preserved"}, indent=2) + "\n"
        )
        native = Native(
            {
                "/sbom": response(envelope, {"ETag": "original"}),
                PATH: response("café\n", {"Content-Length": "6"}),
                SECOND: response("other", {"Content-Length": "5"}),
            }
        )
        result = await handle_request(
            Request.new(ORIGIN + "/sbom"), bucket, native.fetch
        )
        if case == "sbom_unchanged":
            assert await content(result) == envelope.encode()
            assert result.headers.get("ETag") == "original"
        else:
            del documents[1]["location"]
            assert json.loads(await content(result)) == {
                "sboms": documents,
                "extra": "preserved",
            }
            assert not isinstance(result.headers.get("ETag"), str)
            assert await binding.head("results/" + "b" * 64) is None
        assert await binding.head(KEY) is not None
    elif case == "sbom_invalid":
        for body in (
            "invalid",
            "null",
            '{"sboms":{}}',
            '{"sboms":[null]}',
            Uint8Array.new(8 * 1024 * 1024 + 1),
        ):
            native = Native({"/sbom": response(body)})
            result = await handle_request(
                Request.new(ORIGIN + "/sbom"), bucket, native.fetch
            )
            assert result.status == 502 and await content(result) == b""
            assert not bucket.writes
    elif case == "errors":

        async def fail(*_args):
            raise RuntimeError("Internal details must not be returned")

        result = await handle_request(request, bucket, fail)
        assert result.status == 502 and await content(result) == b""
        bucket.get = fail
        result = await handle_request(Request.new(ORIGIN + PATH), bucket, fail)
        assert result.status == 503 and await content(result) == b""
    elif case == "routing":

        class Namespace:
            def idFromName(self, name):
                self.name = name
                return name

            def get(self, _identity, options):
                self.hint = options.locationHint
                return self

            async def fetch(self, forwarded):
                return Response(
                    json.dumps(
                        {
                            "name": self.name,
                            "hint": self.hint,
                            "url": forwarded.url,
                            "method": forwarded.method,
                            "body": await forwarded.text(),
                        }
                    )
                )

        namespace = Namespace()
        app = SimpleNamespace(env=SimpleNamespace(PRESTO=namespace, RESULTS=binding))
        for selector in ("0", "1", "2", "100", "arbitrary", "0extra", "", "%30"):
            raw = Request.new(
                ORIGIN + f"/_instances/{selector}/resolve?format=explicit",
                js_options(
                    {
                        "method": "POST",
                        "body": '{"specs":["zlib"]}',
                    }
                ),
            )
            result = await Application.fetch(app, WorkerRequest(raw))
            if selector in ("0", "1"):
                observed = await result.json()
                assert observed["name"] == f"presto-{selector}"
                assert observed["hint"] == ("weur", "enam")[int(selector)]
                assert observed["url"] == ORIGIN + "/resolve?format=explicit"
                assert observed["method"] == "POST"
                assert observed["body"] == '{"specs":["zlib"]}'
            else:
                assert result.status == 404
    else:
        raise ValueError("Unknown proxy test")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        case = urlsplit(request.url).path.removeprefix("/")
        if case == "__health":
            return Response("ready")
        try:
            async with asyncio.timeout(20):
                await run_case(case, self.env.RESULTS)
            return Response("passed")
        except Exception as exc:
            return Response(f"{case}: {type(exc).__name__}: {exc}", status=500)
