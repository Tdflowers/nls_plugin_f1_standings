"""
F1 worker - fetches driver standings, constructor standings, and the upcoming
race schedule from the Jolpica F1 API (https://api.jolpi.ca), an
Ergast-compatible JSON service.
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

from utils import sb_cache

debug = logging.getLogger("scoreboard")

CACHE_KEY_DRIVERS      = "f1_driver_standings"
CACHE_KEY_CONSTRUCTORS = "f1_constructor_standings"
CACHE_KEY_NEXT_RACE    = "f1_next_race"

_BASE_URL = "https://api.jolpi.ca/ergast/f1"

_HEADERS = {
    "User-Agent": "NHLLEDScoreboard/1.0 (F1 standings plugin)",
    "Accept": "application/json",
}

# Known F1 team/constructor IDs -> short 3-letter display code
TEAM_SHORT = {
    "red_bull":         "RBR",
    "ferrari":          "FER",
    "mercedes":         "MER",
    "mclaren":          "MCL",
    "aston_martin":     "AMR",
    "alpine":           "ALP",
    "williams":         "WIL",
    "haas":             "HAS",
    "kick_sauber":      "SAU",
    "sauber":           "SAU",
    "rb":               "RB",
    "racing_bulls":     "RB",
    "visa_cash_app_rb": "RB",
}

# Weekend session keys in chronological display order
_SESSION_ORDER = [
    ("FirstPractice",    "FP1"),
    ("SecondPractice",   "FP2"),
    ("ThirdPractice",    "FP3"),
    ("SprintQualifying", "SQ"),
    ("Sprint",           "SPR"),
    ("Qualifying",       "Q"),
]


def _current_season():
    return datetime.now().year


def _get(url):
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        debug.warning(f"F1 worker: request failed for {url}: {e}")
        return None
    except (json.JSONDecodeError, ValueError) as e:
        debug.warning(f"F1 worker: JSON decode error for {url}: {e}")
        return None


def _parse_dt(date_str, time_str="00:00:00Z"):
    try:
        dt = datetime.strptime(f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------
# Driver standings
# ------------------------------------------------------------------

def fetch_drivers(season=None, cache_ttl=7200):
    """Fetch, parse, and cache F1 driver standings. Returns list or None."""
    if season is None:
        season = _current_season()

    url = f"{_BASE_URL}/{season}/driverStandings.json"
    data = _get(url)
    if not data:
        return None

    try:
        standings_lists = data["MRData"]["StandingsTable"]["StandingsLists"]
        if not standings_lists:
            debug.warning(f"F1 worker: no driver standings lists returned for {season}")
            return None
        raw_drivers = standings_lists[0]["DriverStandings"]
    except (KeyError, IndexError) as e:
        debug.warning(f"F1 worker: unexpected driver standings structure: {e}")
        return None

    drivers = []
    for entry in raw_drivers:
        try:
            driver      = entry["Driver"]
            constructor = entry["Constructors"][0]
            team_id     = constructor["constructorId"]
            code        = driver.get("code") or driver.get("familyName", "???")[:3].upper()
            drivers.append({
                "position": int(entry["position"]),
                "code":     code,
                "name":     f"{driver['givenName']} {driver['familyName']}",
                "team":     constructor["name"],
                "team_id":  team_id,
                "points":   float(entry["points"]),
            })
        except (KeyError, ValueError, IndexError) as e:
            debug.warning(f"F1 worker: skipping malformed driver entry: {e}")

    if not drivers:
        debug.warning(f"F1 worker: parsed 0 drivers for {season}")
        return None

    debug.info(f"F1 worker: cached {len(drivers)} driver standings for {season} (TTL {cache_ttl}s)")
    sb_cache.set(CACHE_KEY_DRIVERS, drivers, expire=cache_ttl)
    return drivers


# ------------------------------------------------------------------
# Constructor standings
# ------------------------------------------------------------------

def fetch_constructors(season=None, cache_ttl=7200):
    """Fetch, parse, and cache F1 constructor standings. Returns list or None."""
    if season is None:
        season = _current_season()

    url = f"{_BASE_URL}/{season}/constructorStandings.json"
    data = _get(url)
    if not data:
        return None

    try:
        standings_lists = data["MRData"]["StandingsTable"]["StandingsLists"]
        if not standings_lists:
            debug.warning(f"F1 worker: no constructor standings lists returned for {season}")
            return None
        raw_constructors = standings_lists[0]["ConstructorStandings"]
    except (KeyError, IndexError) as e:
        debug.warning(f"F1 worker: unexpected constructor standings structure: {e}")
        return None

    constructors = []
    for entry in raw_constructors:
        try:
            constructor = entry["Constructor"]
            team_id     = constructor["constructorId"]
            constructors.append({
                "position": int(entry["position"]),
                "name":     constructor["name"],
                "team_id":  team_id,
                "short":    TEAM_SHORT.get(team_id, constructor["name"][:3].upper()),
                "points":   float(entry["points"]),
            })
        except (KeyError, ValueError) as e:
            debug.warning(f"F1 worker: skipping malformed constructor entry: {e}")

    if not constructors:
        debug.warning(f"F1 worker: parsed 0 constructors for {season}")
        return None

    debug.info(f"F1 worker: cached {len(constructors)} constructor standings for {season} (TTL {cache_ttl}s)")
    sb_cache.set(CACHE_KEY_CONSTRUCTORS, constructors, expire=cache_ttl)
    return constructors


# ------------------------------------------------------------------
# Next race schedule
# ------------------------------------------------------------------

def fetch_next_race(season=None, cache_ttl=7200):
    """Fetch, parse, and cache the next upcoming race. Returns dict or None."""
    if season is None:
        season = _current_season()

    url  = f"{_BASE_URL}/{season}/races/"
    data = _get(url)
    if not data:
        return None

    try:
        races = data["MRData"]["RaceTable"]["Races"]
    except KeyError as e:
        debug.warning(f"F1 worker: unexpected races API structure: {e}")
        return None

    now            = datetime.now(timezone.utc)
    next_race_data = None

    for race in races:
        race_dt = _parse_dt(race.get("date"), race.get("time", "00:00:00Z"))
        if race_dt and race_dt > now:
            next_race_data = race
            break

    if not next_race_data:
        debug.warning("F1 worker: no upcoming race found for season")
        return None

    race_dt  = _parse_dt(next_race_data["date"], next_race_data.get("time", "00:00:00Z"))
    sessions = []
    for api_key, label in _SESSION_ORDER:
        if api_key in next_race_data:
            s  = next_race_data[api_key]
            dt = _parse_dt(s.get("date"), s.get("time", "00:00:00Z"))
            if dt:
                sessions.append({"label": label, "dt": dt})
    sessions.append({"label": "Race", "dt": race_dt})

    circuit_data = next_race_data.get("Circuit", {})
    location     = circuit_data.get("Location", {})
    result = {
        "round":    int(next_race_data["round"]),
        "name":     next_race_data["raceName"],
        "dt":       race_dt,
        "circuit":  circuit_data.get("circuitName", ""),
        "locality": location.get("locality", ""),
        "country":  location.get("country", ""),
        "sessions": sessions,
    }

    sb_cache.set(CACHE_KEY_NEXT_RACE, result, expire=cache_ttl)
    debug.info(
        f"F1 worker: cached Round {result['round']} - {result['name']} "
        f"({len(sessions)} sessions, TTL {cache_ttl}s)"
    )
    return result


# ------------------------------------------------------------------
# Scheduled fetch (called by both boards)
# ------------------------------------------------------------------

def fetch(cache_ttl=7200):
    """Fetch all data: driver standings, constructor standings, next race."""
    fetch_drivers(cache_ttl=cache_ttl)
    fetch_constructors(cache_ttl=cache_ttl)
    fetch_next_race(cache_ttl=cache_ttl)


def get_cached_drivers():
    return sb_cache.get(CACHE_KEY_DRIVERS)


def get_cached_constructors():
    return sb_cache.get(CACHE_KEY_CONSTRUCTORS)


def get_cached_next_race():
    return sb_cache.get(CACHE_KEY_NEXT_RACE)
