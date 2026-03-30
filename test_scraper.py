#!/usr/bin/env python3
"""Comprehensive tests for scraper.py using mocked HTTP responses."""

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
import urllib.error
from io import StringIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

import scraper


class TestTextExtractor(unittest.TestCase):
    """Test HTML text extraction."""

    def test_plain_text(self):
        result = scraper.extract_text("<p>Hello world</p>")
        self.assertEqual(result, "Hello world")

    def test_strips_tags(self):
        result = scraper.extract_text("<b>bold</b> and <i>italic</i>")
        self.assertEqual(result, "bold and italic")

    def test_strips_scripts(self):
        html = "<p>Before</p><script>alert('xss')</script><p>After</p>"
        result = scraper.extract_text(html)
        self.assertIn("Before", result)
        self.assertIn("After", result)
        self.assertNotIn("alert", result)

    def test_strips_styles(self):
        html = "<style>body{color:red}</style><p>Content</p>"
        result = scraper.extract_text(html)
        self.assertIn("Content", result)
        self.assertNotIn("color", result)

    def test_preserves_paragraph_breaks(self):
        html = "<p>First paragraph</p><p>Second paragraph</p>"
        result = scraper.extract_text(html)
        self.assertIn("\n", result)
        self.assertIn("First paragraph", result)
        self.assertIn("Second paragraph", result)

    def test_decodes_html_entities(self):
        html = "<p>Tom &amp; Jerry &lt;3</p>"
        result = scraper.extract_text(html)
        self.assertIn("Tom & Jerry <3", result)

    def test_numeric_entities(self):
        html = "<p>&#169; 2025</p>"
        result = scraper.extract_text(html)
        self.assertIn("©", result)

    def test_nested_skip_tags(self):
        html = "<script><script>nested</script></script><p>Visible</p>"
        result = scraper.extract_text(html)
        self.assertIn("Visible", result)
        self.assertNotIn("nested", result)

    def test_empty_html(self):
        result = scraper.extract_text("")
        self.assertEqual(result, "")

    def test_br_tags(self):
        html = "Line one<br>Line two"
        result = scraper.extract_text(html)
        self.assertIn("Line one", result)
        self.assertIn("Line two", result)

    def test_head_stripped(self):
        html = "<html><head><title>Test</title></head><body><p>Body</p></body></html>"
        result = scraper.extract_text(html)
        self.assertIn("Body", result)
        self.assertNotIn("Test", result)

    def test_real_world_html_with_meta_tags(self):
        """Regression: meta/link are void elements - must not break skip depth."""
        html = textwrap.dedent("""\
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width">
            <link rel="stylesheet" href="style.css">
            <title>Test Page</title>
        </head>
        <body>
            <p>This text must be visible</p>
        </body>
        </html>
        """)
        result = scraper.extract_text(html)
        self.assertIn("This text must be visible", result)
        self.assertNotIn("Test Page", result)

    def test_multiple_meta_tags_dont_suppress_body(self):
        """Many meta tags should not suppress any body content."""
        html = (
            "<html><head>"
            '<meta charset="utf-8">'
            '<meta name="description" content="Test">'
            '<meta name="robots" content="index">'
            '<meta property="og:title" content="OG">'
            '<link rel="icon" href="favicon.ico">'
            '<link rel="canonical" href="https://example.com">'
            "</head><body><h1>Welcome</h1><p>Paragraph text</p></body></html>"
        )
        result = scraper.extract_text(html)
        self.assertIn("Welcome", result)
        self.assertIn("Paragraph text", result)

    def test_void_elements_outside_head(self):
        """img, input, etc. as void elements should not affect skip depth."""
        html = '<p>Before</p><img src="test.png"><p>After</p>'
        result = scraper.extract_text(html)
        self.assertIn("Before", result)
        self.assertIn("After", result)

    def test_noscript_stripped(self):
        html = "<noscript>Enable JS</noscript><p>Content</p>"
        result = scraper.extract_text(html)
        self.assertIn("Content", result)
        self.assertNotIn("Enable JS", result)


