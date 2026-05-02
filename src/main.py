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

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

PRE_MATCH_MINUTES = 30
START_GRACE_MINUTES = 60
STATE_RETENTION_DAYS = 3
CACHE_TTL_HOURS = 6
LOCAL_TZ = timezone(timedelta(hours=-3))   # Argentina (UTC-3, no DST)

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}

SKIP_STATUSES = {"finished", "canceled"}


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def fetch_team_fixtures(team: dict) -> list[dict]:
    out = []
    seen = set()
    for page in range(2):   # ~30 events max, plenty
        try:
            r = requests.get(
                f"{SOFASCORE_BASE}/team/{team['id']}/events/next/{page}",
                headers=SOFASCORE_HEADERS,
                timeout=20,
            )
        except requests.RequestException as e:
            print(f"Sofascore error for {team['name']} page {page}: {e}", file=sys.stderr)
            break
        if r.status_code == 404:
            break
        if r.status_code >= 400:
            print(f"Sofascore HTTP {r.status_code} for {team['name']}: {r.text[:200]}", file=sys.stderr)
            break
        body = r.json()
        events = body.get("events", []) or []
        for ev in events:
            status_type = (ev.get("status") or {}).get("type", "")
            if status_type in SKIP_STATUSES:
                continue
            fid = ev["id"]
            if fid in seen:
                continue
            seen.add(fid)
            kickoff = datetime.fromtimestamp(ev["startTimestamp"], tz=timezone.utc)
            out.append({
                "id": str(fid),
                "kickoff": kickoff.isoformat(),
                "league": (ev.get("tournament") or {}).get("name", ""),
                "home": (ev.get("homeTeam") or {}).get("name", ""),
                "away": (ev.get("awayTeam") or {}).get("name", ""),
            })
        if not body.get("hasNextPage"):
            break
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
    time.sleep(2)


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


def get_fixtures(teams: list[dict], now: datetime, force_refresh: bool) -> dict:
    cache = load_json(CACHE_PATH, {})
    by_team_cached = cache.get("by_team", {})
    cache_has_data = any(by_team_cached.get(str(t["id"])) for t in teams)
    if not force_refresh and cache_is_fresh(cache, now) and cache_has_data:
        age_min = (now - datetime.fromisoformat(cache["fetched_at"])).total_seconds() / 60
        print(f"[cache] using cached fixtures (age {age_min:.0f} min)")
        return by_team_cached

    print("[cache] refreshing fixtures from Sofascore")
    by_team = {}
    any_success = False
    for team in teams:
        fixtures = fetch_team_fixtures(team)
        by_team[str(team["id"])] = fixtures
        if fixtures:
            any_success = True

    if any_success or not cache.get("by_team"):
        save_json(CACHE_PATH, {"fetched_at": now.isoformat(), "by_team": by_team})
        return by_team
    print("[cache] refresh failed, falling back to stale cache", file=sys.stderr)
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
    phone = env("WHATSAPP_PHONE")
    apikey = env("WHATSAPP_APIKEY")
    test_mode = os.environ.get("TEST_MODE", "").lower() in ("1", "true", "yes")

    teams = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["teams"]
    now = datetime.now(timezone.utc)
    state = prune_state(load_json(STATE_PATH, {}), now)

    if test_mode:
        print("[test] running in TEST_MODE — sending a sample message per team")

    fixtures_by_team = get_fixtures(teams, now, force_refresh=test_mode)

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
            fx = sorted(fixtures, key=lambda f: f["kickoff"])[0]
            kickoff = datetime.fromisoformat(fx["kickoff"])
            msg = (
                f"[test] {team['emoji']} proximo partido\n"
                f"⚽ {fx['home']} vs {fx['away']}\n"
                f"🏆 {fx['league']}\n"
                f"🕐 {fmt_local(kickoff)} hs ({kickoff.astimezone(LOCAL_TZ).strftime('%d/%m')})"
            )
            try:
                send_whatsapp(phone, apikey, msg)
                print(f"[test] sent for {team['name']}")
            except requests.RequestException as e:
                print(f"WhatsApp test send failed: {e}", file=sys.stderr)
            continue

        for fx in fixtures:
            fixture_id = fx["id"]
            kickoff = datetime.fromisoformat(fx["kickoff"])
            league = fx["league"]
            home = fx["home"]
            away = fx["away"]
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
