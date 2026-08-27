import argparse
import json
import logging
import sys

from scrapy.crawler import CrawlerProcess

from docs_mcp.config import settings
from docs_mcp.scraper.spider import DocsSpider


class JsonlStdoutPipeline:
    def process_item(self, item, spider):
        sys.stdout.write(json.dumps(item, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return item


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docs-mcp-crawl")
    parser.add_argument("--url", required=True, help="Entry point URL of the docs site")
    parser.add_argument("--depth", type=int, default=settings.crawl_max_depth)
    parser.add_argument("--max-pages", type=int, default=settings.crawl_max_pages)
    parser.add_argument("--delay", type=float, default=settings.crawl_delay)
    parser.add_argument("--user-agent", default=settings.user_agent)
    parser.add_argument("--cache-dir", default=settings.crawl_cache_dir)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    cache_dir = None if args.no_cache else args.cache_dir

    process = CrawlerProcess(
        settings={
            "ITEM_PIPELINES": {"docs_mcp.scraper.runner.JsonlStdoutPipeline": 100},
            "LOG_LEVEL": "WARNING",
            "DOWNLOAD_DELAY": args.delay,
            "USER_AGENT": args.user_agent,
        },
        install_root_handler=False,
    )
    process.crawl(
        DocsSpider,
        base_url=args.url,
        max_depth=args.depth,
        max_pages=args.max_pages,
        cache_dir=cache_dir,
    )
    process.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
