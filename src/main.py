import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "teams.json"
STATE_PATH = ROOT / "state" / "sent.json"
CACHE_PATH = ROOT / "state" / "fixtures_cache.json"

API_BASE = "https://v3.football.api-sports.io"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

PRE_MATCH_MINUTES = 30
START_GRACE_MINUTES = 60
STATE_RETENTION_DAYS = 3
CACHE_TTL_HOURS = 6
LOCAL_TZ = timezone(timedelta(hours=-3))   # Argentina (UTC-3, no DST)

KEEP_STATUSES = {"TBD", "NS", "1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def fetch_fixtures_for_team(team_id: int, season: int, api_key: str) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=14)
    r = requests.get(
        f"{API_BASE}/fixtures",
        headers={"x-apisports-key": api_key},
        params={
            "team": team_id,
            "season": season,
            "from": today.isoformat(),
            "to": end.isoformat(),
            "timezone": "UTC",
        },
        timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        print(f"API errors team={team_id} season={season}: {body['errors']}", file=sys.stderr)
        return []
    return body.get("response", [])


def fetch_team_fixtures(team: dict, api_key: str) -> list[dict]:
    base_season = int(team.get("season", datetime.now(timezone.utc).year))
    seasons = [base_season, base_season + 1]   # cover season rollover
    seen_ids = set()
    out = []
    for season in seasons:
        try:
            fixtures = fetch_fixtures_for_team(team["id"], season, api_key)
        except requests.RequestException as e:
            print(f"Failed to fetch {team['name']} season {season}: {e}", file=sys.stderr)
            continue
        for f in fixtures:
            if f["fixture"]["status"]["short"] not in KEEP_STATUSES:
                continue
            fid = f["fixture"]["id"]
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            out.append(f)
    return out


def send_whatsapp(phone: str, apikey: str, message: str) -> None:
    r = requests.get(
        CALLMEBOT_URL,
        params={"phone": phone, "text": message, "apikey": apikey},
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"CallMeBot error {r.status_code}: {r.text[:200]}", file=sys.stderr)
        r.raise_for_status()
    time.sleep(2)   # CallMeBot rate-limits


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def prune_state(state: dict, now: datetime) -> dict:
    cutoff = now - timedelta(days=STATE_RETENTION_DAYS)
    pruned = {}
    for fid, entry in state.items():
        try:
            ko = datetime.fromisoformat(entry["kickoff"])
        except (KeyError, ValueError):
            continue
        if ko > cutoff:
            pruned[fid] = entry
    return pruned


def cache_is_fresh(cache: dict, now: datetime) -> bool:
    if not cache:
        return False
    try:
        fetched = datetime.fromisoformat(cache["fetched_at"])
    except (KeyError, ValueError):
        return False
    return (now - fetched) < timedelta(hours=CACHE_TTL_HOURS)


def get_fixtures(teams: list[dict], api_key: str, now: datetime, force_refresh: bool) -> dict:
    """Return {team_id_str: [fixtures]}, using cache when fresh."""
    cache = load_json(CACHE_PATH, {})
    if not force_refresh and cache_is_fresh(cache, now) and "by_team" in cache:
        print(f"[cache] using cached fixtures (age {(now - datetime.fromisoformat(cache['fetched_at'])).total_seconds()/60:.0f} min)")
        return cache["by_team"]

    print("[cache] refreshing fixtures from API")
    by_team = {}
    any_success = False
    for team in teams:
        fixtures = fetch_team_fixtures(team, api_key)
        by_team[str(team["id"])] = fixtures
        if fixtures:
            any_success = True

    # Only update cache if we got at least one team's fixtures, otherwise keep stale cache
    if any_success or not cache.get("by_team"):
        save_json(CACHE_PATH, {
            "fetched_at": now.isoformat(),
            "by_team": by_team,
        })
        return by_team
    print("[cache] refresh returned nothing, falling back to stale cache", file=sys.stderr)
    return cache["by_team"]


def fmt_local(dt_utc: datetime) -> str:
    return dt_utc.astimezone(LOCAL_TZ).strftime("%H:%M")


def build_pre_message(team_emoji: str, league: str, home: str, away: str, kickoff_utc: datetime) -> str:
    return (
        f"{team_emoji} En 30 minutos\n"
        f"⚽ {home} vs {away}\n"
        f"🏆 {league}\n"
        f"🕐 {fmt_local(kickoff_utc)} hs"
    )


def build_start_message(team_emoji: str, league: str, home: str, away: str) -> str:
    return (
        f"{team_emoji} ¡EMPIEZA EL PARTIDO!\n"
        f"🔴 {home} vs {away}\n"
        f"🏆 {league}"
    )


def main() -> int:
    api_key = env("API_FOOTBALL_KEY")
    phone = env("WHATSAPP_PHONE")
    apikey = env("WHATSAPP_APIKEY")
    test_mode = os.environ.get("TEST_MODE", "").lower() in ("1", "true", "yes")

    teams = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["teams"]
    now = datetime.now(timezone.utc)
    state = prune_state(load_json(STATE_PATH, {}), now)

    if test_mode:
        print("[test] running in TEST_MODE — sending a sample message per team")

    fixtures_by_team = get_fixtures(teams, api_key, now, force_refresh=test_mode)

    for team in teams:
        team_id = str(team["id"])
        fixtures = fixtures_by_team.get(team_id, [])
        print(f"[fetch] {team['name']}: {len(fixtures)} upcoming fixtures")

        if test_mode:
            if not fixtures:
                try:
                    send_whatsapp(phone, apikey, f"[test] {team['name']}: la API no devolvio partidos proximos")
                except requests.RequestException as e:
                    print(f"WhatsApp test send failed: {e}", file=sys.stderr)
                continue
            fx = sorted(fixtures, key=lambda f: f["fixture"]["date"])[0]
            kickoff = datetime.fromisoformat(fx["fixture"]["date"].replace("Z", "+00:00"))
            league = fx["league"]["name"]
            home = fx["teams"]["home"]["name"]
            away = fx["teams"]["away"]["name"]
            msg = (
                f"[test] {team['emoji']} proximo partido\n"
                f"⚽ {home} vs {away}\n"
                f"🏆 {league}\n"
                f"🕐 {fmt_local(kickoff)} hs ({kickoff.astimezone(LOCAL_TZ).strftime('%d/%m')})"
            )
            try:
                send_whatsapp(phone, apikey, msg)
                print(f"[test] sent for {team['name']}")
            except requests.RequestException as e:
                print(f"WhatsApp test send failed: {e}", file=sys.stderr)
            continue

        for fx in fixtures:
            fixture_id = str(fx["fixture"]["id"])
            kickoff = datetime.fromisoformat(fx["fixture"]["date"].replace("Z", "+00:00"))
            league = fx["league"]["name"]
            home = fx["teams"]["home"]["name"]
            away = fx["teams"]["away"]["name"]
            minutes_until = (kickoff - now).total_seconds() / 60.0
            print(f"  - {home} vs {away} ({league}) en {minutes_until:.0f} min")

            entry = state.get(fixture_id, {
                "kickoff": kickoff.isoformat(),
                "team": team["name"],
                "match": f"{home} vs {away}",
                "league": league,
                "pre_sent": False,
                "start_sent": False,
            })
            entry["kickoff"] = kickoff.isoformat()

            if not entry["pre_sent"] and 0 < minutes_until <= PRE_MATCH_MINUTES:
                msg = build_pre_message(team["emoji"], league, home, away, kickoff)
                try:
                    send_whatsapp(phone, apikey, msg)
                    entry["pre_sent"] = True
                    print(f"[pre] {team['name']}: {home} vs {away} in {minutes_until:.0f} min")
                except requests.RequestException as e:
                    print(f"WhatsApp send failed (pre): {e}", file=sys.stderr)

            minutes_since = -minutes_until
            if not entry["start_sent"] and 0 <= minutes_since <= START_GRACE_MINUTES:
                msg = build_start_message(team["emoji"], league, home, away)
                try:
                    send_whatsapp(phone, apikey, msg)
                    entry["start_sent"] = True
                    print(f"[start] {team['name']}: {home} vs {away}")
                except requests.RequestException as e:
                    print(f"WhatsApp send failed (start): {e}", file=sys.stderr)

            state[fixture_id] = entry

    if not test_mode:
        save_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
