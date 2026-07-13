# Runbook

## Normal operation
The GitHub Action runs `run --commit` 3×/day and commits the updated `state.json`.
Nothing to do day to day. To watch it: Actions tab → latest run → logs show
`CREATE` / `UPDATE` / skipped counts.

## One-time cutover
1. `python cubby_quo_sync.py check` → must PASS (env, Cubby auth + PII, Quo auth).
2. Wipe existing contacts in the **Quo app** (bulk delete).
3. `python cubby_quo_sync.py baseline` → review `baseline_preview.csv`.
4. `python cubby_quo_sync.py baseline --commit`.
5. Push the repo; set Secrets/Variables (below); confirm the first scheduled run.

## GitHub setup
- Repo **Secrets**: `CUBBY_API_KEY`, `QUO_API_KEY`.
- Repo **Variable**: `CUBBY_FACILITY_IDS` (e.g. `fac_aaa,fac_bbb`).
- The workflow needs `contents: write` (already set) to commit `state.json`.
- Adjust cron times in `.github/workflows/cubby-quo-sync.yml` (UTC).

## Manual run between scheduled runs
Actions tab → "Cubby to Quo sync" → "Run workflow" (workflow_dispatch is enabled).

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `check` shows phones blank | Cubby key lacks PII access | Get a PII-enabled key from Cubby |
| Quo calls return 401 | Wrong auth scheme | Set `QUO_AUTH_SCHEME="Bearer "` in `.env` / secret note |
| Baseline count looks capped (100/250/1000) | Cubby pagination | Add a paging loop to the search calls |
| Duplicate cards appear | Baseline was CSV-imported, or `state.json` lost | Re-baseline via API; keep `state.json` committed |
| A card didn't update after a move-out | Cursor not advancing / run failed | Check the last Action log; re-run `run --commit` |
| Card name has the wrong unit order | Ordering is by `moveInDate` | Expected; change `active_unit_names` if needed |
| `run` says "No cursor" | Baseline not committed yet | Run `baseline --commit` first |

## Recovery
- **Lost `state.json`:** re-run the wipe + `baseline --commit` to rebuild the map.
  (If you later store `customerId` in a Quo custom field, the map can be rebuilt
  without a wipe — ask to add that.)
- **Bad data pushed:** fix the source in Cubby; the next `run` recomputes and
  corrects the card automatically (idempotent).

## Turning down debug logging
Once a few runs look clean, set `SYNC_DEBUG=0` (env/secret) or remove the lines
tagged `# [debug]` in `cubby_quo_sync.py`.
