#!/usr/bin/env python3
"""Build data/lights.json and data/italy.json for the Italian lighthouse map.

Light characteristics come from the WAIL reference cards published by ARI Genova
(https://www.arigenova.it/wail/referenze%20fari%20italiani.php). Positions come
from that same index. The coastline is the Openpolis regional boundary, simplified.
"""

from __future__ import annotations

import html
import json
import math
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = Path("/tmp/fari")
WAIL_URL = "https://www.arigenova.it/wail/referenze%20fari%20italiani.php"
ITALY_URL = (
    "https://raw.githubusercontent.com/openpolis/geojson-italy/"
    "master/geojson/limits_IT_regions.geojson"
)

COLOR_WORDS = {
    "BIANCO": "W",
    "WHITE": "W",
    "ROSSO": "R",
    "RED": "R",
    "VERDE": "G",
    "GREEN": "G",
    "GIALLO": "Y",
    "AMBER": "Y",
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "lighthouses-map/0.1"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def load_index() -> list[dict]:
    page_path = RAW / "wail.html"
    if page_path.exists():
        page = page_path.read_text(errors="replace")
    else:
        page = fetch(WAIL_URL).decode("utf-8", "replace")
    lights = []
    for row in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 6:
            continue

        def text(cell: str) -> str:
            cell = re.sub(r"</?br\s*/?>", "\n", cell, flags=re.I)
            cell = re.sub(r"<[^>]+>", " ", cell)
            return re.sub(r"[ \t]+", " ", html.unescape(cell)).strip()

        ref = text(cells[2])
        place = text(cells[4])
        match = re.search(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", place)
        code = re.match(r"([A-Z]{2}-\d+)", ref)
        pdf = re.search(r"href=([^\s>]+)", cells[5])
        if not match or not code or not pdf:
            continue
        # Bearings in the place name ("all. 273") sit on the same line as the
        # coordinates once the line break is removed. Keep the pair that falls
        # on the Italian chart.
        pairs = [
            (float(lat), float(lon))
            for lat, lon in re.findall(r"(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)", place)
        ]
        chosen = next(
            ((lat, lon) for lat, lon in pairs if 35 <= lat <= 48 and 6 <= lon <= 20),
            None,
        )
        if not chosen and match:
            chosen = (float(match.group(1)), float(match.group(2)))
        if not chosen:
            continue
        locality = re.sub(r"\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*", "", place).strip()
        lights.append(
            {
                "region": text(cells[0]),
                "city": text(cells[1]),
                "wail": code.group(1),
                "locality": locality,
                "lat": chosen[0],
                "lon": chosen[1],
                "pdf": pdf.group(1).strip("'\""),
            }
        )
    return lights


def field(text: str, label: str) -> str:
    match = re.search(rf"(?m)^[ \t]*{label}[ \t]+(\S.*?)\s*$", text)
    return match.group(1).strip() if match else ""


def parse_bearing(token: str) -> float | None:
    token = token.strip().replace(",", ".")
    # Cards sometimes write 082° as 0.82.
    if re.fullmatch(r"\d\.\d{2}", token):
        return float(token.replace(".", "")) % 360
    if re.fullmatch(r"\d{1,3}(?:\.\d+)?", token):
        value = float(token)
        if value <= 360:
            return value % 360
    return None


def clockwise(start: float, end: float) -> float:
    span = (end - start) % 360
    return 360.0 if span == 0 else span


def arc(start: float, end: float, span: float | None) -> tuple[float, float] | None:
    forward = clockwise(start, end)
    if span is None:
        return start, end
    if abs(forward - span) <= 18:
        return start, end
    if abs((360 - forward) - span) <= 18:
        return end, start
    return None


def parse_sectors(raw: str, color: str) -> list[dict] | None:
    """Return clockwise arcs as published (bearings from seaward), or None for all-round."""
    if not raw or raw.strip() in {"-", "—", "–"}:
        return None
    text = raw.replace("–", "-").replace("—", "-").replace(".-", "-")
    text = re.sub(r"\s+", " ", text).strip()
    lowered = text.lower()
    if "centrat" in lowered or "isolotto" in lowered or "scoglio il" in lowered:
        if not re.search(r"\d", text):
            return None

    def numbered_pairs(chunk: str) -> list[tuple[float, float]]:
        pairs = []
        for match in re.finditer(
            r"(\d{1,3}(?:[.,]\d+)?|\d\.\d{2})\s*-\s*(\d{1,3}(?:[.,]\d+)?|\d\.\d{2})"
            r"(?:\s*\(\s*(\d{1,3}(?:[.,]\d+)?)\s*\))?",
            chunk,
        ):
            start = parse_bearing(match.group(1))
            end = parse_bearing(match.group(2))
            span = float(match.group(3).replace(",", ".")) if match.group(3) else None
            if start is None or end is None:
                continue
            chosen = arc(start, end, span)
            if chosen:
                pairs.append(chosen)
        return pairs

    hidden = []
    visible = []
    # Split "visible ... occultato ..." when both are present.
    parts = re.split(r"(?i)\b(?:occultat\w*|oscurat\w*)\b", text)
    if len(parts) > 1 and re.search(r"(?i)vis", text):
        visible = numbered_pairs(parts[0])
        for part in parts[1:]:
            hidden.extend(numbered_pairs(part))
    elif re.match(r"(?i)\s*(?:occultat|oscurat)", text):
        hidden = numbered_pairs(text)
    else:
        colored = re.findall(
            r"([WRGY])\s+(\d{1,3}(?:[.,]\d+)?|\d\.\d{2})\s*\(\s*(\d{1,3}(?:[.,]\d+)?)\s*\)",
            text,
            flags=re.I,
        )
        if colored:
            arcs = []
            for swatch, end_token, span_token in colored:
                end = parse_bearing(end_token)
                span = float(span_token.replace(",", "."))
                if end is None or span <= 0 or span >= 360:
                    continue
                arcs.append(
                    {"a": (end - span) % 360, "b": end, "color": swatch.upper()}
                )
            return arcs or None

        tokens = re.findall(r"[WRGY]|\d{1,3}(?:[.,]\d+)?|\d\.\d{2}", text, flags=re.I)
        bearings = []
        colors_between = []
        mode = None
        for token in tokens:
            if re.fullmatch(r"[WRGY]", token, flags=re.I):
                colors_between.append(token.upper())
                mode = "color"
            else:
                value = parse_bearing(token)
                if value is None:
                    continue
                bearings.append(value)
                mode = "num"
        if len(bearings) >= 3 and len(colors_between) >= len(bearings) - 1:
            arcs = []
            for index in range(len(bearings) - 1):
                swatch = colors_between[index]
                start, end = bearings[index], bearings[index + 1]
                if clockwise(start, end) >= 359:
                    continue
                arcs.append({"a": start, "b": end, "color": swatch})
            return arcs or None
        visible = numbered_pairs(text)

    if hidden and not visible:
        arcs = []
        for start, end in hidden:
            # Published arc is the obscured part. Shine the complementary arc.
            arcs.append({"a": end % 360, "b": start % 360, "color": color})
        return arcs or None

    arcs = [{"a": start, "b": end, "color": color} for start, end in visible]
    if hidden and arcs:
        # Drop the simple case into the renderer as extra dark arcs by splitting
        # only when the hidden arc sits inside a single visible one.
        split = []
        for sector in arcs:
            pieces = [sector]
            for hide_a, hide_b in hidden:
                next_pieces = []
                for piece in pieces:
                    next_pieces.extend(subtract_arc(piece, hide_a, hide_b))
                pieces = next_pieces
            split.extend(pieces)
        arcs = split
    if not arcs:
        return None
    if len(arcs) == 1 and clockwise(arcs[0]["a"], arcs[0]["b"]) >= 350:
        return None
    return arcs


def subtract_arc(sector: dict, hide_a: float, hide_b: float) -> list[dict]:
    span = clockwise(sector["a"], sector["b"])
    hide_span = clockwise(hide_a, hide_b)
    # Express the hidden interval relative to the visible start.
    rel_a = (hide_a - sector["a"]) % 360
    rel_b = (hide_b - sector["a"]) % 360
    if rel_a > span and rel_b > span:
        return [sector]
    cuts = []
    if rel_a < span:
        cuts.append(rel_a)
    if rel_b < span:
        cuts.append(rel_b)
    if not cuts or hide_span >= span:
        return [sector]
    # Visible from 0 to span, punch rel_a..rel_b if that forward interval is inside.
    if rel_b > rel_a and rel_b <= span and rel_a >= 0:
        pieces = []
        if rel_a > 1:
            pieces.append((0, rel_a))
        if span - rel_b > 1:
            pieces.append((rel_b, span))
        return [
            {
                "a": (sector["a"] + start) % 360,
                "b": (sector["a"] + end) % 360,
                "color": sector["color"],
            }
            for start, end in pieces
        ]
    return [sector]


def parse_phases(raw: str, period: float | None) -> list[float] | None:
    if not raw or raw.strip() in {"-", "—", "–"}:
        return None
    numbers = [
        float(item.replace(",", "."))
        for item in re.findall(r"\d+(?:[.,]\d+)?", raw.replace("–", "-"))
    ]
    if len(numbers) < 2:
        return None
    total = sum(numbers)
    if total <= 0:
        return None
    if period and abs(total - period) / period > 0.35:
        return None
    if period and abs(total - period) > 0.05:
        numbers = [number * period / total for number in numbers]
    return numbers


def group_count(kind: str) -> int:
    match = re.search(r"\((\d+)\)", kind.replace(" ", ""))
    return int(match.group(1)) if match else 1


def normalize_character(kind: str) -> str:
    compact = re.sub(r"\s+", "", kind)
    compact = compact.replace("LFl", "LFl")
    return compact


def parse_colors(raw: str) -> list[str]:
    text = raw.upper()
    for word, letter in COLOR_WORDS.items():
        text = text.replace(word, letter)
    letters = re.findall(r"[WRGY]", text)
    # "W" repeated from words already replaced. Keep first-seen order, drop dupes
    # only when the source was a single token like "W".
    if not letters:
        return ["W"]
    if len(letters) > 4:
        letters = letters[:4]
    return letters


NAME_FIXES = {
    "Cao Suvero": "Capo Suvero",
    "Poerto Maurizio": "Porto Maurizio",
    "Scaglio Porcelli": "Scoglio Porcelli",
    "Antico Catello Regina Giovanna": "Antico castello Regina Giovanna",
    "Punta Pioppeto": "Punta Pioppetto",
}


def clean_name(title: str, locality: str, city: str) -> str:
    name = title.strip()
    name = re.sub(r"^[A-Z]{2}-\d+\s*,\s*", "", name)
    name = re.sub(r"^(?:ITA|SAR|N/?A)(?:-\d+)?\s*[-–]?\s*", "", name)
    name = re.sub(r"^/\d+\s*-\s*", "", name)
    name = re.sub(r"^[A-Z]{2}-\d+\s*[-–]\s*", "", name)
    name = name.strip(" -–")
    name = NAME_FIXES.get(name, name)
    if not name or re.fullmatch(r"[A-Z]{2}-\d+", name):
        name = locality or city
    name = re.sub(r"\s*-?\d+\.\d+\s*,\s*-?\d+\.\d+\s*", "", name).strip()
    return name or city


def clean_place(locality: str) -> str | None:
    place = re.sub(r"-?\d+\.\d+\s*,\s*-?\d+\.\d+", "", locality or "")
    place = re.sub(r"\s+", " ", place).strip(" ,")
    return place or None


def parse_cards(index: list[dict]) -> list[dict]:
    lights = []
    for item in index:
        text_path = RAW / "txt" / f"{item['wail']}.txt"
        if not text_path.exists():
            continue
        text = text_path.read_text(errors="replace")
        title_match = re.search(r"WAIL\s*[–\-]\s*([^\n]+)", text)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        segnalamento = field(text, "Segnalamento")
        funzione = field(text, "Funzione")
        kind = field(text, "Tipo")
        colore = field(text, "Colore")
        periodo = field(text, "Periodo")
        fasi = field(text, "Fasi")
        settori = field(text, "Settori")
        altezza = field(text, r"Altezza luce dal (?:mare|suolo)")
        range_match = re.search(
            r"Portata nominale\s+.*?Principale:\s*([0-9]+(?:[.,][0-9]+)?)\s*Mn",
            text,
            re.S,
        ) or re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*Mn", text)
        funzione_l = funzione.lower()
        if any(word in funzione_l for word in ("monument", "votiv", "lacust")):
            continue
        if not kind or kind.strip() in {"-", "—"}:
            continue
        if not range_match:
            continue
        period_match = re.search(r"(\d+(?:[.,]\d+)?)", periodo or "")
        period = float(period_match.group(1).replace(",", ".")) if period_match else None
        if kind.strip().lower() in {"f", "led"} and period is None:
            period = 0
        if period is None:
            continue
        colors = parse_colors(colore)
        primary = colors[0]
        character = normalize_character(kind)
        rotating = "rotante" in segnalamento.lower()
        phases = parse_phases(fasi, period if period else None)
        beams = beam_model(character, period, phases, rotating, primary)
        sectors = parse_sectors(settori, primary)
        height_match = re.search(r"(\d+(?:[.,]\d+)?)", altezza or "")
        sector_text = settori.strip() if settori and settori.strip() not in {"-", "—", "–"} else None
        lights.append(
            {
                "id": item["wail"],
                "name": clean_name(title, item.get("locality", ""), item["city"]),
                "place": clean_place(item.get("locality", "")),
                "city": item["city"],
                "region": item["region"],
                "lat": item["lat"],
                "lon": item["lon"],
                "character": format_character(character, colors, period),
                "kind": "rotating" if rotating else ("fixed" if period == 0 else "flash"),
                "period": period,
                "range": float(range_match.group(1).replace(",", ".")),
                "height": float(height_match.group(1).replace(",", ".")) if height_match else None,
                "colors": colors,
                "beams": beams,
                "phases": phases,
                "sectors": sectors,
                "sectorText": sector_text,
                "optic": segnalamento or None,
            }
        )
    return lights


def format_character(character: str, colors: list[str], period: float) -> str:
    color = "".join(colors)
    if period == 0:
        return f"{character} {color}".strip()
    period_text = f"{period:g}s"
    return f"{character} {color} {period_text}".strip()


def beam_model(character: str, period: float, phases: list[float] | None, rotating: bool, color: str):
    if not rotating or period <= 0:
        return None
    if phases:
        beams = []
        cursor = 0.0
        for index, duration in enumerate(phases):
            if index % 2 == 0 and duration > 0:
                width = max(1.6, duration / period * 360)
                beams.append({"ang": (cursor + duration / 2) / period * 360, "width": width, "color": color})
            cursor += duration
        if beams:
            return beams
    count = group_count(character)
    width = min(10.0, max(2.2, 0.4 / period * 360))
    return [
        {"ang": index * 360 / count, "width": width, "color": color}
        for index in range(count)
    ]


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def point_on_land(lon: float, lat: float, features: list[dict]) -> bool:
    for feature in features:
        geometry = feature["geometry"]
        polygons = (
            geometry["coordinates"]
            if geometry["type"] == "MultiPolygon"
            else [geometry["coordinates"]]
        )
        for polygon in polygons:
            if not polygon:
                continue
            if not point_in_ring(lon, lat, polygon[0]):
                continue
            if any(point_in_ring(lon, lat, hole) for hole in polygon[1:]):
                continue
            return True
    return False


def destination(lat: float, lon: float, bearing: float, km: float) -> tuple[float, float]:
    radius = 6371.0
    delta = km / radius
    theta = math.radians(bearing)
    phi = math.radians(lat)
    lam = math.radians(lon)
    phi2 = math.asin(
        math.sin(phi) * math.cos(delta) + math.cos(phi) * math.sin(delta) * math.cos(theta)
    )
    lam2 = lam + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi),
        math.cos(delta) - math.sin(phi) * math.sin(phi2),
    )
    return math.degrees(phi2), (math.degrees(lam2) + 540) % 360 - 180


