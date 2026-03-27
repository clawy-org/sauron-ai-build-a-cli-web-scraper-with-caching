# scraper — CLI Web Scraper with Caching

A command-line web scraper that fetches pages, extracts readable text, and caches results locally.

**Zero external dependencies** — uses only Python stdlib (`urllib`, `html.parser`, `argparse`, `json`, `hashlib`).

## Usage

```bash
# Fetch a URL and extract text (default)
python scraper.py fetch https://example.com

# Output raw HTML instead
python scraper.py fetch https://example.com --raw

# Save output to a file
python scraper.py fetch https://example.com --save output.txt

# Bypass cache
python scraper.py fetch https://example.com --no-cache

# Set cache TTL (in seconds) and request timeout
python scraper.py fetch https://example.com --max-age 60 --timeout 5
```

### Cache Management

```bash
# List all cached URLs with timestamps
python scraper.py cache list

# Get cached version of a URL
python scraper.py cache get https://example.com

# Clear the entire cache
python scraper.py cache clear
```

## Features

- **Text extraction**: Strips HTML tags, scripts, styles, and `<head>` content; preserves paragraph breaks; decodes HTML entities
- **Local caching**: Responses cached to `~/.cache/scraper/` as JSON files keyed by URL hash
- **Cache TTL**: Default 1 hour, configurable with `--max-age`
- **Raw mode**: `--raw` outputs original HTML
- **File output**: `--save` writes to disk
- **Error handling**: Graceful timeouts, HTTP errors, connection failures — all return non-zero exit codes
- **Conditional imports in try/except**: Handles edge cases in real-world HTML

## Cache Format

Each cached entry is a JSON file in `~/.cache/scraper/`:

```json
{
  "url": "https://example.com",
  "fetched_at": "2025-03-27T21:00:00+00:00",
  "status": 200,
  "headers": {"Content-Type": "text/html"},
  "body": "<html>..."
}
```

## Running Tests

```bash
python -m unittest test_scraper -v
```

42 tests covering text extraction, caching logic, CLI parsing, fetch with mocked HTTP, error handling, and the main entry point.
