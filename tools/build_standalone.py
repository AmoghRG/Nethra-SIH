#!/usr/bin/env python3
"""Inline every asset into one self-contained HTML file.

All file I/O is explicitly UTF-8: the sources contain characters (em dashes,
multiplication signs) that Windows' default cp1252 codec cannot decode.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent  # repo root
W = ROOT / "web"


def read(rel):
    return (W / rel).read_text(encoding="utf-8")


html = read("index.html")

for tag, path in [
    ('<link rel="stylesheet" href="vendor/leaflet.css" />', "vendor/leaflet.css"),
    ('<link rel="stylesheet" href="css/styles.css" />', "css/styles.css"),
]:
    html = html.replace(tag, "<style>\n" + read(path) + "\n</style>")

for tag, path in [
    ('<script src="vendor/leaflet.js"></script>', "vendor/leaflet.js"),
    ('<script src="vendor/chart.umd.js"></script>', "vendor/chart.umd.js"),
    ('<script src="js/config.js"></script>', "js/config.js"),
    ('<script src="js/fixtures.js"></script>', "js/fixtures.js"),
    ('<script src="js/roads.js"></script>', "js/roads.js"),
    ('<script src="js/app.js"></script>', "js/app.js"),
]:
    html = html.replace(tag, "<script>\n" + read(path) + "\n</script>")

out = ROOT / "demo" / "netra-dashboard-standalone.html"
out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size:,} bytes)")