def bisector(start: float, end: float) -> float:
    return (start + clockwise(start, end) / 2) % 360


def orient_sectors(lights: list[dict], features: list[dict]) -> None:
    """Italian cards give sector bearings from seaward. Confirm, then store the
    direction the beam travels (0° = north, clockwise)."""
    score = {0: 0, 180: 0}
    sample = 0
    for light in lights:
        if not light["sectors"]:
            continue
        # Use the widest sector's bisector as the "should be water" test.
        sector = max(light["sectors"], key=lambda item: clockwise(item["a"], item["b"]))
        if clockwise(sector["a"], sector["b"]) > 300:
            continue
        sample += 1
        mid = bisector(sector["a"], sector["b"])
        for shift in (0, 180):
            lat, lon = destination(light["lat"], light["lon"], (mid + shift) % 360, 12)
            if not point_on_land(lon, lat, features):
                score[shift] += 1
    shift = 180 if score[180] >= score[0] else 0
    print(f"sector orientation scores (seaward +shift): {score} over {sample}; using +{shift}")
    for light in lights:
        if not light["sectors"]:
            continue
        turned = []
        for sector in light["sectors"]:
            start = (sector["a"] + shift) % 360
            end = (sector["b"] + shift) % 360
            if clockwise(start, end) >= 350:
                continue
            turned.append({"a": round(start, 2), "b": round(end, 2), "color": sector["color"]})
        light["sectors"] = turned or None


