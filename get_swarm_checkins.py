"""
get_swarm_checkins.py - Download your Foursquare/Swarm checkin history to JSON.

PURPOSE
    Queries the Foursquare "historysearch" endpoint (the same one the Swarm web
    app uses) one calendar month at a time, from January of --start-year up to
    now. Each month is paged with limit/offset until a short page is returned,
    and all checkins are written to a single JSON file (a list of checkin
    objects exactly as the API returns them, newest first within each month,
    months in chronological order).

USAGE
    python get_swarm_checkins.py [--config PATH] [--start-year YEAR]
                                 [--output PATH] [--limit N]

ARGUMENTS
    --config PATH       JSON config file with credentials (default: config.json)
    --start-year YEAR   First year to fetch, starting January 1st
                        (default: 2017; set to the year your account began)
    --output PATH       Output JSON file, overwritten if it exists
                        (default: checkins_all.json)
    --limit N           Checkins requested per API call, i.e. the page size
                        (default: 500)

INPUTS
    Config file (JSON) must contain all of:
        userid       Foursquare numeric user id
        wsid         Web session id from a logged-in swarmapp.com session
        oauth_token  OAuth token from a logged-in swarmapp.com session
    Credentials can be obtained with get_swarm_credentials.py in this folder.

DEPENDENCIES
    Python 3.8+ and the third-party "requests" package (pip install requests).

PREREQUISITES
    - Network access to api.foursquare.com.
    - A valid, unexpired wsid/oauth_token; expired credentials make every
      month fail with an API error.
    - Write permission for the output path.

BEHAVIOR AND EXIT CODES
    - Network errors are retried up to 3 times with exponential backoff
      (2s, 4s); API errors and malformed responses are not retried.
    - A month that fails is skipped and reported; if any month failed (after
      all other months are fetched and written) the script exits 1. Failed
      months are omitted from the output, so re-run to get complete data.
    - Exit 1 also on an unreadable/invalid config or an unwritable output file.
    - Exit 0 only if every month was fetched and written successfully.
    - Progress goes to stdout; warnings and errors go to stderr.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Optional

import requests

API_BASE_URL = "https://api.foursquare.com/v2/users/{userid}/historysearch"
API_VERSION = '20240216'
REQUEST_TIMEOUT = 10  # seconds
REQUIRED_CONFIG_FIELDS = {'userid', 'wsid', 'oauth_token'}

# Browser-like headers the Swarm web client sends; static, so built once.
REQUEST_HEADERS = {
    'authority': 'api.foursquare.com',
    'accept': 'application/json, text/javascript, */*; q=0.01',
    'accept-language': 'en-US,en;q=0.9',
    'dnt': '1',
    'origin': 'https://swarmapp.com',
    'referer': 'https://swarmapp.com/',
    'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'cross-site',
    'user-agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    ),
}


class SwarmApiError(Exception):
    """Raised when Swarm API returns an error."""


class MonthRange(NamedTuple):
    label: str  # YYYY-MM
    start: int  # Unix timestamp
    end: int    # Unix timestamp


def load_config(config_path: str) -> Dict:
    """
    Load and validate the JSON config file.

    Raises:
        FileNotFoundError: If config file doesn't exist
        KeyError: If required fields are missing from config
        json.JSONDecodeError: If config is invalid JSON
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with open(config_file) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON in config file: {e.msg}", e.doc, e.pos)

    missing = REQUIRED_CONFIG_FIELDS - set(config.keys())
    if missing:
        raise KeyError(f"Missing required config fields: {missing}")
    return config


def validate_response(data: Dict) -> None:
    """Raise SwarmApiError if the API payload is an error or malformed."""
    if 'meta' not in data:
        raise SwarmApiError("Invalid API response: missing 'meta' field")

    code = data['meta'].get('code')
    if code != 200:
        error_msg = data['meta'].get('errorDetail', 'Unknown error')
        raise SwarmApiError(f"API error code {code}: {error_msg}")

    if 'checkins' not in data.get('response', {}):
        raise SwarmApiError("Invalid API response: missing checkins data")


