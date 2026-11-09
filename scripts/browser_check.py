#!/usr/bin/env python3
"""Optional Playwright smoke test for the MkDocs Material site.

Install playwright separately. Use --url for the deployed Pages project prefix.
"""
import argparse
import pathlib
import tempfile
import urllib.parse

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8126/")
    args = parser.parse_args()
    base = args.url.rstrip("/") + "/"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base, wait_until="networkidle")
        expect(page.locator(".md-content h1")).to_contain_text("从零理解")
        search = page.locator('input[data-md-component="search-query"]')
        # Wait for the theme's worker/index initialization on a cold remote visit.
        expect(page.locator('.md-search-result__meta')).not_to_contain_text('正在初始化', timeout=45000)
        search.fill("背压")
        expect(page.locator(".md-search-result__link").first).to_be_visible(timeout=45000)
        href = page.locator(".md-search-result__link").first.get_attribute("href")
        assert urllib.parse.urlsplit(urllib.parse.urljoin(page.url, href)).path.startswith(urllib.parse.urlsplit(base).path)
        page.keyboard.press("Escape")
        lab_url = urllib.parse.urljoin(base, "docs/book/part-4/19-lab/")
        page.goto(lab_url, wait_until="networkidle")
        expect(page.locator(".md-content h1")).to_contain_text("第 19 章")
        expect(page.locator('.md-sidebar--secondary nav a').first).to_be_visible()
        copy = page.locator(".md-clipboard").first
        expect(copy).to_be_visible()
        copy.click()
        clipboard = page.evaluate("navigator.clipboard.readText()")
        assert "dotnet" in clipboard or "python" in clipboard
        page.locator('article a[href*="Protocol.cs"]').first.click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".md-content h1")).to_contain_text("Protocol.cs")
        expect(page.locator(".highlight")).to_be_visible()
        page.goto(base, wait_until="networkidle")
        desktop = pathlib.Path(tempfile.gettempdir()) / "kestrel-handbook-desktop.png"
        page.screenshot(path=str(desktop), full_page=False)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "desktop horizontal overflow"
        page.set_viewport_size({"width": 390, "height": 844})
        toggle = page.locator('header label[for="__drawer"]')
        expect(toggle).to_be_visible()
        toggle.click()
        expect(page.locator("#__drawer")).to_be_checked()
        # Material uses nested drill-down groups on mobile; open the first part first.
        page.locator('label.md-nav__link[for="__nav_2"]').click()
        page.locator('.md-sidebar--primary a.md-nav__link[href*="01-network"]').first.click()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".md-content h1")).to_contain_text("第 1 章")
        expect(page.locator("#__drawer")).not_to_be_checked()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile horizontal overflow"
        mobile = pathlib.Path(tempfile.gettempdir()) / "kestrel-handbook-mobile.png"
        page.screenshot(path=str(mobile), full_page=False)
        page.locator('label[for="__search"]').first.click()
        search.fill("Memory")
        expect(page.locator(".md-search-result__link").first).to_be_visible()
        assert not errors, errors
        print("PASS Material browser: desktop/mobile navigation, Chinese search, clipboard, source pages, no overflow or page errors")
        print("Screenshots:", desktop, mobile)
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
