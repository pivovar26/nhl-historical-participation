# NHL Historical Participation — GitHub Actions

This repository runs the NHL regular-season participation collector in GitHub's cloud runner and publishes CSV/Excel outputs as a downloadable artifact. No local Python installation is needed.

## Files
- `nhl_historical_participation.py` — collector
- `requirements.txt` — Python dependencies
- `.github/workflows/build-nhl-history.yml` — manual cloud workflow
- `nhl_season_summary.csv` — season summary
- `nhl_player_season.csv` — one row per unique player per season
- `nhl_team_season.csv` — team game counts
- `nhl_audit.csv` — checks and warnings
- `nhl_historical_participation.xlsx` — Excel workbook with four sheets

## Setup
1. Sign in at https://github.com/ and create a repository named `nhl-historical-participation`.
2. Choose a **public** repository if you want to use standard GitHub-hosted runners under the public-repository terms. Check current GitHub plan limits before relying on free usage.
3. Upload all files in this package, preserving `.github/workflows/build-nhl-history.yml` exactly, then commit them.
4. Open the repository's **Actions** tab and enable workflows if prompted.
5. Choose **Build NHL historical participation tables** → **Run workflow**.
6. Test first with start `2024`, end `2025`. Open the run and download the artifact shown under **Artifacts**.
7. Inspect `nhl_audit.csv`. If the test works, run start `1917`, end `2025`.

## If a full run times out
Run smaller chunks:
- 1917–1949
- 1950–1979
- 1980–2009
- 2010–2025

Each run creates its own downloadable artifact. Combine CSVs after downloading, removing repeated header rows.

## Important data-quality caveat
This is a reproducible first-pass collection pipeline, not a certified final dataset. The public NHL Stats REST API may change. More importantly, the current traded-player denominator sums full-season games for each club represented in the player's season rows. This is a **proxy**, not the exact number of games available to that player during each club tenure. Multi-team seasons are flagged in the audit. Exact percentages for traded players require roster-tenure/game-level data.

The exactly-one-game category is based on summed regular-season games played across a player's club rows. A player with one game for each of two clubs is counted as having played two games in the season.

## Categories
1. Exactly 1 game
2. More than 1 game and at most 10%
3. More than 10% and less than 25%
4. At least 25% and less than 50%
5. At least 50% and less than 75%
6. At least 75% but less than 100%
7. 100% of available team games (proxy)

## Troubleshooting
- Missing workflow: check that `.github/workflows/build-nhl-history.yml` was committed at that exact path.
- API errors/rate limits: rerun the workflow; the script retries common transient HTTP failures.
- No artifact: open the run logs and inspect the failed step.
- Treat `ERROR` and `REVIEW` in `nhl_audit.csv` as items to investigate. A successful API response does not prove historical completeness.
- Source: https://api.nhle.com/stats/rest/en
