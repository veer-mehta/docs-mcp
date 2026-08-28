import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse, urlunparse

import scrapy
from scrapy.http import HtmlResponse, Response
from scrapy_playwright.page import PageMethod

logger = logging.getLogger(__name__)

NON_PAGE_EXTENSIONS = re.compile(
    r"\.(png|jpe?g|gif|svg|ico|css|js|mjs|map|pdf|zip|gz|tar|rar|7z|mp4|webm|mp3|wav|woff2?|ttf|eot|xml|json|txt|rss|atom)$",
    re.IGNORECASE,
)

LANG_SEGMENT = re.compile(r"^[a-z]{2}(-[a-z]{2,3})?$")

CACHE_EXPIRY = 60 * 60 * 24 * 7


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


class DocsSpider(scrapy.Spider):
    name = "docs"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS": 4,
        "DOWNLOAD_TIMEOUT": 45,
        "RETRY_TIMES": 1,
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 30_000,
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
    }

    def __init__(
        self,
        base_url: str,
        max_depth: int = 2,
        max_pages: int = 30,
        cache_dir: str | None = None,
        lang: str = "",
        sitemap: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.base_url = normalize_url(base_url)
        parsed = urlparse(self.base_url)
        self.allowed_domains: set[str] = {parsed.netloc}
        self.base_path = ""
        self.max_depth = int(max_depth)
        self.max_pages = int(max_pages)
        self.lang = lang.lower().strip()
        self.use_sitemap = sitemap
        self.seen: set[str] = set()
        self._root_landed = False
        self._cache_dir: Path | None = None
        if cache_dir:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_hits = 0
        self._cache_misses = 0

    def _cache_get(self, url: str) -> dict | None:
        if not self._cache_dir:
            return None
        url = normalize_url(url)
        cache_path = self._cache_dir / f"{_url_key(url)}.json"
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text())
            if (data.get("ts", 0)) < (time.time() - CACHE_EXPIRY):
                return None
            return data
        except Exception:
            return None

    def _cache_put(self, url: str, title: str | None, html: str, final_url: str | None = None) -> None:
        if not self._cache_dir:
            return
        key = _url_key(url)
        cache_path = self._cache_dir / f"{key}.json"
        try:
            data = {"url": url, "title": title, "html": html, "ts": time.time()}
            if final_url:
                data["final_url"] = final_url
            cache_path.write_text(json.dumps(data))
        except OSError as e:
            self.logger.warning("cache write failed for %s: %s", url, e)

    async def start(self):
        if self.use_sitemap:
            urls = await self._fetch_sitemap()
            if urls:
                self.logger.info("sitemap: found %d urls", len(urls))
                for url in urls[: self.max_pages]:
                    if url in self.seen:
                        continue
                    self.seen.add(url)
                    cached = self._cache_get(url)
                    if cached is not None:
                        self._cache_hits += 1
                        response = HtmlResponse(
                            url=cached["final_url"],
                            body=cached["html"].encode("utf-8"),
                            encoding="utf-8",
                            request=scrapy.Request(url),
                        )
                        response.meta["depth"] = 0
                        for item in self.parse_page(response):
                            yield item
                    else:
                        self._cache_misses += 1
                        req = self._request(url)
                        req.meta["depth"] = 0
                        yield req
                return
        cached = self._cache_get(self.base_url)
        if cached is not None:
            self._cache_hits += 1
            response = HtmlResponse(
                url=cached["final_url"],
                body=cached["html"].encode("utf-8"),
                encoding="utf-8",
                request=scrapy.Request(self.base_url),
            )
            response.meta["depth"] = 0
            for item in self.parse_page(response):
                yield item
        else:
            self._cache_misses += 1
            yield self._request(self.base_url)

    async def _fetch_sitemap(self) -> list[str]:
        import httpx

        sitemap_url = self.base_url.rstrip("/") + "/sitemap.xml"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(sitemap_url)
                resp.raise_for_status()
        except Exception as exc:
            self.logger.warning("sitemap fetch failed: %s", exc)
            return []
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            self.logger.warning("sitemap parse failed: %s", exc)
            return []
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = []
        for loc in root.findall(".//s:loc", ns):
            text = (loc.text or "").strip()
            if not text:
                continue
            normalized = normalize_url(text)
            if not self._should_follow(normalized):
                continue
            urls.append(normalized)
        return urls

    def _request(self, url: str) -> scrapy.Request:
        return scrapy.Request(
            url,
            callback=self.parse_page,
            errback=self.on_error,
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "networkidle")
                ],
                "download_timeout": 45,
            },
        )

    def parse_page(self, response: Response) -> Any:
        url = normalize_url(response.url)
        parsed = urlparse(url)
        if not self._root_landed:
            self._root_landed = True
            self.allowed_domains.add(parsed.netloc)
            segments = [s for s in parsed.path.split("/") if s]
            if len(segments) > 1:
                self.base_path = "/" + "/".join(segments[:-1])

        if url not in self.seen:
            self.seen.add(url)
            title = response.css("title::text").get()
            title_clean = (title or "").strip() or None
            html = response.text
            self._cache_put(url, title_clean, html, final_url=url)
            req_url = normalize_url(response.request.url)
            if req_url != url:
                self._cache_put(req_url, title_clean, html, final_url=url)
            yield {
                "url": url,
                "title": title_clean,
                "html": html,
            }

        if response.meta.get("depth", 0) >= self.max_depth:
            return
        for href in response.css("a::attr(href)").getall():
            if len(self.seen) >= self.max_pages:
                return
            candidate = normalize_url(response.urljoin(href))
            if candidate in self.seen or not self._should_follow(candidate):
                continue
            self.seen.add(candidate)
            cached = self._cache_get(candidate)
            if cached is not None:
                self._cache_hits += 1
                cached_response = HtmlResponse(
                    url=cached["final_url"],
                    body=cached["html"].encode("utf-8"),
                    encoding="utf-8",
                    request=scrapy.Request(candidate),
                )
                cached_response.meta["depth"] = response.meta.get("depth", 0) + 1
                for item in self.parse_page(cached_response):
                    yield item
            else:
                self._cache_misses += 1
                req = self._request(candidate)
                req.meta["depth"] = response.meta.get("depth", 0) + 1
                yield req

    def _should_follow(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.netloc not in self.allowed_domains:
            return False
        if self.base_path and not (
            parsed.path == self.base_path
            or parsed.path.startswith(self.base_path + "/")
        ):
            return False
        if NON_PAGE_EXTENSIONS.search(parsed.path):
            return False
        if self.lang and not self._lang_matches(parsed.path):
            return False
        return True

    def _lang_matches(self, path: str) -> bool:
        segments = [s for s in path.split("/") if s]
        if not segments:
            return True
        first = segments[0]
        if not LANG_SEGMENT.match(first):
            return True
        return first == self.lang or first.startswith(self.lang + "-")

    def on_error(self, failure) -> None:
        self.logger.warning(
            "failed to fetch %s: %s", failure.request.url, failure.value
        )
