#!/usr/bin/env python3
"""
scraper - CLI web scraper with local caching.

Fetches web pages, extracts readable text, and caches results locally.
Uses only Python stdlib.
"""

import argparse
import hashlib
import html
import html.parser
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "scraper")
DEFAULT_MAX_AGE = 3600  # 1 hour
DEFAULT_TIMEOUT = 10


class TextExtractor(html.parser.HTMLParser):
    """Extract readable text from HTML, stripping tags, scripts, and styles."""

    SKIP_TAGS = {"script", "style", "head", "meta", "link", "noscript"}
    BLOCK_TAGS = {
        "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "tr", "blockquote", "pre", "hr", "section", "article",
        "header", "footer", "nav", "main", "aside", "figure",
        "figcaption", "details", "summary", "table", "thead", "tbody",
    }

    def __init__(self):
        super().__init__()
        self._pieces = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag_lower in self.BLOCK_TAGS and not self._skip_depth:
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag_lower in self.BLOCK_TAGS and not self._skip_depth:
            self._pieces.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._pieces.append(data)

    def handle_entityref(self, name):
        if not self._skip_depth:
            char = html.unescape(f"&{name};")
            self._pieces.append(char)

    def handle_charref(self, name):
        if not self._skip_depth:
            char = html.unescape(f"&#{name};")
            self._pieces.append(char)

    def get_text(self):
        raw = "".join(self._pieces)
        # Collapse multiple blank lines into at most two newlines
        lines = raw.split("\n")
        result = []
        blank_count = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                blank_count += 1
                if blank_count <= 1:
                    result.append("")
            else:
                blank_count = 0
                result.append(stripped)
        return "\n".join(result).strip()


def extract_text(html_content):
    """Extract readable text from HTML string."""
    extractor = TextExtractor()
    extractor.feed(html_content)
    return extractor.get_text()


def url_hash(url):
    """Generate a hash key for a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def get_cache_path(url):
    """Get the cache file path for a URL."""
    return os.path.join(CACHE_DIR, f"{url_hash(url)}.json")


def save_to_cache(url, status, headers, body):
    """Save a response to the cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    entry = {
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "headers": dict(headers),
        "body": body,
    }
    cache_path = get_cache_path(url)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
    return entry


def load_from_cache(url, max_age=DEFAULT_MAX_AGE):
    """Load a cached response if it exists and hasn't expired.
    Returns the cache entry dict or None.
    """
    cache_path = get_cache_path(url)
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            entry = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Check TTL
    fetched_at = entry.get("fetched_at", "")
    try:
        fetched_time = datetime.fromisoformat(fetched_at)
        age = (datetime.now(timezone.utc) - fetched_time).total_seconds()
        if age > max_age:
            return None
    except (ValueError, TypeError):
        return None

    return entry


def list_cache():
    """List all cached URLs with timestamps."""
    if not os.path.isdir(CACHE_DIR):
        return []

    entries = []
    for fname in sorted(os.listdir(CACHE_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(CACHE_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                entry = json.load(f)
            entries.append({
                "url": entry.get("url", "unknown"),
                "fetched_at": entry.get("fetched_at", "unknown"),
                "status": entry.get("status", "?"),
                "file": fname,
            })
        except (json.JSONDecodeError, OSError):
            continue
    return entries


def clear_cache():
    """Remove all cached files."""
    if not os.path.isdir(CACHE_DIR):
        return 0

    count = 0
    for fname in os.listdir(CACHE_DIR):
        if fname.endswith(".json"):
            os.remove(os.path.join(CACHE_DIR, fname))
            count += 1
    return count


def fetch_url(url, timeout=DEFAULT_TIMEOUT):
    """Fetch a URL and return (status, headers, body).
    Raises on network/HTTP errors.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "scraper/1.0 (Python stdlib)"},
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    status = resp.status
    headers = {k: v for k, v in resp.getheaders()}
    body = resp.read().decode("utf-8", errors="replace")
    return status, headers, body


def cmd_fetch(args):
    """Handle the 'fetch' subcommand."""
    url = args.url

    # Check cache first (unless --no-cache)
    if not args.no_cache:
        cached = load_from_cache(url, max_age=args.max_age)
        if cached is not None:
            body = cached["body"]
            if not args.raw:
                body = extract_text(body)
            if args.save:
                with open(args.save, "w", encoding="utf-8") as f:
                    f.write(body)
                print(f"Saved to {args.save} (from cache)")
            else:
                print(body)
            return 0

    # Fetch from network
    try:
        status, headers, body = fetch_url(url, timeout=args.timeout)
    except urllib.error.HTTPError as e:
        print(f"HTTP error {e.code}: {e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        return 1
    except TimeoutError:
        print(f"Timeout after {args.timeout}s", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Cache the response
    if not args.no_cache:
        save_to_cache(url, status, headers, body)

    # Output
    output = body if args.raw else extract_text(body)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved to {args.save}")
    else:
        print(output)
    return 0


def cmd_cache(args):
    """Handle the 'cache' subcommand."""
    action = args.cache_action

    if action == "list":
        entries = list_cache()
        if not entries:
            print("Cache is empty.")
            return 0
        for e in entries:
            print(f"  {e['fetched_at']}  [{e['status']}]  {e['url']}")
        print(f"\n{len(entries)} cached entries")
        return 0

    elif action == "clear":
        count = clear_cache()
        print(f"Cleared {count} cached entries.")
        return 0

    elif action == "get":
        if not args.url:
            print("Error: 'cache get' requires a URL", file=sys.stderr)
            return 1
        # Load from cache with very large max_age (don't expire for explicit get)
        cached = load_from_cache(args.url, max_age=10**9)
        if cached is None:
            print(f"No cache entry for: {args.url}", file=sys.stderr)
            return 1
        body = cached["body"]
        print(f"Cached at: {cached['fetched_at']}")
        print(f"Status: {cached['status']}")
        print(f"---")
        print(extract_text(body))
        return 0

    else:
        print(f"Unknown cache action: {action}", file=sys.stderr)
        return 1


def build_parser():
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="scraper",
        description="CLI web scraper with local caching",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # fetch subcommand
    fetch_parser = subparsers.add_parser("fetch", help="Fetch a URL")
    fetch_parser.add_argument("url", help="URL to fetch")
    fetch_parser.add_argument("--raw", action="store_true", help="Output raw HTML")
    fetch_parser.add_argument("--save", metavar="FILE", help="Save output to file")
    fetch_parser.add_argument("--no-cache", action="store_true", help="Bypass cache")
    fetch_parser.add_argument(
        "--max-age", type=int, default=DEFAULT_MAX_AGE,
        help=f"Cache TTL in seconds (default: {DEFAULT_MAX_AGE})"
    )
    fetch_parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT})"
    )

    # cache subcommand
    cache_parser = subparsers.add_parser("cache", help="Manage the cache")
    cache_parser.add_argument(
        "cache_action", choices=["list", "clear", "get"],
        help="Cache operation"
    )
    cache_parser.add_argument("url", nargs="?", help="URL (for 'get')")

    return parser


def main(argv=None):
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "fetch":
        return cmd_fetch(args)
    elif args.command == "cache":
        return cmd_cache(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
