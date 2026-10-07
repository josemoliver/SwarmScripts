#!/usr/bin/env python3
"""Capture the Swarm web credentials (userid, wsid, oauth_token) for your own account.

Opens a real browser at https://swarmapp.com/history. You log in yourself (the
script never sees your password), and it watches the page's own requests to the
Foursquare API, pulling the parameters out of the first authenticated call. They
are saved to config.json in the format get_swarm_checkins.py expects.

Setup:
    pip install playwright
    playwright install chromium

Usage:
    python get_swarm_credentials.py [--output config.json] [--timeout 300]

Note: the saved token is a live credential for your account. Keep config.json
out of version control and don't share it.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HISTORY_URL = "https://swarmapp.com/history"
API_PATH = re.compile(r"^/v2/users/(\d+)/historysearch")


def extract(url: str):
    """Return the credentials dict if `url` is an authenticated historysearch call."""
    parsed = urlparse(url)
    if parsed.hostname != "api.foursquare.com":
        return None
    match = API_PATH.match(parsed.path)
    if not match:
        return None
    query = parse_qs(parsed.query)
    wsid = query.get("wsid", [None])[0]
    token = query.get("oauth_token", [None])[0]
    if not (wsid and token):
        return None
    return {"userid": match.group(1), "wsid": wsid, "oauth_token": token}


def capture(timeout_s: int):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright is required: pip install playwright && playwright install chromium")

    found = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_context().new_page()

        def on_request(request):
            if not found:
                creds = extract(request.url)
                if creds:
                    found.update(creds)

        page.on("request", on_request)
        page.goto(HISTORY_URL)
        print(
            "Log in to Swarm in the browser window, then pick any month in the "
            "history dropdown if nothing loads. Waiting for credentials...",
            file=sys.stderr,
        )

        waited = 0
        while not found and waited < timeout_s:
            page.wait_for_timeout(500)
            waited += 0.5
        browser.close()

    if not found:
        sys.exit(f"Timed out after {timeout_s}s without seeing an authenticated history request.")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default=Path(__file__).resolve().parent / "config.json")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for login")
    args = parser.parse_args()

    creds = capture(args.timeout)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=4)
    print(f"Saved credentials for user {creds['userid']} to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
