import logging
import re

import httpx

from docs_mcp.parsers import Dependency

logger = logging.getLogger(__name__)

PYPI_API = "https://pypi.org/pypi/{name}/json"
NPM_API = "https://registry.npmjs.org/{name}"

LANGUAGE_DOCS = {
    "python": "https://docs.python.org/3/",
    "node": "https://nodejs.org/docs/latest/api/",
}

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
                gh_match = re.search(r'github\.com[/:]([^/]+/[^/.]+)', repo_url)
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
