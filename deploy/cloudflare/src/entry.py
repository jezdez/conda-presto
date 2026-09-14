from __future__ import annotations

import asyncio
import json
import re
import secrets
from urllib.parse import urlsplit, urlunsplit

from js import AbortController, AbortSignal, Headers, Request, TextDecoder
from pyodide.ffi import to_js
from workers import DurableObject, Response, WorkerEntrypoint

from proxy import handle_request, js_options, unavailable


class PrestoContainer(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.start_lock = asyncio.Lock()
        self.ready = False
        self.location_observed = False
        self.native_location = None

    async def ensure_ready(self):
        async with self.start_lock:
            container = self.ctx.container
            if not container.running:
                self.ready = False
                self.location_observed = False
                self.native_location = None
                container.start(
                    js_options(
                        {
                            "enableInternet": True,
                            "env": {
                                "CONDA_PRESTO_CHANNELS": self.env.CONDA_PRESTO_CHANNELS,
                                "CONDA_PRESTO_ALLOWED_CHANNELS": (
                                    self.env.CONDA_PRESTO_CHANNELS
                                ),
                                "CONDA_PRESTO_PLATFORMS": (
                                    self.env.CONDA_PRESTO_PLATFORMS
                                ),
                                "CONDA_PRESTO_RESULT_CACHE_BACKEND": "memory",
                                "CONDA_PRESTO_PERSISTENT_WORKER": "true",
                                "CONDA_PRESTO_CONCURRENCY": "1",
                                "CONDA_PRESTO_WORKERS": "1",
                            },
                        },
                    )
                )
            await container.setInactivityTimeout(600_000)
            if self.ready:
                return
            async with asyncio.timeout(30):
                while container.running:
                    try:
                        response = await container.getTcpPort(8000).fetch(
                            "http://container/health",
                            signal=AbortSignal.timeout(2000),
                        )
                        await response.body.cancel()
                        if response.status == 200:
                            self.ready = True
                            return
                    except Exception:
                        pass
                    await asyncio.sleep(0.25)
                raise RuntimeError("Container stopped during startup")

    async def observe_location(self):
        abort = AbortController.new()
        try:
            async with asyncio.timeout(2):
                process = await self.ctx.container.exec(
                    to_js(
                        [
                            "/app/entrypoint.sh",
                            "python",
                            "-c",
                            "import json, os; "
                            "print(json.dumps({k: os.getenv(k) for k in "
                            "['CLOUDFLARE_LOCATION', "
                            "'CLOUDFLARE_DURABLE_OBJECT_ID']}))",
                        ]
                    ),
                    signal=abort.signal,
                )
                output = await process.output()
                if output.exitCode != 0:
                    return
                observation = json.loads(TextDecoder.new().decode(output.stdout))
                location = observation.get("CLOUDFLARE_LOCATION")
                if (
                    observation.get("CLOUDFLARE_DURABLE_OBJECT_ID")
                    == self.ctx.id.toString()
                    and isinstance(location, str)
                    and re.fullmatch(r"[a-zA-Z0-9-]{1,64}", location)
                ):
                    self.native_location = location
        except Exception:
            pass
        finally:
            abort.abort()
            self.location_observed = True

    async def fetch(self, request):
        try:
            await self.ensure_ready()
            raw = request.js_object
            url = urlsplit(raw.url)._replace(scheme="http")
            response = await self.ctx.container.getTcpPort(8000).fetch(
                Request.new(urlunsplit(url), raw)
            )
            if not self.location_observed:
                await self.observe_location()
            headers = Headers.new(response.headers)
            headers.set("X-Presto-Instance", self.ctx.id.toString())
            if self.native_location:
                headers.set("X-Presto-Container-Location", self.native_location)
            return Response(response.body, status=response.status, headers=headers)
        except Exception:
            return Response(status=502, headers={"Cache-Control": "no-store"})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        raw = request.js_object
        url = urlsplit(raw.url)
        selected = secrets.randbelow(2)
        if url.path.startswith("/_instances/"):
            match = re.fullmatch(r"/_instances/([01])(/.*)?", url.path)
            if not match:
                return Response(unavailable(404))
            selected = int(match[1])
            raw = Request.new(urlunsplit(url._replace(path=match[2] or "/")), raw)
        instance = self.env.PRESTO.get(
            self.env.PRESTO.idFromName(f"presto-{selected}"),
            js_options({"locationHint": ("weur", "enam")[selected]}),
        )

        async def fetch_container(forwarded):
            return (await instance.fetch(forwarded)).js_object

        return Response(await handle_request(raw, self.env.RESULTS, fetch_container))
