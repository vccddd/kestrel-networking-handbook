#!/usr/bin/env python3
"""Validate generated site links, fragments, search routes, and safety boundaries."""
import json
import pathlib
import urllib.parse
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.ids = set()
        self.main = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "main":
            self.main = True
        for key in ("href", "src"):
            if key in attrs:
                self.links.append(attrs[key])


def main():
    errors = []
    pages = {}
    for path in SITE.rglob("*.html"):
        page = Page()
        page.feed(path.read_text(encoding="utf-8"))
        pages[path.resolve()] = page
        if not page.main:
            errors.append(f"Missing main: {path.relative_to(SITE)}")
    checked = 0
    for path, page in pages.items():
        for link in page.links:
            parts = urllib.parse.urlsplit(link)
            if parts.scheme or link.startswith("//"):
                continue
            decoded = urllib.parse.unquote(parts.path)
            if decoded.startswith('/kestrel-networking-handbook/'):
                destination = (SITE / decoded.removeprefix('/kestrel-networking-handbook/')).resolve()
            else:
                destination = (path.parent / decoded).resolve() if decoded else path
            if destination.is_dir():
                destination /= "index.html"
            if not destination.is_relative_to(SITE.resolve()):
                errors.append(f"Link escapes site: {path.relative_to(SITE)}: {link}")
            elif not destination.exists():
                errors.append(f"Missing target: {path.relative_to(SITE)}: {link}")
            elif parts.fragment and destination.suffix == ".html":
                fragment = urllib.parse.unquote(parts.fragment)
                # Source line ranges are reduced to the first valid anchor by the builder.
                if fragment not in pages[destination].ids:
                    errors.append(f"Missing fragment: {path.relative_to(SITE)}: {link}")
            checked += 1
    search = json.loads((SITE / "search/search_index.json").read_text(encoding="utf-8"))["docs"]
    book_pages = {item["location"].split("#")[0] for item in search if
                  item["location"].startswith("docs/book/") or item["location"] in {"", ".", "index.html"}}
    if len(book_pages) != 27:
        errors.append(f"Expected 27 searchable book pages, found {len(book_pages)}")
    for item in search:
        parts = urllib.parse.urlsplit(item["location"])
        target = SITE / urllib.parse.unquote(parts.path)
        if target.is_dir():
            target /= "index.html"
        if not target.is_file() or not item["title"]:
            errors.append(f"Invalid search entry: {item.get('title')}")
    forbidden = [p for p in SITE.rglob("*") if p.suffix in {".key", ".pfx", ".p12", ".pem"}]
    if forbidden:
        errors.append("Certificate/key assets found in reading site")
    if errors:
        raise SystemExit("FAIL\n" + "\n".join(errors))
    print(f"PASS {len(pages)} rendered pages, {checked} local links/fragments, 27 searchable book pages; no certificate assets.")


if __name__ == "__main__":
    main()