class TestUrlHash(unittest.TestCase):
    def test_consistent_hash(self):
        h1 = scraper.url_hash("https://example.com")
        h2 = scraper.url_hash("https://example.com")
        self.assertEqual(h1, h2)

    def test_different_urls_different_hash(self):
        h1 = scraper.url_hash("https://example.com/a")
        h2 = scraper.url_hash("https://example.com/b")
        self.assertNotEqual(h1, h2)

    def test_hash_length(self):
        h = scraper.url_hash("https://example.com")
        self.assertEqual(len(h), 16)


class TestCaching(unittest.TestCase):
    """Test cache operations with a temp cache dir."""

    def setUp(self):
        self.orig_cache_dir = scraper.CACHE_DIR
        self.tmpdir = tempfile.mkdtemp()
        scraper.CACHE_DIR = self.tmpdir

    def tearDown(self):
        scraper.CACHE_DIR = self.orig_cache_dir
        shutil.rmtree(self.tmpdir)

    def test_save_and_load(self):
        url = "https://example.com"
        scraper.save_to_cache(url, 200, {"Content-Type": "text/html"}, "<p>Hello</p>")
        cached = scraper.load_from_cache(url, max_age=3600)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["url"], url)
        self.assertEqual(cached["status"], 200)
        self.assertIn("Hello", cached["body"])

    def test_cache_miss(self):
        cached = scraper.load_from_cache("https://nonexistent.example.com")
        self.assertIsNone(cached)

    def test_cache_expiry(self):
        url = "https://example.com"
        scraper.save_to_cache(url, 200, {}, "body")
        # Load with max_age=0 should miss
        cached = scraper.load_from_cache(url, max_age=0)
        self.assertIsNone(cached)

    def test_list_cache(self):
        scraper.save_to_cache("https://a.com", 200, {}, "a")
        scraper.save_to_cache("https://b.com", 200, {}, "b")
        entries = scraper.list_cache()
        self.assertEqual(len(entries), 2)
        urls = {e["url"] for e in entries}
        self.assertIn("https://a.com", urls)
        self.assertIn("https://b.com", urls)

    def test_list_empty_cache(self):
        entries = scraper.list_cache()
        self.assertEqual(len(entries), 0)

    def test_clear_cache(self):
        scraper.save_to_cache("https://a.com", 200, {}, "a")
        scraper.save_to_cache("https://b.com", 200, {}, "b")
        count = scraper.clear_cache()
        self.assertEqual(count, 2)
        entries = scraper.list_cache()
        self.assertEqual(len(entries), 0)

    def test_clear_empty_cache(self):
        count = scraper.clear_cache()
        self.assertEqual(count, 0)

    def test_corrupted_cache_file(self):
        url = "https://example.com"
        cache_path = scraper.get_cache_path(url)
        with open(cache_path, "w") as f:
            f.write("not json!")
        cached = scraper.load_from_cache(url)
        self.assertIsNone(cached)


class TestCLIParsing(unittest.TestCase):
    """Test argument parsing."""

    def test_fetch_basic(self):
        parser = scraper.build_parser()
        args = parser.parse_args(["fetch", "https://example.com"])
        self.assertEqual(args.command, "fetch")
        self.assertEqual(args.url, "https://example.com")
        self.assertFalse(args.raw)
        self.assertFalse(args.no_cache)

    def test_fetch_with_flags(self):
        parser = scraper.build_parser()
        args = parser.parse_args([
            "fetch", "https://example.com",
            "--raw", "--no-cache", "--save", "out.txt",
            "--max-age", "60", "--timeout", "5"
        ])
        self.assertTrue(args.raw)
        self.assertTrue(args.no_cache)
        self.assertEqual(args.save, "out.txt")
        self.assertEqual(args.max_age, 60)
        self.assertEqual(args.timeout, 5)

    def test_cache_list(self):
        parser = scraper.build_parser()
        args = parser.parse_args(["cache", "list"])
        self.assertEqual(args.command, "cache")
        self.assertEqual(args.cache_action, "list")

    def test_cache_clear(self):
        parser = scraper.build_parser()
        args = parser.parse_args(["cache", "clear"])
        self.assertEqual(args.cache_action, "clear")

    def test_cache_get(self):
        parser = scraper.build_parser()
        args = parser.parse_args(["cache", "get", "https://example.com"])
        self.assertEqual(args.cache_action, "get")
        self.assertEqual(args.url, "https://example.com")


