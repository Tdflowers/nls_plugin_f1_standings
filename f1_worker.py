"""
F1 standings worker - fetches driver and constructor standings from formula1.com.

formula1.com renders via Next.js; the page embeds a __NEXT_DATA__ JSON blob
that we extract first. If the JSON path changes (as F1 updates their site),
the HTML table parser acts as a fallback.
"""
import json
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from utils import sb_cache

debug = logging.getLogger("scoreboard")

CACHE_KEY_DRIVERS = "f1_driver_standings"
CACHE_KEY_CONSTRUCTORS = "f1_constructor_standings"
CACHE_TTL = 3600  # seconds

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Known F1 team IDs -> short 3-letter display code
TEAM_SHORT = {
    "red_bull": "RBR",
    "ferrari": "FER",
    "mercedes": "MER",
    "mclaren": "MCL",
    "aston_martin": "AMR",
    "alpine": "ALP",
    "williams": "WIL",
    "haas": "HAS",
    "kick_sauber": "SAU",
    "sauber": "SAU",
    "rb": "RB",
    "racing_bulls": "RB",
    "visa_cash_app_rb": "RB",
}


def _current_season():
    return datetime.now().year


def _fetch_page(url):
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        debug.warning(f"F1 worker: failed to fetch {url}: {e}")
        return None


def _extract_next_data(html):
    """Return parsed __NEXT_DATA__ JSON, or None if absent/unparseable."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            return json.loads(script.string)
        except json.JSONDecodeError:
            pass
    return None


# Candidate paths inside __NEXT_DATA__ that may contain the driver list.
# formula1.com has changed this structure across seasons; we try all of them.
_DRIVER_PATHS = [
    ["props", "pageProps", "resultsarchive", "series", "0",
     "StandingsTable", "StandingsLists", "0", "DriverStandings"],
    ["props", "pageProps", "standings", "drivers"],
    ["props", "pageProps", "driverStandings"],
    ["props", "pageProps", "results", "drivers"],
]

_CONSTRUCTOR_PATHS = [
    ["props", "pageProps", "resultsarchive", "series", "0",
     "StandingsTable", "StandingsLists", "0", "ConstructorStandings"],
    ["props", "pageProps", "standings", "constructors"],
    ["props", "pageProps", "constructorStandings"],
    ["props", "pageProps", "results", "constructors"],
]


def _traverse(data, path):
    node = data
    for key in path:
        if isinstance(node, list):
            node = node[int(key)]
        else:
            node = node[key]
    return node


def _find_in_next_data(data, paths):
    for path in paths:
        try:
            result = _traverse(data, path)
            if isinstance(result, list) and result:
                return result
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None


def _normalize_driver(entry):
    """Map a raw Next.js driver standings entry to our dict schema."""
    try:
        driver = entry.get("Driver", {})
        constructor = entry.get("Constructor", {})

        code = driver.get("code", "")
        if not code:
            family = driver.get("familyName", "")
            code = family[:3].upper() if family else "???"

        team_name = constructor.get("name", "")
        team_id = constructor.get("constructorId", "")
        if not team_id:
            team_id = team_name.lower().replace(" ", "_").replace("-", "_")

        pts_raw = entry.get("points", "0")
        points = float(pts_raw)

        return {
            "position": int(entry.get("position", 0)),
            "code": code,
            "name": f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
            "team": team_name,
            "team_id": team_id,
            "points": points,
        }
    except (ValueError, TypeError):
        return None


def _normalize_constructor(entry):
    """Map a raw Next.js constructor standings entry to our dict schema."""
    try:
        constructor = entry.get("Constructor", {})
        team_name = constructor.get("name", "")
        team_id = constructor.get("constructorId", "")
        if not team_id:
            team_id = team_name.lower().replace(" ", "_").replace("-", "_")

        pts_raw = entry.get("points", "0")
        points = float(pts_raw)

        return {
            "position": int(entry.get("position", 0)),
            "name": team_name,
            "team_id": team_id,
            "short": TEAM_SHORT.get(team_id, team_name[:3].upper()),
            "points": points,
        }
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# HTML table fallback parsers
# ---------------------------------------------------------------------------

def _parse_drivers_html(html):
    """
    Parse driver standings from an HTML <table>.

    formula1.com driver rows typically look like:
      <td>1</td>
      <td><span class="...">VER</span> Max Verstappen ...</td>
      <td>NED</td>
      <td>Red Bull Racing</td>
      <td>456</td>
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue

        parsed = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue

            pos_text = cells[0].get_text(strip=True)
            if not pos_text.isdigit():
                continue
            pos = int(pos_text)

            # Driver code: look for a short all-caps span (VER, HAM, …)
            driver_cell = cells[1]
            code = None
            for span in driver_cell.find_all("span"):
                text = span.get_text(strip=True)
                if len(text) == 3 and text.isupper() and text.isalpha():
                    code = text
                    break
            if not code:
                # Fallback: last 3 chars of the last word in the cell
                words = driver_cell.get_text(strip=True).split()
                code = words[-1][:3].upper() if words else "???"

            full_name = driver_cell.get_text(" ", strip=True)

            # Team is usually the second-to-last cell, points the last
            team_name = cells[-2].get_text(strip=True) if len(cells) >= 5 else ""
            pts_text = cells[-1].get_text(strip=True)
            try:
                points = float(pts_text)
            except ValueError:
                continue

            team_id = team_name.lower().replace(" ", "_").replace("-", "_")
            parsed.append({
                "position": pos,
                "code": code,
                "name": full_name,
                "team": team_name,
                "team_id": team_id,
                "points": points,
            })

        if parsed:
            return parsed

    return None


