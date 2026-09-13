"""Tests for conda_presto.plugin registration."""

from __future__ import annotations

import argparse

import pytest

from conda_presto.plugin import conda_subcommands


@pytest.fixture()
def parser():
    """Configured argument parser from the plugin's subcommand."""
    sc = next(iter(conda_subcommands()))
    p = argparse.ArgumentParser()
    sc.configure_parser(p)
    return p


def test_plugin_yields_subcommand():
    subcommands = list(conda_subcommands())
    assert len(subcommands) == 1
    sc = subcommands[0]
    assert sc.name == "presto"
    assert sc.summary
    assert callable(sc.action)
    assert callable(sc.configure_parser)


@pytest.mark.parametrize(
    "argv, attr, expected",
    [
        pytest.param(
            ["-c", "conda-forge", "-p", "linux-64", "zlib"],
            "channel",
            ["conda-forge"],
            id="channel",
        ),
        pytest.param(
            ["-c", "conda-forge", "-p", "linux-64", "zlib"],
            "platforms",
            ["linux-64"],
            id="platform",
        ),
        pytest.param(
            ["-c", "conda-forge", "-p", "linux-64", "zlib"],
            "specs",
            ["zlib"],
            id="specs",
        ),
        pytest.param(
            ["-c", "conda-forge", "zlib"],
            "serve",
            False,
            id="serve-default",
        ),
        pytest.param(
            ["--serve", "--port", "9000"],
            "serve",
            True,
            id="serve-flag",
        ),
        pytest.param(
            ["--serve", "--port", "9000"],
            "port",
            9000,
            id="serve-port",
        ),
        pytest.param(
            ["--solver", "rattler", "-c", "conda-forge", "zlib"],
            "solver",
            "rattler",
            id="solver",
        ),
        pytest.param(
            ["--format", "yaml", "-c", "conda-forge", "zlib"],
            "output_format",
            "yaml",
            id="format-yaml",
        ),
        pytest.param(
            ["-c", "conda-forge", "zlib"],
            "output_format",
            None,
            id="format-default-native",
        ),
    ],
)
def test_parser_flag(parser, argv, attr, expected):
    args = parser.parse_args(argv)
    assert getattr(args, attr) == expected
