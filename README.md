# Foursquare / Swarm Tools

Small Python scripts for getting your own check-in history out of
[Swarm](https://www.swarmapp.com/) (Foursquare) and turning it into a map-ready
GeoJSON file.

The workflow has three steps, one script each:

```
get_swarm_credentials.py  ->  config.json
get_swarm_checkins.py     ->  checkins_all.json
fs_geojson.py             ->  foursquare_venues.geojson
```
## Scripts overview

| Script | Purpose |
| --- | --- |
| [get_swarm_credentials.py](get_swarm_credentials.py) | Captures your Swarm web credentials via a browser login |
| [get_swarm_checkins.py](get_swarm_checkins.py) | Downloads your full check-in history to JSON |
| [fs_geojson.py](fs_geojson.py) | Converts check-in JSON into a GeoJSON file of unique venues |

## Requirements

- Python 3.8 or newer
- `requests` (for `get_swarm_checkins.py`)
- `playwright` and its Chromium browser (only for `get_swarm_credentials.py`)

```bash
pip install requests playwright
playwright install chromium
```

`fs_geojson.py` uses only the standard library.

## Detailed usage

### 1. `get_swarm_credentials.py`

Opens a real Chromium window at `https://swarmapp.com/history`. You log in
yourself (the script never sees your password). It watches the page's own
requests to the Foursquare API and extracts the `userid`, `wsid` and
`oauth_token` from the first authenticated call, then saves them as the config
file that `get_swarm_checkins.py` reads.

```bash
python get_swarm_credentials.py [--output config.json] [--timeout 300]
```

| Option | Default | Description |
| --- | --- | --- |
| `--output` | `config.json` | Where to save the credentials |
| `--timeout` | `300` | Seconds to wait for you to log in |

If nothing loads after logging in, pick any month in the history dropdown to
trigger the request.

### 2. `get_swarm_checkins.py`

Downloads your check-in history from the `historysearch` endpoint, one calendar
month at a time from January of `--start-year` to today. Each month is paged
with `limit`/`offset`, so months with more than `--limit` check-ins are fetched
in full. Network errors are retried up to 3 times with exponential backoff.
Results are written as a single JSON list of check-ins.

```bash
python get_swarm_checkins.py [--config config.json] [--start-year 2010] \
                             [--output checkins_all.json] [--limit 500]
```

| Option | Default | Description |
| --- | --- | --- |
| `--config` | `config.json` | Credentials file (`userid`, `wsid`, `oauth_token`) |
| `--start-year` | `2010` | First year to fetch; set to the year your account began |
| `--output` | `checkins_all.json` | Output file (overwritten if it exists) |
| `--limit` | `500` | Check-ins per API request (page size) |

If any month fails, the script still writes the rest, lists the failed months,
and exits with status 1. Re-run to get complete data. Credentials expire, so if
every month fails with an API error, re-run `get_swarm_credentials.py`.

### 3. `fs_geojson.py`

Converts a check-in history JSON file into a GeoJSON `FeatureCollection` with one
`Point` feature per unique venue (deduplicated by Foursquare venue ID, falling
back to name + coordinates). Each feature carries venue details (name, category,
address, phone, website, ...) plus `checkin_count`, `first_checkin` and
`last_checkin`. Check-ins with no venue or valid coordinates are skipped and
counted in the summary. The result can be opened in tools such as
[geojson.io](https://geojson.io), QGIS or Leaflet.

```bash
python fs_geojson.py [input.json] [output.geojson]
```

| Argument | Default | Description |
| --- | --- | --- |
| `input` | `foursquare_checkins.json` | Check-in JSON (a bare list or a common API wrapper) |
| `output` | `foursquare_venues.geojson` | GeoJSON file to write |

Example using the output from step 2:

```bash
python fs_geojson.py checkins_all.json venues.geojson
```

## Security and privacy

- `config.json` contains a **live credential** for your Swarm account. Never
  commit or share it. It is listed in `.gitignore`.
- Your check-in history and the generated GeoJSON reveal where you have been.
  Those files are also git-ignored; think before publishing them.

## License

[MIT](LICENSE)
