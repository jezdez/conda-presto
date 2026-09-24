"""Compare exported lock records, package identities and declared SBOM roots."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from conda_workspaces.lockfile import CondaLockLoader, load_lockfile_data
from ruamel.yaml import YAML

source = load_lockfile_data(Path("conda.lock").read_text())
extracted = load_lockfile_data(Path("extracted/conda.lock").read_text())
references = source["environments"]["tools"]["packages"]["cpu"]
assert set(extracted["environments"]) == {"tools"}
assert extracted["environments"]["tools"]["packages"] == {"cpu": references}
urls = {reference["conda"] for reference in references}
records = {
    record["conda"]: record for record in source["packages"] if record["conda"] in urls
}
assert {record["conda"]: record for record in extracted["packages"]} == records
explicit = Path("explicit.txt").read_text().splitlines()
assert "@EXPLICIT" in explicit
assert {line for line in explicit if line and not line.startswith(("#", "@"))} == urls
normalized = tomllib.loads(Path("normalized.toml").read_text())
environment = CondaLockLoader(Path("conda.lock"), data=source).env_for(
    "cpu", "tools", package_platform="linux-64", metadata_only=True
)
assert set(normalized["dependencies"]) == {
    record.name for record in environment.explicit_packages
}
for record in environment.explicit_packages:
    dependency = normalized["dependencies"][record.name]
    assert dependency["version"] == record.version
    assert dependency["url"] == record.url
    assert dependency["sha256"] == record.sha256
print("Workspace extraction preserves exact records and explicit package URLs")
print("Normalized declaration preserves saved versions, URLs and hashes")

reader = YAML(typ="safe")
pixi = reader.load(Path("pixi.lock"))
conda_lock = reader.load(Path("conda-lock.yml"))
assert conda_lock["version"] == 1
assert conda_lock["metadata"]["platforms"] == ["linux-64"]
assert pixi["version"] == 6
assert set(pixi["environments"]) == {"default"}
assert set(pixi["environments"]["default"]["packages"]) == {"linux-64"}
assert {package["url"]: package["hash"] for package in conda_lock["package"]} == {
    package["conda"]: {
        name: package[name] for name in ("sha256", "md5") if name in package
    }
    for package in pixi["packages"]
}
print("Generic lock conversion preserves package URLs and hashes")

sbom = json.loads(Path("sbom.json").read_text())
assert sbom["bomFormat"] == "CycloneDX"
assert sbom["specVersion"] == "1.7"
root = sbom["metadata"]["component"]
properties = {item["name"]: item["value"] for item in root["properties"]}
assert properties["conda:environment:root-dependency-source"] == "requested-packages"
components = {component["name"]: component for component in sbom["components"]}
assert len(components) == len(records)
identities = {
    record.url: (record.name, record.version)
    for record in environment.explicit_packages
}
for component in components.values():
    distributions = {
        reference["url"]
        for reference in component["externalReferences"]
        if reference["type"] == "distribution"
    }
    assert len(distributions) == 1
    url = distributions.pop()
    record = records[url]
    assert (component["name"], component["version"]) == identities[url]
    hashes = {item["alg"]: item["content"] for item in component["hashes"]}
    assert hashes["SHA-256"] == record["sha256"]
dependencies = {item["ref"]: item["dependsOn"] for item in sbom["dependencies"]}
assert set(dependencies[root["bom-ref"]]) == {
    components[name]["bom-ref"] for name in ("zlib", "zstd")
}
print("SBOM contains exact saved packages, hashes and declared zlib/zstd roots")
