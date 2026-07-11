"""Tests for conda_presto.config env-var parsing helpers."""

from __future__ import annotations

import importlib

import pytest

import conda_presto.config as config_module
from conda_presto.config import env_bool, env_int, env_list


@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param("a,b,c", ["a", "b", "c"], id="simple"),
        pytest.param("a, b ,c", ["a", "b", "c"], id="whitespace"),
        pytest.param("a,,b", ["a", "b"], id="empty-parts"),
        pytest.param(" , ,a , ", ["a"], id="mostly-empty"),
        pytest.param("only", ["only"], id="single"),
    ],
)
def test_env_list_parses_and_strips(monkeypatch, raw, expected):
    monkeypatch.setenv("CONDA_PRESTO_TEST_LIST", raw)
    assert env_list("CONDA_PRESTO_TEST_LIST", "") == expected


def test_env_list_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("CONDA_PRESTO_TEST_LIST", raising=False)
    assert env_list("CONDA_PRESTO_TEST_LIST", "x, y") == ["x", "y"]


def test_env_int_parses_valid(monkeypatch):
    monkeypatch.setenv("CONDA_PRESTO_TEST_INT", "42")
    assert env_int("CONDA_PRESTO_TEST_INT", 0) == 42


def test_env_int_rejects_non_numeric(monkeypatch):
    monkeypatch.setenv("CONDA_PRESTO_TEST_INT", "not-a-number")
    with pytest.raises(ValueError, match="Invalid integer for CONDA_PRESTO_TEST_INT"):
        env_int("CONDA_PRESTO_TEST_INT", 0)


@pytest.mark.parametrize("raw", ["", None], ids=["empty", "unset"])
def test_env_int_uses_default(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("CONDA_PRESTO_TEST_INT", raising=False)
    else:
        monkeypatch.setenv("CONDA_PRESTO_TEST_INT", raw)
    assert env_int("CONDA_PRESTO_TEST_INT", 42) == 42


@pytest.mark.parametrize(
    "raw, expected",
    [
        pytest.param("1", True, id="one"),
        pytest.param("true", True, id="true"),
        pytest.param("yes", True, id="yes"),
        pytest.param("0", False, id="zero"),
        pytest.param("false", False, id="false"),
        pytest.param("no", False, id="no"),
    ],
)
def test_env_bool_parses_known_values(monkeypatch, raw, expected):
    monkeypatch.setenv("CONDA_PRESTO_TEST_BOOL", raw)
    assert env_bool("CONDA_PRESTO_TEST_BOOL") is expected


def test_env_bool_rejects_ambiguous_value(monkeypatch):
    monkeypatch.setenv("CONDA_PRESTO_TEST_BOOL", "sometimes")
    with pytest.raises(ValueError, match="Invalid boolean for CONDA_PRESTO_TEST_BOOL"):
        env_bool("CONDA_PRESTO_TEST_BOOL")


@pytest.mark.parametrize(
    "raw, expected_bytes",
    [
        pytest.param("2", 2 * 1024 * 1024, id="two-mb"),
        pytest.param("0", 0, id="disabled"),
    ],
)
def test_result_cache_max_memory_mb_converts_to_bytes(monkeypatch, raw, expected_bytes):
    monkeypatch.setenv("CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB", raw)
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.RESULT_CACHE_MAX_MEMORY_BYTES == expected_bytes
    finally:
        monkeypatch.delenv("CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB", raising=False)
        importlib.reload(config_module)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("CONDA_PRESTO_MAX_REPAIR_SUGGESTIONS", id="suggestions"),
        pytest.param("CONDA_PRESTO_MAX_REPAIR_ATTEMPTS", id="attempts"),
        pytest.param("CONDA_PRESTO_MAX_REPAIR_TIME_BUDGET_MS", id="time-budget"),
    ],
)
def test_repair_limits_must_be_positive(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match="Repair limits must be positive"):
        importlib.reload(config_module)
    monkeypatch.delenv(name)
    importlib.reload(config_module)


def test_solver_hot_set_size_must_not_be_negative(monkeypatch):
    monkeypatch.setenv("CONDA_PRESTO_SOLVER_CACHE_HOTSET_SIZE", "-1")
    with pytest.raises(ValueError, match="must not be negative"):
        importlib.reload(config_module)
    monkeypatch.delenv("CONDA_PRESTO_SOLVER_CACHE_HOTSET_SIZE")
    importlib.reload(config_module)


def test_solver_hot_set_persistence_rejects_memory_backend(monkeypatch):
    monkeypatch.setenv("CONDA_PRESTO_SOLVER_CACHE_HOTSET_PERSIST", "true")
    monkeypatch.setenv("CONDA_PRESTO_RESULT_CACHE_BACKEND", "memory")
    with pytest.raises(ValueError, match="requires a file or Redis"):
        importlib.reload(config_module)
    monkeypatch.delenv("CONDA_PRESTO_SOLVER_CACHE_HOTSET_PERSIST")
    monkeypatch.delenv("CONDA_PRESTO_RESULT_CACHE_BACKEND")
    importlib.reload(config_module)


@pytest.mark.parametrize("backend", ["file", "redis"])
def test_solver_hot_set_persistence_accepts_persistent_backend(monkeypatch, backend):
    monkeypatch.setenv("CONDA_PRESTO_SOLVER_CACHE_HOTSET_PERSIST", "true")
    monkeypatch.setenv("CONDA_PRESTO_RESULT_CACHE_BACKEND", backend)
    reloaded = importlib.reload(config_module)
    try:
        assert reloaded.SOLVER_CACHE_HOTSET_PERSIST is True
    finally:
        monkeypatch.delenv("CONDA_PRESTO_SOLVER_CACHE_HOTSET_PERSIST")
        monkeypatch.delenv("CONDA_PRESTO_RESULT_CACHE_BACKEND")
        importlib.reload(config_module)
