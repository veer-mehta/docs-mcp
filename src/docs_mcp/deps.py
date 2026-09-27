import json
import logging
import re
import tomllib
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


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
        match = re.match(r"^([a-zA-Z0-9_.-]+)\s*([=<>!~]=?\s*\S+)?", line)
        if match:
            name = match.group(1)
            ver = (match.group(2) or "").strip().lstrip("=<>!~").strip() or None
            deps.append(Dependency(name=name, version=ver, ecosystem="pypi"))
    return deps


def parse_pyproject_toml(content: str) -> list[Dependency]:
    data = tomllib.loads(content)
    deps = []

    for dep_str in data.get("project", {}).get("dependencies", []):
        match = re.match(r"^([a-zA-Z0-9_.-]+)\s*(\[.*?\])?\s*([=<>!~]=?\s*\S+)?", dep_str)
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
            ver = re.sub(r"^[\^~>=<*]+", "", ver_str).strip() or None
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


PARSERS = {"requirements.txt": parse_requirements_txt, "pyproject.toml": parse_pyproject_toml, "package.json": parse_package_json, "package-lock.json": parse_package_lock}


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


PYPI_API = "https://pypi.org/pypi/{name}/json"
NPM_API = "https://registry.npmjs.org/{name}"

LANGUAGE_DOCS = {"python": "https://docs.python.org/3/", "node": "https://nodejs.org/docs/latest/api/"}

JS_RUNTIME_LIBS = {"node", "npm", "core-js", "tslib", "typescript", "webpack", "vite", "esbuild", "rollup", "parcel"}
PYTHON_STDLIB = {"pip", "setuptools", "wheel", "build", "twine"}


async def find_doc_url(dep: Dependency) -> str | None:
    if dep.ecosystem == "pypi":
        return await _find_pypi_docs(dep.name)
    if dep.ecosystem == "npm":
        return await _find_npm_docs(dep.name)
    return None


async def _find_pypi_docs(name: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(PYPI_API.format(name=name))
            if resp.status_code != 200:
                return None
            data = resp.json()
            info = data.get("info", {})

            for field in ("project_urls", "home_page"):
                val = info.get(field)
                if isinstance(val, dict):
                    for key, url in val.items():
                        if url and _is_doc_url(key, url):
                            return url
                elif isinstance(val, str) and val.startswith("http"):
                    if _is_doc_url("home", val):
                        return val

            doc_url = info.get("docs_url") or info.get("project_url")
            if doc_url and doc_url.startswith("http"):
                return doc_url

            home = info.get("home_page", "")
            if home and home.startswith("http"):
                return home

            return f"https://pypi.org/project/{name}/"
    except Exception as exc:
        logger.debug("pypi lookup failed for %s: %s", name, exc)
        return f"https://pypi.org/project/{name}/"


async def _find_npm_docs(name: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(NPM_API.format(name=name))
            if resp.status_code != 200:
                return None
            data = resp.json()
            homepage = data.get("homepage", "")
            repo = data.get("repository", {})
            repo_url = repo.get("url", "") if isinstance(repo, dict) else str(repo)

            if homepage and homepage.startswith("http"):
                return homepage

            if repo_url:
                gh_match = re.search(r"github\.com[/:]([^/]+/[^/.]+)", repo_url)
                if gh_match:
                    repo_path = gh_match.group(1).rstrip(".git")
                    return f"https://github.com/{repo_path}"

            return f"https://www.npmjs.com/package/{name}"
    except Exception as exc:
        logger.debug("npm lookup failed for %s: %s", name, exc)
        return f"https://www.npmjs.com/package/{name}"


def _is_doc_url(key: str, url: str) -> bool:
    key_lower = key.lower()
    if any(w in key_lower for w in ("doc", "docs", "documentation", "wiki", "manual")):
        return True
    if any(w in url for w in ("readthedocs", "docs.", ".readthedocs.io", ".github.io")):
        return True
    return False


def filter_deps(deps: list[Dependency], max_deps: int = 20) -> list[Dependency]:
    skip = JS_RUNTIME_LIBS | PYTHON_STDLIB
    seen = set()
    filtered = []
    for dep in deps:
        name_lower = dep.name.lower()
        if name_lower in skip or name_lower in seen:
            continue
        seen.add(name_lower)
        filtered.append(dep)
        if len(filtered) >= max_deps:
            break
    return filtered


def detect_language(deps: list[Dependency]) -> str | None:
    ecosystems = [d.ecosystem for d in deps]
    if ecosystems.count("pypi") > ecosystems.count("npm"):
        return "python"
    if ecosystems.count("npm") > ecosystems.count("pypi"):
        return "node"
    return None


async def resolve_dependencies(filename: str, content: str, max_deps: int = 20) -> dict:
    deps = parse_dep_file(filename, content)
    if not deps:
        raise ValueError(f"could not parse dependencies from {filename}")
    deps = filter_deps(deps, max_deps=max_deps)
    lang = detect_language(deps)
    results = []
    for dep in deps:
        doc_url = await find_doc_url(dep)
        if doc_url:
            results.append({"name": dep.name, "version": dep.version, "url": doc_url, "ecosystem": dep.ecosystem})
    if lang and lang in LANGUAGE_DOCS:
        results.append({"name": lang, "version": "latest", "url": LANGUAGE_DOCS[lang], "ecosystem": "language"})
    if not results:
        raise ValueError("no documentation URLs found")
    return {"dependencies": results, "language": lang, "total": len(results)}
