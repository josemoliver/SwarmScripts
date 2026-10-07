#!/usr/bin/env python3
"""Convert a Foursquare check-in history JSON file into a GeoJSON FeatureCollection.

One Point feature is produced per unique venue. Venues are deduplicated by the
Foursquare venue ID; if a check-in has no venue ID, a fallback key of
(name, lat, lng) is used instead.

Usage:
    python fs_geojson.py [input.json] [output.geojson]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_checkins(path):
    """Return the list of check-ins, accepting a bare list or common wrappers."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "checkins"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                return value["items"]
        if isinstance(data.get("response"), dict):
            return load_from_response(data["response"])
    raise ValueError("Unrecognized check-in file structure")


def load_from_response(resp):
    checkins = resp.get("checkins")
    if isinstance(checkins, dict):
        return checkins.get("items", [])
    return checkins or []


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def venue_coords(venue):
    loc = venue.get("location") or {}
    lat, lng = to_float(loc.get("lat")), to_float(loc.get("lng"))
    if lat is None or lng is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def venue_key(venue, coords):
    """Venue ID is authoritative; fall back to name + coordinates."""
    vid = venue.get("id")
    if vid:
        return ("id", str(vid))
    name = (venue.get("name") or "").strip().casefold()
    lat, lng = coords if coords else (None, None)
    return ("name_coords", name, lat, lng)


def iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def clean(d):
    """Drop None / empty values so properties stay compact."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def build_properties(venue):
    loc = venue.get("location") or {}
    contact = venue.get("contact") or {}
    categories = venue.get("categories") or []
    primary = next((c for c in categories if c.get("primary")), categories[0] if categories else {})
    props = {
        "id": venue.get("id"),
        "name": venue.get("name"),
        "category": primary.get("name"),
        "category_id": primary.get("id"),
        "categories": [c.get("name") for c in categories if c.get("name")],
        "address": loc.get("address"),
        "cross_street": loc.get("crossStreet"),
        "city": loc.get("city"),
        "state": loc.get("state"),
        "postal_code": loc.get("postalCode"),
        "country": loc.get("country"),
        "country_code": loc.get("cc"),
        "phone": contact.get("formattedPhone") or contact.get("phone"),
        "website": venue.get("url"),
        "foursquare_url": venue.get("canonicalUrl"),
        "verified": venue.get("verified"),
        "closed": venue.get("closed"),
        "venue_checkins_count": (venue.get("stats") or {}).get("checkinsCount"),
    }
    return clean(props)


def convert(checkins):
    venues = {}  # key -> {"feature": ..., "times": [...]}
    skipped = 0
    for checkin in checkins:
        venue = checkin.get("venue") if isinstance(checkin, dict) else None
        if not venue:
            skipped += 1
            continue
        coords = venue_coords(venue)
        if coords is None:
            skipped += 1
            continue
        key = venue_key(venue, coords)
        entry = venues.get(key)
        if entry is None:
            lat, lng = coords
            entry = venues[key] = {
                "feature": {
                    "type": "Feature",
                    "id": venue.get("id") or None,
                    "geometry": {"type": "Point", "coordinates": [lng, lat]},
                    "properties": build_properties(venue),
                },
                "times": [],
            }
            if entry["feature"]["id"] is None:
                del entry["feature"]["id"]
        if checkin.get("createdAt") is not None:
            entry["times"].append(checkin["createdAt"])
        else:
            entry.setdefault("untimed", 0)
        entry["count"] = entry.get("count", 0) + 1

    features = []
    for entry in venues.values():
        feature, times = entry["feature"], entry["times"]
        feature["properties"]["checkin_count"] = entry["count"]
        if times:
            feature["properties"]["first_checkin"] = iso(min(times))
            feature["properties"]["last_checkin"] = iso(max(times))
        features.append(feature)

    features.sort(key=lambda f: (f["properties"].get("name") or "").casefold())
    return {"type": "FeatureCollection", "features": features}, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", nargs="?", default=HERE / "foursquare_checkins.json")
    parser.add_argument("output", nargs="?", default=HERE / "foursquare_venues.geojson")
    args = parser.parse_args()

    checkins = load_checkins(args.input)
    collection, skipped = convert(checkins)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False, indent=2)

    print(
        f"{len(checkins)} check-ins -> {len(collection['features'])} unique venues "
        f"({skipped} skipped: no venue or coordinates). Wrote {args.output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