class TestFetchCommand(unittest.TestCase):
    """Test the fetch command with mocked HTTP."""

    def setUp(self):
        self.orig_cache_dir = scraper.CACHE_DIR
        self.tmpdir = tempfile.mkdtemp()
        scraper.CACHE_DIR = self.tmpdir

    def tearDown(self):
        scraper.CACHE_DIR = self.orig_cache_dir
        shutil.rmtree(self.tmpdir)

    @patch("scraper.fetch_url")
    def test_fetch_extracts_text(self, mock_fetch):
        mock_fetch.return_value = (200, {"Content-Type": "text/html"}, "<p>Hello World</p>")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            parser = scraper.build_parser()
            args = parser.parse_args(["fetch", "https://example.com", "--no-cache"])
            result = scraper.cmd_fetch(args)
        self.assertEqual(result, 0)
        self.assertIn("Hello World", mock_out.getvalue())

    @patch("scraper.fetch_url")
    def test_fetch_raw(self, mock_fetch):
        html_body = "<p>Hello <b>World</b></p>"
        mock_fetch.return_value = (200, {}, html_body)
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            parser = scraper.build_parser()
            args = parser.parse_args(["fetch", "https://example.com", "--raw", "--no-cache"])
            result = scraper.cmd_fetch(args)
        self.assertEqual(result, 0)
        self.assertIn("<p>Hello <b>World</b></p>", mock_out.getvalue())

    @patch("scraper.fetch_url")
    def test_fetch_caches_response(self, mock_fetch):
        mock_fetch.return_value = (200, {}, "<p>Cached content</p>")
        parser = scraper.build_parser()

        # First fetch — hits network
        with patch("sys.stdout", new_callable=StringIO):
            args = parser.parse_args(["fetch", "https://example.com"])
            scraper.cmd_fetch(args)
        self.assertEqual(mock_fetch.call_count, 1)

        # Second fetch — should use cache, not call fetch_url again
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            args = parser.parse_args(["fetch", "https://example.com"])
            scraper.cmd_fetch(args)
        self.assertEqual(mock_fetch.call_count, 1)  # Still 1 — used cache
        self.assertIn("Cached content", mock_out.getvalue())

    @patch("scraper.fetch_url")
    def test_no_cache_flag_bypasses(self, mock_fetch):
        mock_fetch.return_value = (200, {}, "<p>Fresh</p>")
        parser = scraper.build_parser()

        # First fetch (caches)
        with patch("sys.stdout", new_callable=StringIO):
            args = parser.parse_args(["fetch", "https://example.com"])
            scraper.cmd_fetch(args)

        # Second fetch with --no-cache
        with patch("sys.stdout", new_callable=StringIO):
            args = parser.parse_args(["fetch", "https://example.com", "--no-cache"])
            scraper.cmd_fetch(args)
        self.assertEqual(mock_fetch.call_count, 2)  # Called twice

    @patch("scraper.fetch_url")
    def test_fetch_save_to_file(self, mock_fetch):
        mock_fetch.return_value = (200, {}, "<p>Saved text</p>")
        outfile = os.path.join(self.tmpdir, "output.txt")
        parser = scraper.build_parser()
        with patch("sys.stdout", new_callable=StringIO):
            args = parser.parse_args(["fetch", "https://example.com", "--save", outfile, "--no-cache"])
            result = scraper.cmd_fetch(args)
        self.assertEqual(result, 0)
        with open(outfile) as f:
            content = f.read()
        self.assertIn("Saved text", content)

    @patch("scraper.fetch_url")
    def test_fetch_http_error(self, mock_fetch):
        mock_fetch.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None
        )
        parser = scraper.build_parser()
        with patch("sys.stderr", new_callable=StringIO) as mock_err:
            args = parser.parse_args(["fetch", "https://example.com", "--no-cache"])
            result = scraper.cmd_fetch(args)
        self.assertEqual(result, 1)
        self.assertIn("404", mock_err.getvalue())

    @patch("scraper.fetch_url")
    def test_fetch_connection_error(self, mock_fetch):
        mock_fetch.side_effect = urllib.error.URLError("Connection refused")
        parser = scraper.build_parser()
        with patch("sys.stderr", new_callable=StringIO) as mock_err:
            args = parser.parse_args(["fetch", "https://bad.example.com", "--no-cache"])
            result = scraper.cmd_fetch(args)
        self.assertEqual(result, 1)

    @patch("scraper.fetch_url")
    def test_fetch_timeout(self, mock_fetch):
        mock_fetch.side_effect = TimeoutError()
        parser = scraper.build_parser()
        with patch("sys.stderr", new_callable=StringIO) as mock_err:
            args = parser.parse_args(["fetch", "https://slow.example.com", "--no-cache"])
            result = scraper.cmd_fetch(args)
        self.assertEqual(result, 1)
        self.assertIn("Timeout", mock_err.getvalue())

    @patch("scraper.fetch_url")
    def test_fetch_real_world_html(self, mock_fetch):
        """Regression: ensure real-world HTML with meta tags extracts text."""
        real_html = textwrap.dedent("""\
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="/css/main.css">
            <title>Example Domain</title>
        </head>
        <body>
            <h1>Example Domain</h1>
            <p>This domain is for use in illustrative examples.</p>
        </body>
        </html>
        """)
        mock_fetch.return_value = (200, {"Content-Type": "text/html"}, real_html)
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            parser = scraper.build_parser()
            args = parser.parse_args(["fetch", "https://example.com", "--no-cache"])
            result = scraper.cmd_fetch(args)
        self.assertEqual(result, 0)
        output = mock_out.getvalue()
        self.assertIn("Example Domain", output)
        self.assertIn("illustrative examples", output)


