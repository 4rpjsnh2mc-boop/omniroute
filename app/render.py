#!/usr/bin/env python3
"""Открывает страницу во встроенном браузере (Chromium через Playwright) и печатает готовый HTML.

Нужен для площадок, которые показывают закупку только после выполнения JavaScript
(например, Bidzaar). Запросы во внутреннюю сеть блокируются.

Использование:  render.py <url>
"""

import ipaddress
import os
import socket
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

_cache = {}


def internal(host):
    if not host:
        return True
    if host in _cache:
        return _cache[host]
    bad = host == "localhost" or host.endswith(".local") or host.endswith(".internal")
    if not bad:
        try:
            for info in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                    bad = True
                    break
        except (socket.gaierror, ValueError):
            bad = True
    _cache[host] = bad
    return bad


def main():
    url = sys.argv[1]
    if internal(urllib.parse.urlsplit(url).hostname):
        sys.exit(3)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
                                    executable_path=os.environ.get("TENDER_CHROMIUM") or None)
        ctx = browser.new_context(locale="ru-RU", user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"))
        page = ctx.new_page()

        def guard(route):
            u = urllib.parse.urlsplit(route.request.url)
            if u.scheme not in ("http", "https", "data", "blob") or (u.scheme in ("http", "https") and internal(u.hostname)):
                return route.abort()
            if route.request.resource_type in ("image", "media", "font"):
                return route.abort()  # быстрее и меньше трафика
            return route.continue_()

        page.route("**/*", guard)
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:
            pass  # берём то, что успело загрузиться
        page.wait_for_timeout(1500)
        sys.stdout.write(page.content())
        browser.close()


if __name__ == "__main__":
    main()
