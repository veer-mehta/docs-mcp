import json
import re
import tomllib
from dataclasses import dataclass


@dataclass
class Dependency:
    name: str
    version: str | None = None
    ecosystem: str = "pypi"


def parse_requirements_txt(content: str) -> list[Dependency]:
    deps = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r'^([a-zA-Z0-9_.-]+)\s*([=<>!~]=?\s*\S+)?', line)
        if match:
            name = match.group(1)
            ver = (match.group(2) or "").strip().lstrip("=<>!~").strip() or None
            deps.append(Dependency(name=name, version=ver, ecosystem="pypi"))
    return deps


def parse_pyproject_toml(content: str) -> list[Dependency]:
    data = tomllib.loads(content)
    deps = []

    for dep_str in data.get("project", {}).get("dependencies", []):
        match = re.match(r'^([a-zA-Z0-9_.-]+)\s*(\[.*?\])?\s*([=<>!~]=?\s*\S+)?', dep_str)
        if match:
            name = match.group(1)
            ver = (match.group(3) or "").strip().lstrip("=<>!~").strip() or None
            deps.append(Dependency(name=name, version=ver, ecosystem="pypi"))

    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name, spec in poetry_deps.items():
        if name.lower() == "python":
            continue
        if isinstance(spec, str):
            ver = spec.strip().lstrip("=<>!~^").strip() or None
        elif isinstance(spec, dict):
            ver = spec.get("version", "").strip().lstrip("=<>!~^").strip() or None
        else:
            ver = None
        deps.append(Dependency(name=name, version=ver, ecosystem="pypi"))

    return deps


def parse_package_json(content: str) -> list[Dependency]:
    data = json.loads(content)
    deps = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for name, ver_str in data.get(section, {}).items():
            ver = re.sub(r'^[\^~>=<*]+', '', ver_str).strip() or None
            deps.append(Dependency(name=name, version=ver, ecosystem="npm"))
    return deps


def parse_package_lock(content: str) -> list[Dependency]:
    data = json.loads(content)
    deps = []
    packages = data.get("packages", data.get("dependencies", {}))
    for key, info in packages.items():
        name = key.split("node_modules/")[-1] if "node_modules/" in key else key
        if not name:
            continue
        ver = info.get("version", None)
        deps.append(Dependency(name=name, version=ver, ecosystem="npm"))
    return deps


PARSERS = {
    "requirements.txt": parse_requirements_txt,
    "pyproject.toml": parse_pyproject_toml,
    "package.json": parse_package_json,
    "package-lock.json": parse_package_lock,
}


def parse_dep_file(filename: str, content: str) -> list[Dependency]:
    for pattern, parser in PARSERS.items():
        if filename == pattern or filename.endswith("/" + pattern):
            return parser(content)
    if filename.endswith(".txt"):
        return parse_requirements_txt(content)
    if filename.endswith(".toml"):
        return parse_pyproject_toml(content)
    if filename.endswith(".json"):
        return parse_package_json(content)
    return []