class TestCacheCommand(unittest.TestCase):
    """Test cache subcommands."""

    def setUp(self):
        self.orig_cache_dir = scraper.CACHE_DIR
        self.tmpdir = tempfile.mkdtemp()
        scraper.CACHE_DIR = self.tmpdir

    def tearDown(self):
        scraper.CACHE_DIR = self.orig_cache_dir
        shutil.rmtree(self.tmpdir)

    def test_cache_list_empty(self):
        parser = scraper.build_parser()
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            args = parser.parse_args(["cache", "list"])
            result = scraper.cmd_cache(args)
        self.assertEqual(result, 0)
        self.assertIn("empty", mock_out.getvalue())

    def test_cache_list_with_entries(self):
        scraper.save_to_cache("https://example.com", 200, {}, "body")
        parser = scraper.build_parser()
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            args = parser.parse_args(["cache", "list"])
            result = scraper.cmd_cache(args)
        self.assertEqual(result, 0)
        self.assertIn("example.com", mock_out.getvalue())

    def test_cache_clear(self):
        scraper.save_to_cache("https://example.com", 200, {}, "body")
        parser = scraper.build_parser()
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            args = parser.parse_args(["cache", "clear"])
            result = scraper.cmd_cache(args)
        self.assertEqual(result, 0)
        self.assertIn("Cleared 1", mock_out.getvalue())

    def test_cache_get_hit(self):
        scraper.save_to_cache("https://example.com", 200, {}, "<p>Cached body</p>")
        parser = scraper.build_parser()
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            args = parser.parse_args(["cache", "get", "https://example.com"])
            result = scraper.cmd_cache(args)
        self.assertEqual(result, 0)
        self.assertIn("Cached body", mock_out.getvalue())

    def test_cache_get_miss(self):
        parser = scraper.build_parser()
        with patch("sys.stderr", new_callable=StringIO) as mock_err:
            args = parser.parse_args(["cache", "get", "https://nonexistent.com"])
            result = scraper.cmd_cache(args)
        self.assertEqual(result, 1)


class TestMainEntrypoint(unittest.TestCase):
    """Test the main() function."""

    def test_no_command(self):
        with patch("sys.stdout", new_callable=StringIO):
            result = scraper.main([])
        self.assertEqual(result, 1)

    @patch("scraper.fetch_url")
    def test_main_fetch(self, mock_fetch):
        mock_fetch.return_value = (200, {}, "<p>Main test</p>")
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = scraper.main(["fetch", "https://example.com", "--no-cache"])
        self.assertEqual(result, 0)
        self.assertIn("Main test", mock_out.getvalue())


if __name__ == "__main__":
    unittest.main()