def douglas_peucker(points: list, epsilon: float) -> list:
    if len(points) < 3:
        return points
    lat0 = points[len(points) // 2][1]
    scale = max(0.2, math.cos(math.radians(lat0)))

    def distance(point, start, end) -> float:
        x0, y0 = (point[0] - start[0]) * scale, point[1] - start[1]
        x1, y1 = (end[0] - start[0]) * scale, end[1] - start[1]
        denom = x1 * x1 + y1 * y1
        if denom == 0:
            return math.hypot(x0, y0)
        t = max(0.0, min(1.0, (x0 * x1 + y0 * y1) / denom))
        return math.hypot(x0 - t * x1, y0 - t * y1)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        left, right = stack.pop()
        max_dist = 0.0
        index = None
        for cursor in range(left + 1, right):
            dist = distance(points[cursor], points[left], points[right])
            if dist > max_dist:
                max_dist = dist
                index = cursor
        if index is not None and max_dist > epsilon:
            keep[index] = True
            stack.append((left, index))
            stack.append((index, right))
    return [point for point, flagged in zip(points, keep) if flagged]


def simplify_italy(features: list[dict]) -> list:
    rings = []
    before = 0
    for feature in features:
        geometry = feature["geometry"]
        polygons = (
            geometry["coordinates"]
            if geometry["type"] == "MultiPolygon"
            else [geometry["coordinates"]]
        )
        for polygon in polygons:
            for ring in polygon:
                before += len(ring)
                if len(ring) < 4:
                    continue
                lons = [point[0] for point in ring]
                lats = [point[1] for point in ring]
                span = max(max(lons) - min(lons), max(lats) - min(lats))
                # Keep islets. A 1 km tolerance would erase the rocks lights stand on.
                if span < 0.08 or len(ring) < 40:
                    epsilon = 0.0015
                elif span < 0.4:
                    epsilon = 0.0035
                else:
                    epsilon = 0.006
                simplified = douglas_peucker(ring, epsilon)
                if len(simplified) < 4:
                    simplified = ring[:: max(1, len(ring) // 12)]
                    if simplified[0] != simplified[-1]:
                        simplified.append(simplified[0])
                if len(simplified) < 4:
                    continue
                if simplified[0] != simplified[-1]:
                    simplified.append(simplified[0])
                rings.append([[round(x, 4), round(y, 4)] for x, y in simplified])
    after = sum(len(ring) for ring in rings)
    print(f"coast points {before} -> {after} across {len(rings)} rings")
    return rings


def main() -> None:
    index = load_index()
    features = json.loads((RAW / "it-regions.geojson").read_text())["features"]
    lights = parse_cards(index)
    orient_sectors(lights, features)
    for light in lights:
        light["lat"] = round(light["lat"], 5)
        light["lon"] = round(light["lon"], 5)
        light["range"] = round(light["range"], 2)
        if light["phases"]:
            light["phases"] = [round(value, 3) for value in light["phases"]]
        if light["beams"]:
            for beam in light["beams"]:
                beam["ang"] = round(beam["ang"], 2)
                beam["width"] = round(beam["width"], 2)
    lights.sort(key=lambda light: (-light["range"], light["name"]))
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "lights.json").write_text(
        json.dumps(lights, ensure_ascii=False, separators=(",", ":"))
    )
    rings = simplify_italy(features)
    (data_dir / "italy.json").write_text(
        json.dumps({"rings": rings}, separators=(",", ":"))
    )
    kinds = {}
    for light in lights:
        kinds[light["kind"]] = kinds.get(light["kind"], 0) + 1
    sectored = sum(1 for light in lights if light["sectors"])
    print(f"lights {len(lights)} kinds {kinds} with sectors {sectored}")
    print("sample", lights[0]["name"], lights[0]["character"], lights[0]["kind"])


if __name__ == "__main__":
    main()
