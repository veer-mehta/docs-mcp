import json
import sys


class JsonlStdoutPipeline:
    def open_spider(self, spider):
        sys.stderr.write(f"JsonlStdoutPipeline opened for {spider.name}\n")

    def process_item(self, item, spider):
        line = json.dumps(item, ensure_ascii=False) + "\n"
        sys.stderr.write(f"JsonlStdoutPipeline: emitting item {item.get('url', '?')}\n")
        sys.stderr.flush()
        try:
            sys.stdout.write(line)
            sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"JsonlStdoutPipeline: write failed: {e}\n")
        return item
