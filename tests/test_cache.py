"""Tests for result-cache storage and identity."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import anyio
import msgspec
import pytest
from conda.models.channel import Channel
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore

import conda_presto.cache as cache_module
from conda_presto.cache import ResolveCacheContext, ResultCache, StoredResult
from conda_presto.resolve import RepodataSnapshot
from conda_presto.storage import StoreOperationCoordinator
from conda_presto.workspace_lock import WorkspaceLockInput


@pytest.fixture()
def resolve_cache_state():
    return {
        "repodata": RepodataSnapshot((), False),
        "solve_context": ResolveCacheContext(
            channel_priority="strict",
            use_only_tar_bz2=False,
            pinned_packages=(),
            allow_cycles=True,
            add_pip_as_python_dependency=True,
            virtual_packages=(("linux-64", ("__linux=1",)),),
        ),
    }


@pytest.fixture
def workspace_cache_input(
    workspace_consistent_lock_path, workspace_consistent_manifest_text
):
    return WorkspaceLockInput.from_path(workspace_consistent_lock_path).with_manifest(
        workspace_consistent_manifest_text, "conda.toml"
    )


@pytest.fixture(params=["ordinary", "workspace", "update"])
def workspace_identity(request, workspace_cache_input):
    if request.param == "ordinary":
        return None
    if request.param == "workspace":
        operation = workspace_cache_input.manifest.select(["test"], ["cpu"])
    else:
        operation = workspace_cache_input.prepare_update(
            "test", "cpu", ("probe",)
        ).configured()
    return operation.cache_identity()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["error", "timeout", "oversized"])
async def test_result_cache_omits_permalink_after_persistent_write_failure(
    monkeypatch, failure
):
    release = anyio.Event()

    class FailingStore:
        async def get(self, _key):
            raise RuntimeError("store unavailable")

        async def set(self, _key, _value, expires_in=None):
            if failure == "timeout":
                await release.wait()
            else:
                raise RuntimeError("store unavailable")

    if failure == "oversized":
        monkeypatch.setattr(cache_module, "RESULT_CACHE_MAX_STORED_BYTES", 1)
    monkeypatch.setattr(cache_module, "RESULT_CACHE_STORE_TIMEOUT_S", 0.01)
    store_operations = StoreOperationCoordinator(FailingStore())
    cache = ResultCache(max_size=1, store_operations=store_operations)
    cache.remember_memory("public", StoredResult(b"previous", "text/plain"))

    async with store_operations.lifespan():
        response = await cache.remember("public", b"body", "text/plain")
        release.set()
        missing = await cache.get_response("public", location="/r/public")

    assert response.content == b"body"
    assert "Location" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert missing is None
    assert not cache.entries
    assert cache.current_bytes == 0


@pytest.mark.anyio
@pytest.mark.parametrize("max_size", [0, 1], ids=["store-only", "memory-and-store"])
async def test_result_cache_publishes_a_permalink_readable_by_another_instance(
    max_size,
):
    store = MemoryStore()
    first_operations = StoreOperationCoordinator(store)
    second_operations = StoreOperationCoordinator(store)
    first = ResultCache(max_size=max_size, store_operations=first_operations)
    second = ResultCache(max_size=1, store_operations=second_operations)

    async with first_operations.lifespan(), second_operations.lifespan():
        response = await first.remember("public", b"body", "text/plain")
        retrieved = await second.get_response(
            ResultCache.resolve_key(response.headers["Location"].rsplit("/", 1)[1]),
            immutable=True,
        )

    assert response.headers["Location"].startswith("/r/")
    assert retrieved is not None
    assert retrieved.content == response.content == b"body"
    assert retrieved.media_type == response.media_type == "text/plain"


@pytest.mark.anyio
@pytest.mark.parametrize("same_body", [False, True], ids=["timestamp", "media-type"])
async def test_changed_output_has_a_new_immutable_location(same_body):
    cache = ResultCache(max_size=8)
    first_body = b'{"metadata":{"timestamp":"2026-09-13T00:00:00Z"}}'
    second_body = (
        first_body
        if same_body
        else b'{"metadata":{"timestamp":"2026-09-13T00:00:01Z"}}'
    )
    first = await cache.remember("request", first_body, "application/json")
    second = await cache.remember(
        "request", second_body, "text/plain" if same_body else "application/json"
    )

    assert first.headers["Location"] != second.headers["Location"]
    for response in (first, second):
        digest = response.headers["Location"].rsplit("/", 1)[1]
        retained = await cache.get_response(cache.resolve_key(digest), immutable=True)
        assert retained.content == response.content
        assert retained.media_type == response.media_type
    latest = await cache.get_response("request")
    assert latest.headers["Location"] == second.headers["Location"]


@pytest.mark.anyio
@pytest.mark.parametrize("publication_succeeds", [True, False])
async def test_cached_request_republishes_missing_artifact(
    monkeypatch, publication_succeeds
):
    store_operations = StoreOperationCoordinator(MemoryStore())
    cache = ResultCache(max_size=8, store_operations=store_operations)
    async with store_operations.lifespan():
        first = await cache.remember("request", b"body", "text/plain")
        digest = first.headers["Location"].rsplit("/", 1)[1]
        artifact_key = cache.resolve_key(digest)
        await cache.discard(artifact_key)
        if not publication_succeeds:

            async def fail_publication(*_args, **_kwargs):
                return False

            monkeypatch.setattr(store_operations, "set", fail_publication)
        cached = await cache.get_response("request")
        artifact = await cache.get_response(artifact_key, immutable=True)

    assert cached.content == b"body"
    assert ("Location" in cached.headers) is publication_succeeds
    assert (artifact is not None) is publication_succeeds
    if publication_succeeds:
        assert cached.headers["Location"] == first.headers["Location"]
        assert artifact.content == b"body"


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["memory", "store"])
async def test_artifact_lookup_rejects_valid_data_under_the_wrong_digest(source):
    store = MemoryStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=8, store_operations=store_operations)
    key = cache.resolve_key("a" * 64)
    stored = StoredResult(b"different artifact", "text/plain")
    await store.set(key, msgspec.msgpack.encode(stored))
    if source == "memory":
        cache.remember_memory(key, stored)

    async with store_operations.lifespan():
        result = await cache.get_response(key, immutable=True)

    assert result is None
    assert not cache.entries
    assert cache.current_bytes == 0
    assert await store.get(key) is None


@pytest.mark.anyio
async def test_result_cache_retains_sbom_package_and_environment_identifiers():
    cache = ResultCache(max_size=4)
    body = msgspec.json.encode(
        {
            "components": [
                {
                    "purl": (
                        "pkg:conda/zlib@1.3?build=0&channel=conda-forge&subdir=linux-64"
                    ),
                    "bom-ref": "conda-environment:environment?platform=linux-64",
                }
            ]
        }
    )
    response = await cache.remember("request", body, "application/json")

    digest = response.headers["Location"].rsplit("/", 1)[1]
    retained = await cache.get_response(cache.resolve_key(digest), immutable=True)
    assert retained.content == body


@pytest.mark.anyio
async def test_result_cache_persistent_read_has_a_caller_deadline(monkeypatch):
    started = anyio.Event()
    release = anyio.Event()

    class SlowStore:
        async def get(self, _key):
            started.set()
            await release.wait()

        async def set(self, _key, _value, expires_in=None):
            return None

    monkeypatch.setattr(cache_module, "RESULT_CACHE_STORE_TIMEOUT_S", 0.01)
    store = SlowStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=8, store_operations=store_operations)

    async with store_operations.lifespan():
        assert await cache.get_response("key", location="/r/key") is None
        await started.wait()
        release.set()


@pytest.mark.parametrize(
    ("backend", "expected_type"),
    [
        pytest.param("file", FileStore, id="file"),
        pytest.param("redis", RedisStore, id="redis"),
    ],
)
def test_result_cache_store_for_config_returns_store(
    tmp_path,
    backend,
    expected_type,
):
    store = ResultCache.store_for_config(
        backend,
        str(tmp_path) if backend == "file" else None,
        "redis://localhost:6379/0" if backend == "redis" else None,
        "conda-presto-test",
    )

    assert isinstance(store, expected_type)


@pytest.mark.parametrize(
    "backend, error",
    [
        pytest.param("file", "CONDA_PRESTO_RESULT_CACHE_DIR", id="file-dir"),
        pytest.param("sqlite", "Unsupported result cache backend", id="unknown"),
    ],
)
def test_result_cache_store_for_config_rejects_invalid_config(backend, error):
    with pytest.raises(ValueError, match=error):
        ResultCache.store_for_config(backend, None, None, "conda-presto")


@pytest.mark.parametrize(
    "max_bytes, writes, expected_retained, expected_keys, expected_bytes",
    [
        pytest.param(
            30,
            (("first", 20), ("second", 20)),
            (True, True),
            ["second"],
            30,
            id="evict-oldest-by-bytes",
        ),
        pytest.param(
            100,
            (("same", 10), ("same", 20)),
            (True, True),
            ["same"],
            30,
            id="replace-existing-accounting",
        ),
        pytest.param(
            10,
            (("oversized", 20),),
            (False,),
            [],
            0,
            id="skip-oversized",
        ),
        pytest.param(
            20,
            (("same", 5), ("same", 20)),
            (True, False),
            [],
            0,
            id="drop-existing-oversized-replacement",
        ),
    ],
)
def test_result_cache_memory_limit(
    max_bytes,
    writes,
    expected_retained,
    expected_keys,
    expected_bytes,
):
    cache = ResultCache(max_size=10, max_bytes=max_bytes)

    retained = tuple(
        cache.remember_memory(
            key,
            cache_module.StoredResult(b"x" * body_size, "text/plain"),
        )
        for key, body_size in writes
    )

    assert retained == expected_retained
    assert list(cache.entries) == expected_keys
    assert cache.current_bytes == expected_bytes


@pytest.mark.anyio
async def test_result_cache_remember_omits_permalink_when_memory_rejects_result():
    cache = ResultCache(max_size=10, max_bytes=10)

    response = await cache.remember(
        "oversized",
        b"x" * 20,
        "text/plain",
    )

    assert "Location" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert not cache.entries


@pytest.mark.anyio
async def test_result_cache_uses_no_store_until_immutable_permalink_lookup():
    cache = ResultCache(max_size=10)
    response = await cache.remember("key", b"body", "text/plain")

    mutable = await cache.get_response("key", location="/r/key")
    immutable = await cache.get_response(
        ResultCache.resolve_key(response.headers["Location"].rsplit("/", 1)[1]),
        immutable=True,
    )

    assert mutable.headers["Cache-Control"] == "no-store"
    assert immutable.headers["Cache-Control"] == ("public, max-age=86400, immutable")


@pytest.mark.anyio
async def test_result_cache_can_return_a_credentialed_result_without_retaining_it():
    cache = ResultCache(max_size=10)

    response = await cache.remember(
        "key",
        b"body",
        "text/plain",
        retain=False,
    )

    assert response.headers["Cache-Control"] == "no-store"
    assert "Location" not in response.headers
    assert cache.entries == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "media_type"),
    [
        pytest.param(
            b'[{"url":"https://public.example.test/python.conda"},'
            b'{"url":"https://user:secret@cdn.example.test/zlib.conda"}]',
            "application/json",
            id="native-json",
        ),
        pytest.param(
            b"@EXPLICIT\nhttps://cdn.example.test/zlib.conda?signature=secret\n",
            "text/plain",
            id="exporter",
        ),
        pytest.param(
            b"https://user:\nsecret@cdn.example.test/zlib.conda",
            "text/plain",
            id="url-with-newline",
        ),
        pytest.param(
            b'{"purl":"pkg:conda/zlib@1.3?channel=https%3A%2F%2Fuser%3Asecret%40example.test"}',
            "application/json",
            id="purl-credentialed-channel",
        ),
        pytest.param(
            b'{"purl":"pkg:conda/zlib@1.3?channel=https%3A%2F%2Fexample.test%2Ft%2Fsecret"}',
            "application/json",
            id="purl-channel-token-path",
        ),
        pytest.param(
            b'{"purl":"pkg:conda/zlib@1.3?token=secret"}',
            "application/json",
            id="purl-token-qualifier",
        ),
        pytest.param(
            b'{"password":"secret"}',
            "application/json",
            id="json-password",
        ),
    ],
)
async def test_result_cache_does_not_retain_credentialed_output(body, media_type):
    store = MemoryStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        response = await cache.remember(
            "key",
            body,
            media_type,
        )

    assert response.content == body
    assert response.headers["Cache-Control"] == "no-store"
    assert "Location" not in response.headers
    assert cache.entries == {}
    assert await store.get("key") is None


@pytest.mark.anyio
@pytest.mark.parametrize("credentialed", [False, True])
async def test_explicit_file_comments_do_not_hide_or_invent_credentials(credentialed):
    host = "user:secret@conda.example" if credentialed else "conda.example"
    body = (
        "# This file can create an environment.\n"
        "# platform: linux-64\n@EXPLICIT\n"
        f"https://{host}/linux-64/probe-1.0-0.conda\n"
    ).encode()
    cache = ResultCache(max_size=10)
    response = await cache.remember("explicit", body, "text/plain")
    assert response.content == body
    assert ("Location" in response.headers) is not credentialed
    if not credentialed:
        retained = await cache.get_response("explicit")
        assert retained.content == body


@pytest.mark.anyio
async def test_result_cache_deletes_credentialed_persistent_output():
    store = MemoryStore()
    await store.set(
        "key",
        msgspec.msgpack.encode(
            StoredResult(
                body=b"https://user:secret@cdn.example.test/zlib.conda",
                media_type="text/plain",
            )
        ),
    )
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        response = await cache.get_response("key", location="/r/key")

    assert response is None
    assert await store.get("key") is None


@pytest.mark.anyio
async def test_result_cache_discards_credentialed_memory_output():
    cache = ResultCache(max_size=10)
    stored = StoredResult(
        body=b"https://user:secret@cdn.example.test/zlib.conda",
        media_type="text/plain",
    )
    cache.entries["key"] = stored
    cache.current_bytes = stored.memory_size

    response = await cache.get_response("key", location="/r/key")

    assert response is None
    assert cache.entries == {}
    assert cache.current_bytes == 0


def test_result_cache_removes_previous_output_when_replacement_has_credentials():
    cache = ResultCache(max_size=10)
    cache.remember_memory("key", StoredResult(b"public", "text/plain"))

    retained = cache.remember_memory(
        "key",
        StoredResult(
            b"https://user:secret@cdn.example.test/zlib.conda",
            "text/plain",
        ),
    )

    assert not retained
    assert cache.entries == {}
    assert cache.current_bytes == 0


@pytest.mark.anyio
async def test_persistent_result_cache_entries_expire():
    class Store:
        expires_in = None

        async def set(self, _key, _value, *, expires_in=None):
            self.expires_in = expires_in

    store = Store()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        await cache.store_entry(
            "key",
            cache_module.StoredResult(b"body", "text/plain"),
        )

    assert store.expires_in == cache_module.RESULT_CACHE_STORE_MAX_AGE_S


@pytest.mark.anyio
async def test_result_cache_ignores_corrupt_persistent_entry():
    class Store:
        value = b"corrupt"

        async def get(self, key):
            return self.value

        async def delete(self, key):
            self.value = None

    store = Store()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        assert await cache.get_response("key", location="/r/key") is None

    assert store.value is None


@pytest.mark.anyio
async def test_result_cache_deletes_oversized_persistent_entry(monkeypatch):
    class Store:
        value = b"large"

        async def get(self, key):
            return self.value

        async def delete(self, key):
            self.value = None

    monkeypatch.setattr(cache_module, "RESULT_CACHE_MAX_STORED_BYTES", 4)
    store = Store()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        assert await cache.get_response("key", location="/r/key") is None

    assert store.value is None


def test_different_output_formats_produce_different_cache_keys(resolve_cache_state):
    default_key = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )
    explicit_key = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        "explicit",
        exporter_identity=(
            "explicit",
            "conda.plugins.environment_exporters.explicit:export_explicit",
            (("conda", "test"),),
        ),
        **resolve_cache_state,
    )

    assert explicit_key != default_key


def test_result_cache_key_covers_protocol_version(monkeypatch, resolve_cache_state):
    first = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )
    monkeypatch.setattr(cache_module, "CACHE_ENVELOPE_VERSION", 7)

    assert (
        ResultCache.key_for(
            ["zlib"],
            ["conda-forge"],
            ["linux-64"],
            None,
            **resolve_cache_state,
        )
        != first
    )


def test_result_cache_key_captures_default_state(monkeypatch, resolve_cache_state):
    calls = []
    monkeypatch.setattr(
        RepodataSnapshot,
        "capture",
        lambda channels, platforms: (
            calls.append((channels, platforms)) or resolve_cache_state["repodata"]
        ),
    )
    monkeypatch.setattr(
        ResolveCacheContext,
        "capture",
        lambda platforms: (
            calls.append(platforms) or resolve_cache_state["solve_context"]
        ),
    )

    key = ResultCache.key_for(["zlib"], ["conda-forge"], None, None)

    assert len(key) == 64
    assert calls == [
        (["conda-forge"], [cache_module.NATIVE_SUBDIR]),
        [cache_module.NATIVE_SUBDIR],
    ]


def test_result_cache_key_tolerates_missing_dependency_version(
    monkeypatch,
    resolve_cache_state,
):
    def missing_version(_):
        raise ModuleNotFoundError

    monkeypatch.setattr(cache_module, "pkg_version", missing_version)

    key = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )

    assert len(key) == 64


def test_empty_output_format_does_not_collide_with_native(resolve_cache_state):
    native = ResultCache.key_for(
        ["zlib"], ["conda-forge"], ["linux-64"], None, **resolve_cache_state
    )
    empty = ResultCache.key_for(
        ["zlib"], ["conda-forge"], ["linux-64"], "", **resolve_cache_state
    )

    assert empty != native


def test_exporter_provider_version_changes_cache_key(resolve_cache_state):
    request = (["zlib"], ["conda-forge"], ["linux-64"], "third-party")
    first = ResultCache.key_for(
        *request,
        exporter_identity=(
            "third-party",
            "third_party.exporter:export",
            (("third-party", "1"),),
        ),
        **resolve_cache_state,
    )
    second = ResultCache.key_for(
        *request,
        exporter_identity=(
            "third-party",
            "third_party.exporter:export",
            (("third-party", "2"),),
        ),
        **resolve_cache_state,
    )

    assert second != first


@pytest.mark.parametrize("package_name", cache_module.CACHE_DEPENDENCY_PACKAGES)
def test_different_dependency_versions_produce_different_cache_keys(
    monkeypatch,
    package_name,
    resolve_cache_state,
):
    def version_one(package):
        if package == package_name:
            return "one"
        return "test"

    def version_two(package):
        if package == package_name:
            return "two"
        return "test"

    monkeypatch.setattr(cache_module, "pkg_version", version_one)
    first = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )
    monkeypatch.setattr(cache_module, "pkg_version", version_two)
    second = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )

    assert second != first


def test_cache_keys_use_virtual_packages_from_the_request_type(
    resolve_cache_state, workspace_identity
):
    first_context = resolve_cache_state["solve_context"]
    second_context = replace(
        first_context,
        virtual_packages=(("linux-64", ("__glibc=9.9", "__linux=1")),),
    )
    request = (["zlib"], ["conda-forge"], ["linux-64"], None)
    repodata = resolve_cache_state["repodata"]

    first = ResultCache.key_for(
        *request,
        repodata=repodata,
        solve_context=first_context,
        workspace_identity=workspace_identity,
    )
    second = ResultCache.key_for(
        *request,
        repodata=repodata,
        solve_context=second_context,
        workspace_identity=workspace_identity,
    )

    assert (second != first) is (workspace_identity is None)


@pytest.mark.parametrize("update", [False, True], ids=["workspace", "update"])
def test_workspace_virtual_requirements_produce_different_cache_keys(
    workspace_cache_input, resolve_cache_state, update
):
    keys = []
    for version in ("2.28", "2.29"):
        lock = workspace_cache_input.with_manifest(
            workspace_cache_input.manifest_content.replace('"2.28"', f'"{version}"'),
            "conda.toml",
        )
        operation = (
            lock.prepare_update("test", "cpu", ("probe",)).configured()
            if update
            else lock.manifest.select(["test"], ["cpu"])
        )
        keys.append(
            ResultCache.key_for(
                ["probe"],
                ["conda-forge"],
                ["linux-64"],
                None,
                workspace_identity=operation.cache_identity(),
                **resolve_cache_state,
            )
        )
    assert keys[0] != keys[1]


def test_resolve_cache_context_captures_cuda_override(monkeypatch):
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "12.0")
    first = ResolveCacheContext.capture(["linux-64"])
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "13.0")
    second = ResolveCacheContext.capture(["linux-64"])

    assert first.virtual_packages != second.virtual_packages


@pytest.mark.parametrize(
    ("first_context", "second_context"),
    [
        pytest.param(
            {"channel_priority": "strict"},
            {"channel_priority": "disabled"},
            id="channel-priority",
        ),
        pytest.param(
            {"use_only_tar_bz2": False},
            {"use_only_tar_bz2": True},
            id="package-format",
        ),
        pytest.param(
            {"pinned_packages": ("python<3.13",)},
            {"pinned_packages": ("python<3.14",)},
            id="pinned-packages",
        ),
        pytest.param(
            {"allow_cycles": True},
            {"allow_cycles": False},
            id="allow-cycles",
        ),
        pytest.param(
            {"add_pip_as_python_dependency": True},
            {"add_pip_as_python_dependency": False},
            id="add-pip",
        ),
    ],
)
def test_solve_context_produces_different_cache_keys(
    first_context,
    second_context,
    resolve_cache_state,
    workspace_identity,
):
    request = (["zlib"], ["conda-forge"], ["linux-64"], None)
    base = resolve_cache_state["solve_context"]

    first = ResultCache.key_for(
        *request,
        repodata=resolve_cache_state["repodata"],
        solve_context=replace(base, **first_context),
        workspace_identity=workspace_identity,
    )
    second = ResultCache.key_for(
        *request,
        repodata=resolve_cache_state["repodata"],
        solve_context=replace(base, **second_context),
        workspace_identity=workspace_identity,
    )

    assert first != second


@pytest.mark.parametrize(
    "channel",
    [
        pytest.param("https://user:secret@repo.example/channel", id="basic-auth"),
        pytest.param("https://repo.example/t/secret/channel", id="token"),
        pytest.param("https://repo.example/%2574%252Fsecret/channel", id="encoded"),
        pytest.param("https://repo.example/channel?signature=secret", id="query"),
    ],
)
def test_result_cache_detects_credentialed_channels(channel):
    assert ResultCache.channels_have_credentials([channel])


def test_result_cache_accepts_public_channels():
    assert not ResultCache.channels_have_credentials(
        ["conda-forge", "https://repo.example/channel"]
    )


def test_result_cache_detects_credentials_in_custom_multichannel(monkeypatch):
    monkeypatch.setattr(
        cache_module,
        "context",
        SimpleNamespace(
            custom_multichannels={
                "private": (
                    Channel("https://user:secret@example.test/t/token/channel"),
                )
            }
        ),
    )

    assert ResultCache.channels_have_credentials(["private"])
    assert ResultCache.specs_have_credentials(["private::zlib"])


@pytest.mark.parametrize("spec", ["zlib%ZZ", "["])
def test_result_cache_does_not_retain_unparseable_specs(spec):
    assert ResultCache.specs_have_credentials([spec])


def test_result_cache_does_not_retain_unparseable_channels(monkeypatch):
    def invalid_channel(_):
        raise ValueError("invalid channel")

    monkeypatch.setattr(cache_module, "Channel", invalid_channel)

    assert ResultCache.channels_have_credentials(["channel"])