class SwarmApi:
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds

    def __init__(self, config_path: str):
        """
        Initialize API client from config file.

        Args:
            config_path: Path to JSON config file with userid, wsid, oauth_token

        Raises:
            FileNotFoundError, KeyError, json.JSONDecodeError: see load_config
        """
        config = load_config(config_path)
        self.wsid = config['wsid']
        self.oauth_token = config['oauth_token']
        self.url = API_BASE_URL.format(userid=config['userid'])
        # A session reuses the TCP/TLS connection across the many monthly requests.
        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)

    def __call__(self, start: int, end: int, limit: int, offset: int = 0) -> Dict:
        """
        Fetch one page of checkins for a date range.

        Args:
            start: Unix timestamp for start of range
            end: Unix timestamp for end of range
            limit: Max checkins to return per request
            offset: Number of checkins to skip (for pagination)

        Returns:
            API response as dict

        Raises:
            SwarmApiError: If API returns an error or network fails after retries
        """
        params = {
            'locale': 'en',
            'explicit-lang': 'false',
            'v': API_VERSION,
            'offset': offset,
            'limit': limit,
            'm': 'swarm',
            'clusters': 'false',
            'afterTimestamp': start,
            'beforeTimestamp': end,
            'sort': 'newestfirst',
            'wsid': self.wsid,
            'oauth_token': self.oauth_token,
        }

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = self.session.get(self.url, params=params, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.RequestException as e:
                if attempt == self.MAX_RETRIES:
                    raise SwarmApiError(f"API request failed after {self.MAX_RETRIES} attempts: {e}")
                wait_time = self.RETRY_DELAY * (2 ** (attempt - 1))
                print(f"Request failed (attempt {attempt}/{self.MAX_RETRIES}): {e}. "
                      f"Retrying in {wait_time}s...", file=sys.stderr)
                time.sleep(wait_time)
            except json.JSONDecodeError as e:
                raise SwarmApiError(f"Failed to parse API response as JSON: {e}")
            else:
                validate_response(data)
                return data


def generate_month_ranges(start_year: int, end_date: Optional[datetime] = None) -> List[MonthRange]:
    """
    Generate a MonthRange for each month from January of start_year until end_date.

    Args:
        start_year: Year to start from (January 1st)
        end_date: Optional end date (defaults to now); the last range is capped to it
    """
    end_date = end_date or datetime.now()

    def months() -> Iterator[datetime]:
        year, month = start_year, 1
        while True:
            yield datetime(year, month, 1)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    ranges = []
    starts = months()
    current = next(starts)
    while current < end_date:
        next_month = next(starts)
        period_end = min(next_month, end_date)
        ranges.append(MonthRange(
            label=current.strftime("%Y-%m"),
            start=int(current.timestamp()),
            end=int(period_end.timestamp()),
        ))
        current = next_month
    return ranges


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Download Foursquare/Swarm checkin history')
    parser.add_argument('--config', default='config.json', help='Path to config.json')
    parser.add_argument('--start-year', type=int, default=2017, help='Year account was started')
    parser.add_argument('--output', default='checkins_all.json', help='Output JSON file')
    parser.add_argument('--limit', type=int, default=500, help='Max checkins per request')
    return parser.parse_args()


def fetch_month(api: SwarmApi, month: MonthRange, limit: int) -> List[Dict]:
    """
    Fetch all checkins in a month, paging with offset until a short page is returned.

    Raises:
        SwarmApiError: If any page fails (the month is then treated as failed
            rather than returning partial data).
    """
    items: List[Dict] = []
    while True:
        page = api(month.start, month.end, limit, offset=len(items))['response']['checkins']
        page_items = page.get('items', [])
        items.extend(page_items)
        if len(page_items) < limit:
            return items


def fetch_all_checkins(api: SwarmApi, ranges: List[MonthRange], limit: int):
    """Fetch every month, logging progress. Returns (checkins, failed_month_labels)."""
    checkins = []
    failed_months = []
    total = len(ranges)

    for i, month in enumerate(ranges, start=1):
        try:
            items = fetch_month(api, month, limit)
        except SwarmApiError as e:
            print(f"[{i}/{total}] {month.label}: ERROR - {e}", file=sys.stderr)
            failed_months.append(month.label)
            continue

        checkins.extend(items)
        print(f"[{i}/{total}] {month.label}: {len(items)} checkins")

    return checkins, failed_months


def main():
    args = parse_args()

    try:
        api = SwarmApi(args.config)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"ERROR: Failed to load config: {e}", file=sys.stderr)
        sys.exit(1)

    ranges = generate_month_ranges(args.start_year)
    print(f"Fetching checkins for {len(ranges)} month(s) starting from {args.start_year}...")

    checkins, failed_months = fetch_all_checkins(api, ranges, args.limit)

    output_path = Path(args.output)
    try:
        with open(output_path, 'w') as f:
            json.dump(checkins, f, indent=2)
        print(f"\nSuccessfully wrote {len(checkins)} checkins to {output_path}")
    except IOError as e:
        print(f"ERROR: Failed to write output file: {e}", file=sys.stderr)
        sys.exit(1)

    if failed_months:
        print(f"WARNING: Failed to fetch data for {len(failed_months)} month(s): "
              f"{', '.join(failed_months)}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
