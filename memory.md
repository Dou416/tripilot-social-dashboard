# Social Media Content Dashboard

## Purpose
Unified view of Tripilot's social media content performance across 4 platforms: Reddit, Twitter/X, TikTok, Xiaohongshu.

## Architecture
- `collector.py` — Python script that fetches data from all 4 platforms
- `data/social_data.json` — Collected data (generated output)
- `dashboard.html` — Static HTML dashboard (Chart.js, vanilla JS, dark theme)
- `config.yaml` — Platform account config (usernames, user IDs)
- `.env` — Secrets (cookies, passwords) — not committed

## Data Flow
`collector.py` → `data/social_data.json` → `dashboard.html` reads via `fetch()`

## Platform Status
| Platform | Method | Auth Required | Status |
|----------|--------|---------------|--------|
| Reddit | Public `.json` API | None (User-Agent only) | Working |
| Twitter/X | `twikit` library | Username + password in .env | Stubbed, ready to enable |
| TikTok | `TikTokApi` | `ms_token` cookie in .env | Stubbed, ready to enable |
| Xiaohongshu | `xhs` library | Browser cookie in .env | Stubbed, ready to enable |

## Key Decisions
- Graceful degradation: each platform collector is try/except wrapped, skips with log warning if credentials missing
- Dashboard shows "No data yet" cards for unconfigured platforms
- Single JSON output file for all platforms
- Must serve via `python3 -m http.server` (CORS prevents file:// access)

## How to Use
```bash
cd ~/A01/projects/social-dashboard
python3 collector.py                  # Fetch data
python3 -m http.server 8080           # Serve dashboard
# Open http://localhost:8080/dashboard.html
```

---
**Created**: 2026-02-27
**Status**: MVP complete (Reddit working, other platforms ready for credentials)