def _parse_constructors_html(html):
    """Parse constructor standings from an HTML <table>."""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue

        parsed = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            pos_text = cells[0].get_text(strip=True)
            if not pos_text.isdigit():
                continue
            pos = int(pos_text)

            team_name = cells[1].get_text(strip=True)
            pts_text = cells[-1].get_text(strip=True)
            try:
                points = float(pts_text)
            except ValueError:
                continue

            team_id = team_name.lower().replace(" ", "_").replace("-", "_")
            parsed.append({
                "position": pos,
                "name": team_name,
                "team_id": team_id,
                "short": TEAM_SHORT.get(team_id, team_name[:3].upper()),
                "points": points,
            })

        if parsed:
            return parsed

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_drivers(season=None):
    """Fetch, parse, and cache F1 driver standings. Returns list or None."""
    if season is None:
        season = _current_season()

    url = f"https://www.formula1.com/en/results/{season}/drivers"
    html = _fetch_page(url)
    if not html:
        return None

    drivers = None

    next_data = _extract_next_data(html)
    if next_data:
        raw = _find_in_next_data(next_data, _DRIVER_PATHS)
        if raw:
            drivers = [_normalize_driver(e) for e in raw]
            drivers = [d for d in drivers if d]

    if not drivers:
        drivers = _parse_drivers_html(html)

    if not drivers:
        debug.warning(f"F1 worker: could not parse driver standings for {season}")
        return None

    debug.info(f"F1 worker: cached {len(drivers)} driver standings for {season}")
    sb_cache.set(CACHE_KEY_DRIVERS, drivers, expire=CACHE_TTL)
    return drivers


def fetch_constructors(season=None):
    """Fetch, parse, and cache F1 constructor standings. Returns list or None."""
    if season is None:
        season = _current_season()

    url = f"https://www.formula1.com/en/results/{season}/team"
    html = _fetch_page(url)
    if not html:
        return None

    constructors = None

    next_data = _extract_next_data(html)
    if next_data:
        raw = _find_in_next_data(next_data, _CONSTRUCTOR_PATHS)
        if raw:
            constructors = [_normalize_constructor(e) for e in raw]
            constructors = [c for c in constructors if c]

    if not constructors:
        constructors = _parse_constructors_html(html)

    if not constructors:
        debug.warning(f"F1 worker: could not parse constructor standings for {season}")
        return None

    debug.info(f"F1 worker: cached {len(constructors)} constructor standings for {season}")
    sb_cache.set(CACHE_KEY_CONSTRUCTORS, constructors, expire=CACHE_TTL)
    return constructors


def fetch():
    """Fetch both driver and constructor standings (called by scheduler)."""
    fetch_drivers()
    fetch_constructors()


def get_cached_drivers():
    return sb_cache.get(CACHE_KEY_DRIVERS)


def get_cached_constructors():
    return sb_cache.get(CACHE_KEY_CONSTRUCTORS)
