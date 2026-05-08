"""Tests for conda_presto.config env-var parsing helpers."""

from __future__ import annotations

import pytest

from conda_presto.config import env_int, env_list


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
