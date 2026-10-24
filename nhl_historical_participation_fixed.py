#!/usr/bin/env python3
"""
Build NHL regular-season participation tables for 1917-18 through 2025-26.

Outputs:
  nhl_season_summary.csv
  nhl_player_season.csv
  nhl_team_season.csv
  nhl_audit.csv
  nhl_historical_participation.xlsx

Source: NHL Stats REST API (public, undocumented API).
This script deliberately records API responses and audit flags instead of silently
filling missing historical data.

Important methodology note:
The NHL season-summary API provides player totals by player/team, but does not
reliably expose each player's exact roster-availability interval. Therefore this
implementation computes the denominator for a traded player as the sum of the
regular-season games played by each NHL team for which the player has a season
row. This is a reproducible proxy, NOT an exact count of games available during
the player's roster tenure. The audit sheet flags multi-team seasons. If exact
availability windows are required, game-level roster/transaction data must be
added before treating traded-player percentages as exact.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://api.nhle.com/stats/rest/en"
FIRST_SEASON = 19171918
LAST_SEASON = 20252026
GAME_TYPE_REGULAR = 2
SESSION = requests.Session()
retry_strategy = Retry(total=5, connect=5, read=5, backoff_factor=1.5,
                       status_forcelist=[429, 500, 502, 503, 504],
                       allowed_methods=["GET"], respect_retry_after_header=True)
adapter = HTTPAdapter(max_retries=retry_strategy)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)
SESSION.headers.update({"User-Agent": "NHL-Historical-Participation-Research/1.0"})


def season_ids(start: int = FIRST_SEASON, end: int = LAST_SEASON) -> list[int]:
    return [y * 10000 + (y + 1) for y in range(start // 10000, end // 10000 + 1)]


def api_report(entity: str, season: int, *, limit: int = -1) -> list[dict[str, Any]]:
    """Fetch one season report, checking HTTP and response shape."""
    url = f"{BASE}/{entity}/summary"
    params = {
        "isAggregate": "false",
        "isGame": "false",
        "sort": "points",
        "dir": "DESC",
        "start": 0,
        "limit": limit,
        # Season and game type are dimension filters and belong together in cayenneExp.
        "cayenneExp": f"seasonId={season} and gameTypeId={GAME_TYPE_REGULAR}",
    }
    response = SESSION.get(url, params=params, timeout=90)
    if not response.ok:
        raise RuntimeError(
            f"NHL API {entity} report for {season} returned HTTP {response.status_code}; "
            f"response body: {response.text[:1000]}"
        )
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(f"Unexpected API response for {entity}, {season}: {str(payload)[:500]}")
    return payload["data"]


def first_value(row: dict[str, Any], *keys: str, default=None):
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return default


def numeric(row: dict[str, Any], *keys: str, default=0) -> float:
    value = first_value(row, *keys, default=default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def season_label(season_id: int) -> str:
    y = season_id // 10000
    return f"{y}-{str(y + 1)[-2:]}"


def pct_category(pct: float, gp: int) -> str:
    # One-game players are separated before the percentage bands.
    if gp == 1:
        return "01_exactly_1_game"
    if pct <= 10:
        return "02_over_1_to_10_percent"
    if pct < 25:
        return "03_over_10_to_under_25_percent"
    if pct < 50:
        return "04_25_to_under_50_percent"
    if pct < 75:
        return "05_50_to_under_75_percent"
    if pct < 100:
        return "06_75_to_under_100_percent"
    return "07_all_available_team_games"


def fetch_data(seasons: list[int], pause: float = 0.15):
    players_all, teams_all, audits = [], [], []
    for idx, season in enumerate(seasons, start=1):
        print(f"[{idx}/{len(seasons)}] Fetching {season_label(season)}")
        try:
            skater_rows = api_report("skater", season)
            time.sleep(pause)
            team_rows = api_report("team", season)
            time.sleep(pause)
        except Exception as exc:
            audits.append({
                "season_id": season, "season": season_label(season),
                "check": "API_FETCH", "status": "ERROR", "details": repr(exc)
            })
            continue

        # Team rows: retain one record per team, deduplicating if API returns repeated dimensions.
        team_map = {}
        for row in team_rows:
            team_id = first_value(row, "teamId", "team_id")
            team_name = first_value(row, "teamFullName", "teamName", "team_name", default=str(team_id))
            gp = int(numeric(row, "gamesPlayed", "games_played", "gp"))
            if team_id is not None:
                team_map[str(team_id)] = {
                    "season_id": season, "season": season_label(season),
                    "team_id": str(team_id), "team": team_name, "team_games": gp,
                }
        for team in team_map.values():
            teams_all.append(team)

        # Player report can have team-split rows. Group by player, preserve the club splits.
        player_team_rows = []
        for row in skater_rows:
            player_id = first_value(row, "playerId", "player_id")
            if player_id is None:
                continue
            team_id = first_value(row, "teamId", "team_id")
            gp = int(numeric(row, "gamesPlayed", "games_played", "gp"))
            name = first_value(row, " skaterFullName", "skaterFullName", "playerName",
                               "fullName", "name", default=f"Player {player_id}")
            player_team_rows.append({
                "season_id": season, "season": season_label(season),
                "player_id": str(player_id), "player": name,
                "team_id": str(team_id) if team_id is not None else "",
                "team_games_played": gp,
                "goals": int(numeric(row, "goals")),
                "assists": int(numeric(row, "assists")),
                "points": int(numeric(row, "points")),
            })

        # Sum GP across team splits to get unique player season totals.
        by_player: dict[str, list[dict[str, Any]]] = {}
        for row in player_team_rows:
            by_player.setdefault(row["player_id"], []).append(row)

        season_player_rows = []
        for pid, splits in by_player.items():
            gp = sum(r["team_games_played"] for r in splits)
            club_ids = sorted({r["team_id"] for r in splits if r["team_id"]})
            available = sum(team_map.get(tid, {}).get("team_games", 0) for tid in club_ids)
            # This denominator is explicitly a proxy for traded players; see README.
            pct = (100.0 * gp / available) if available > 0 else math.nan
            category = pct_category(pct, gp) if not math.isnan(pct) else "99_unclassified"
            season_player_rows.append({
                "season_id": season, "season": season_label(season),
                "player_id": pid, "player": splits[0]["player"],
                "clubs_count": len(club_ids), "clubs": "; ".join(club_ids),
                "games_played": gp, "available_team_games_proxy": available,
                "games_pct_proxy": round(pct, 4) if not math.isnan(pct) else None,
                "category": category,
                "goals": sum(r["goals"] for r in splits),
                "assists": sum(r["assists"] for r in splits),
                "points": sum(r["points"] for r in splits),
            })
        players_all.extend(season_player_rows)

        total_players = len(season_player_rows)
        total_teams = len(team_map)
        team_games_list = [v["team_games"] for v in team_map.values()]
        unique_team_gp = sorted(set(team_games_list))
        audits.extend([
            {"season_id": season, "season": season_label(season), "check": "TEAM_COUNT",
             "status": "OK" if total_teams else "ERROR", "details": f"teams={total_teams}"},
            {"season_id": season, "season": season_label(season), "check": "PLAYER_COUNT",
             "status": "OK" if total_players else "ERROR", "details": f"unique_players={total_players}"},
            {"season_id": season, "season": season_label(season), "check": "TEAM_GAMES_SHAPE",
             "status": "OK" if len(unique_team_gp) <= 2 else "REVIEW",
             "details": f"team_GP_values={unique_team_gp}"},
            {"season_id": season, "season": season_label(season), "check": "MULTI_TEAM_PLAYERS",
             "status": "REVIEW" if any(p["clubs_count"] > 1 for p in season_player_rows) else "OK",
             "details": f"multi_team_players={sum(p['clubs_count'] > 1 for p in season_player_rows)}; denominator is season-team GP proxy"},
        ])
        # Conservation check: categories should partition all unique players.
        categorized = sum(1 for p in season_player_rows if p["category"] != "99_unclassified")
        audits.append({
            "season_id": season, "season": season_label(season), "check": "CATEGORY_CONSERVATION",
            "status": "OK" if categorized == total_players else "ERROR",
            "details": f"players={total_players}; categorized={categorized}",
        })

    return pd.DataFrame(players_all), pd.DataFrame(teams_all), pd.DataFrame(audits)


def build_summary(players: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    if players.empty:
        return pd.DataFrame()
    team_summary = teams.groupby(["season_id", "season"], as_index=False).agg(
        teams=("team_id", "nunique"),
        total_team_games=("team_games", "sum"),
        min_team_games=("team_games", "min"),
        max_team_games=("team_games", "max"),
    )
    # One-game category is separated. The next band is >1 game and <=10%.
    categories = [
        ("players_exactly_1_game", "01_exactly_1_game"),
        ("players_over_1_to_10_pct", "02_over_1_to_10_percent"),
        ("players_over_10_under_25_pct", "03_over_10_to_under_25_percent"),
        ("players_25_to_under_50_pct", "04_25_to_under_50_percent"),
        ("players_50_to_under_75_pct", "05_50_to_under_75_percent"),
        ("players_75_to_under_100_pct", "06_75_to_under_100_percent"),
        ("players_all_available_games", "07_all_available_team_games"),
    ]
    grouped = players.groupby(["season_id", "season"], as_index=False).agg(
        unique_players=("player_id", "nunique"),
        multi_team_players=("clubs_count", lambda x: int((x > 1).sum())),
    )
    for col, cat in categories:
        temp = players[players["category"] == cat].groupby("season_id")["player_id"].nunique()
        grouped[col] = grouped["season_id"].map(temp).fillna(0).astype(int)
    grouped["category_sum"] = grouped[[c for c, _ in categories]].sum(axis=1)
    grouped["category_check"] = grouped["category_sum"] == grouped["unique_players"]
    out = team_summary.merge(grouped, on=["season_id", "season"], how="outer")
    out["teams"] = out["teams"].fillna(0).astype(int)
    out["unique_players"] = out["unique_players"].fillna(0).astype(int)
    out["pct_one_game"] = (100 * out["players_exactly_1_game"] / out["unique_players"]).round(3)
    out["pct_multi_team"] = (100 * out["multi_team_players"] / out["unique_players"]).round(3)
    return out.sort_values("season_id")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=19171918, help="First season ID, e.g. 19171918")
    parser.add_argument("--end", type=int, default=20252026, help="Last season ID, e.g. 20252026")
    parser.add_argument("--outdir", default="nhl_historical_output")
    parser.add_argument("--pause", type=float, default=0.15, help="Pause between API calls, seconds")
    args = parser.parse_args()
    start_year, end_year = args.start // 10000, args.end // 10000
    seasons = [y * 10000 + y + 1 for y in range(start_year, end_year + 1)]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    players, teams, audit = fetch_data(seasons, args.pause)
    summary = build_summary(players, teams)

    # Save CSVs as UTF-8 with BOM for convenient opening in Excel.
    summary.to_csv(outdir / "nhl_season_summary.csv", index=False, encoding="utf-8-sig")
    players.to_csv(outdir / "nhl_player_season.csv", index=False, encoding="utf-8-sig")
    teams.to_csv(outdir / "nhl_team_season.csv", index=False, encoding="utf-8-sig")
    audit.to_csv(outdir / "nhl_audit.csv", index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(outdir / "nhl_historical_participation.xlsx", engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Season Summary", index=False)
        players.to_excel(writer, sheet_name="Player Season", index=False)
        teams.to_excel(writer, sheet_name="Team Season", index=False)
        audit.to_excel(writer, sheet_name="Audit", index=False)

    print(f"\nFinished. Outputs saved to: {outdir.resolve()}")
    print(f"Seasons requested: {len(seasons)}; seasons with summary rows: {len(summary)}")
    print("Review nhl_audit.csv for fetch errors and multi-team denominator warnings.")
    print("Do not treat traded-player percentages as exact until roster-tenure windows are added.")


if __name__ == "__main__":
    main()
