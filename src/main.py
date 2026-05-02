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

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"

PRE_MATCH_MARKS = [60, 45, 30, 15]   # minutes before kickoff to remind, largest first
START_GRACE_MINUTES = 60
STATE_RETENTION_DAYS = 3
CACHE_TTL_HOURS = 6
DAYS_AHEAD = 14
LOCAL_TZ = timezone(timedelta(hours=-3))   # Argentina (UTC-3, no DST)

ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"Missing env var: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def fetch_league_events(league_slug: str) -> tuple[str, list[dict]]:
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=DAYS_AHEAD)
    date_param = f"{today.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    r = requests.get(
        f"{ESPN_BASE}/{league_slug}/scoreboard",
        params={"dates": date_param},
        headers=ESPN_HEADERS,
        timeout=20,
    )
    if r.status_code == 404:
        print(f"ESPN: league {league_slug} not found (404)", file=sys.stderr)
        return league_slug, []
    if r.status_code >= 400:
        print(f"ESPN HTTP {r.status_code} for {league_slug}: {r.text[:200]}", file=sys.stderr)
        return league_slug, []
    body = r.json()
    leagues = body.get("leagues") or []
    name = (leagues[0].get("name") if leagues else league_slug) or league_slug
    return name, body.get("events", []) or []


def extract_fixture(ev: dict, league_name: str) -> dict | None:
    comp = (ev.get("competitions") or [{}])[0]
    competitors = comp.get("competitors", []) or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_name = (home.get("team") or {}).get("displayName", "")
    away_name = (away.get("team") or {}).get("displayName", "")
    if not home_name or not away_name:
        return None
    state = (((ev.get("status") or {}).get("type") or {}).get("state", ""))
    if state == "post":   # finished
        return None
    raw_date = ev.get("date")
    if not raw_date:
        return None
    try:
        kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
    except ValueError:
        return None
    return {
        "id": str(ev.get("id")),
        "kickoff": kickoff.isoformat(),
        "league": league_name,
        "home": home_name,
        "away": away_name,
    }


def fetch_team_fixtures(team: dict, league_cache: dict) -> list[dict]:
    out = []
    seen = set()
    match_str = team["match"].lower()
    for slug in team.get("leagues", []):
        if slug not in league_cache:
            league_cache[slug] = fetch_league_events(slug)
        league_name, events = league_cache[slug]
        for ev in events:
            fx = extract_fixture(ev, league_name)
            if not fx:
                continue
            if match_str not in fx["home"].lower() and match_str not in fx["away"].lower():
                continue
            if fx["id"] in seen:
                continue
            seen.add(fx["id"])
            out.append(fx)
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
    cache_has_data = any(by_team_cached.get(t["name"]) for t in teams)
    if not force_refresh and cache_is_fresh(cache, now) and cache_has_data:
        age_min = (now - datetime.fromisoformat(cache["fetched_at"])).total_seconds() / 60
        print(f"[cache] using cached fixtures (age {age_min:.0f} min)")
        return by_team_cached

    print("[cache] refreshing fixtures from ESPN")
    league_cache: dict[str, tuple[str, list[dict]]] = {}
    by_team = {}
    any_success = False
    for team in teams:
        fixtures = fetch_team_fixtures(team, league_cache)
        by_team[team["name"]] = fixtures
        if fixtures:
            any_success = True

    fetched_any_league = any(events for _, events in league_cache.values())
    if any_success or fetched_any_league or not cache.get("by_team"):
        save_json(CACHE_PATH, {"fetched_at": now.isoformat(), "by_team": by_team})
        return by_team
    print("[cache] refresh failed, falling back to stale cache", file=sys.stderr)
    return cache.get("by_team", {})


def fmt_local(dt_utc: datetime) -> str:
    return dt_utc.astimezone(LOCAL_TZ).strftime("%H:%M")


def build_pre_message(team_emoji: str, league: str, home: str, away: str, kickoff_utc: datetime, minutes_left: int) -> str:
    return (
        f"{team_emoji} En {minutes_left} minutos\n"
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
        fixtures = fixtures_by_team.get(team["name"], [])
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
                "pre_marks_sent": [],
                "start_sent": False,
            })
            entry["kickoff"] = kickoff.isoformat()
            # migrate from old format (single pre_sent flag)
            if "pre_marks_sent" not in entry:
                entry["pre_marks_sent"] = list(PRE_MATCH_MARKS) if entry.get("pre_sent") else []
            entry.pop("pre_sent", None)

            unsent_passed = [m for m in PRE_MATCH_MARKS
                             if m not in entry["pre_marks_sent"] and 0 < minutes_until <= m]
            if unsent_passed:
                shown_minutes = max(int(round(minutes_until)), 1)
                msg = build_pre_message(team["emoji"], league, home, away, kickoff, shown_minutes)
                try:
                    send_whatsapp(phone, apikey, msg)
                    entry["pre_marks_sent"] = sorted(set(entry["pre_marks_sent"] + unsent_passed))
                    print(f"[pre] {team['name']}: {home} vs {away} in {minutes_until:.0f} min (marks fired: {unsent_passed})")
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
