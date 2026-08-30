#!/usr/bin/env python3
"""
Fetch real street centrelines from OpenStreetMap and bake them into
fixtures/road_network.json, so the dashboard renders real roads offline.

Run once, commit the output, and the demo never touches the network:

    python3 tools/fetch_roads.py
    python3 tools/fetch_roads.py --lat 12.86889 --lng 74.86389 --radius 800

Data (c) OpenStreetMap contributors, ODbL. Attribution is required and is
already rendered on the map.
"""

import argparse
import json
import math
import pathlib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

# Tried in order. If one is busy or blocked, the next is attempted.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
ROAD_CLASSES = "trunk|primary|secondary|tertiary|unclassified|residential|service"

# Keep in sync with web/js/config.js
DEFAULT_LAT = 12.86889
DEFAULT_LNG = 74.86389
DEFAULT_RADIUS_M = 800
DEFAULT_NAME = "Pumpwell Circle, Mangaluru"


def bbox_around(lat, lng, radius_m):
    d_lat = radius_m / 110574.0
    d_lng = radius_m / (111320.0 * math.cos(math.radians(lat)))
    return (lat - d_lat, lng - d_lng, lat + d_lat, lng + d_lng)


def build_query(lat, lng, radius_m):
    s, w, n, e = bbox_around(lat, lng, radius_m)
    return (
        f'[out:json][timeout:60];'
        f'way["highway"~"^({ROAD_CLASSES})$"]({s},{w},{n},{e});'
        f'out geom;'
    )


def ssl_context(insecure=False):
    """
    Build an SSL context that works on stock Windows/msys2 Python, which often
    ships without a usable CA bundle (CERTIFICATE_VERIFY_FAILED on every HTTPS
    call). certifi supplies one if installed.
    """
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch_from(url, query, insecure=False):
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            # Overpass asks for a descriptive UA so they can contact heavy users.
            "User-Agent": "NETRA-SIH-dashboard/1.0 (road risk map; contact: team)",
        },
    )
    with urllib.request.urlopen(req, timeout=90, context=ssl_context(insecure)) as resp:
        return json.load(resp)


def is_ssl_failure(exc):
    """urllib wraps SSL errors inside URLError, so unwrap before checking."""
    if isinstance(exc, ssl.SSLError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLError):
        return True
    return "CERTIFICATE_VERIFY_FAILED" in str(exc) or "SSL" in type(reason).__name__


def fetch(query, insecure=False):
    """Try each mirror in turn. Raises the last error if all fail."""
    last = None
    saw_ssl_error = False
    for url in OVERPASS_MIRRORS:
        host = urllib.parse.urlparse(url).netloc
        try:
            print(f"  trying {host} ...", flush=True)
            return fetch_from(url, query, insecure)
        except (ssl.SSLError, urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError) as exc:
            last = exc
            if is_ssl_failure(exc):
                saw_ssl_error = True
                print("    TLS verification failed")
            else:
                print(f"    failed: {exc}")

    if saw_ssl_error and not insecure:
        raise SystemExit(
            "\nEvery mirror failed TLS verification. Your Python has no usable CA\n"
            "certificate bundle - common with the msys2/MSYS builds on Windows.\n"
            "\nFix it properly (30 seconds, and it fixes pip and every other\n"
            "HTTPS script you run too):\n"
            "    python3 -m pip install certifi\n"
            "then re-run this script.\n"
            "\nIf that is not an option right now, you can skip verification:\n"
            "    python3 tools/fetch_roads.py --insecure\n"
            "This fetches public read-only OpenStreetMap geometry over an\n"
            "unverified connection. No credentials are sent. Acceptable for this\n"
            "one-off fetch; do not make a habit of it."
        )
    raise last if last else RuntimeError("no mirrors configured")


def normalise(payload):
    roads = []
    for el in payload.get("elements", []):
        if el.get("type") != "way":
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        tags = el.get("tags", {}) or {}
        roads.append(
            {
                "id": str(el["id"]),
                "name": tags.get("name") or tags.get("ref"),
                "highway": tags.get("highway", "unclassified"),
                "oneway": bool(tags.get("oneway") and tags.get("oneway") != "no"),
                "path": [[g["lat"], g["lon"]] for g in geom],
            }
        )
    return roads


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=DEFAULT_LAT)
    ap.add_argument("--lng", type=float, default=DEFAULT_LNG)
    ap.add_argument("--radius", type=float, default=DEFAULT_RADIUS_M,
                    help="half-width of the fetch box, in metres")
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--out", default=None,
                    help="output path (default: fixtures/road_network.json)")
    ap.add_argument("--insecure", action="store_true",
                    help="skip TLS verification (last resort when no CA bundle "
                         "is available; fetches public OSM data only)")
    args = ap.parse_args()

    repo = pathlib.Path(__file__).resolve().parent.parent
    out = pathlib.Path(args.out) if args.out else repo / "fixtures" / "road_network.json"

    query = build_query(args.lat, args.lng, args.radius)
    print(f"Fetching roads within {args.radius:.0f} m of ({args.lat}, {args.lng})…")

    if args.insecure:
        print("  (TLS verification disabled via --insecure)")

    try:
        payload = fetch(query, insecure=args.insecure)
    except Exception as exc:
        print(f"\nAll Overpass mirrors failed: {exc}", file=sys.stderr)
        print("Overpass is rate-limited and periodically busy. Wait a minute and "
              "retry.", file=sys.stderr)
        return 1

    roads = normalise(payload)
    if not roads:
        print("No roads returned — check the coordinates are on land and the "
              "radius is large enough.", file=sys.stderr)
        return 1

    doc = {
        "generated": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "junction": {"name": args.name, "center": [args.lat, args.lng]},
        "bbox": list(bbox_around(args.lat, args.lng, args.radius)),
        "source": "OpenStreetMap via Overpass API, ODbL",
        "roads": roads,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    named = sum(1 for r in roads if r["name"])
    print(f"Wrote {out}")
    print(f"  {len(roads)} ways, {named} of them named")
    print("\nNow rebuild the fixture bundle so the standalone build picks it up:")
    print("  python3 tools/gen_fixtures.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
