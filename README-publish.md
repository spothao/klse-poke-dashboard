# How to rebuild and publish the Poke dashboard

`gh` is not authenticated on this box, and `git push` will fail until it is. A scheduled or later run should rebuild locally, then publish with the **user-GitHub-xai** MCP tools (not `gh` or `git push`).

Repo: `spothao/klse-poke-dashboard`  
Branch: `main`  
Live site: https://spothao.github.io/klse-poke-dashboard/

## 1. Rebuild

```bash
cd /workspace/poke-dashboard
python3 build.py
```

This reads:

- `/workspace/papertrade/ledger.json` (MoneyLens)
- `/workspace/papertrade-screener/ledger.json` (Screener)
- `/workspace/poke-dashboard/comparison.md` (optional weekly summary)

and writes the HTML pages: `index.html`, `compare.html`, `compare-2.html` (if needed), `moneylens.html`, `screener.html`, `watchlist.html`, `watchlist-2.html` (and any further splits).

If `comparison.md` is missing, the weekly-summary page shows “Weekly summary arrives Fri after close.” To refresh the Friday write-up, edit `comparison.md` first (date in a `# …` heading, then plain paragraphs), then rerun `build.py`.

## 2. Confirm no overflow

```bash
cd /workspace/poke-dashboard
node measure_viewports.mjs index.html compare.html compare-2.html moneylens.html screener.html watchlist.html watchlist-2.html
```

Omit a filename if that page was not generated. The script must print `FIT CHECK PASSED` for 360×480 and 400×530 (it also checks 1072×1448). If it fails, tighten the layout or split a table, rebuild, and measure again. Do not publish an overflowing page.

## 3. Push with the GitHub MCP

Use the `user-GitHub-xai` MCP tool `push_files` (call `GetDynamicTools` for that namespace/tool first if the schema is not already loaded).

Each call needs:

- `owner`: `spothao`
- `repo`: `klse-poke-dashboard`
- `branch`: `main`
- `message`: a short commit message
- `files`: an array of `{ "path": "…", "content": "…" }`

Rules:

- A content value may be `$file:/absolute/path` (the whole value, no surrounding text). **At most four `$file:` arguments are allowed in one call.** If more than four files changed, make further `push_files` calls (each call is its own commit). Smaller files can be inlined as literal `content` to stay within the limit.
- Push every changed HTML page. Also push `build.py`, `comparison.md`, `README.md`, or `README-publish.md` if those changed.
- Do **not** push credentials, ledger files, `preview-*.png`, `fit-report.json`, or `__pycache__/`.

Example (first batch of pages):

- `push_files` with `index.html`, `compare.html`, `compare-2.html`, `moneylens.html` as `$file:/workspace/poke-dashboard/…`
- Then another `push_files` for `screener.html`, `watchlist.html`, `watchlist-2.html`, and any updated `comparison.md` or `build.py`

Set `mcpDetails.description` to a short plain-language sentence about the push.

## 4. Wait for GitHub Pages

The workflow `.github/workflows/pages.yml` deploys every `*.html` file from `main`. After the last push:

1. Wait about 60–90 seconds.
2. Call `actions_list` on `user-GitHub-xai` with `method: list_workflow_runs`, `owner: spothao`, `repo: klse-poke-dashboard`.
3. Confirm the latest **Deploy GitHub Pages** run for the new commit SHA has `status: completed` and `conclusion: success`.
4. If it failed, call `actions_list` with `method: list_workflow_jobs` and `resource_id` set to the run id, then `get_job_logs` with `return_content: true` for the failed job id. Fix and push again.

## 5. Verify the live URLs

Each of these must return HTTP 200 and the dashboard HTML (`text/html`, a “Last updated” line, Prev/Home/Next):

- https://spothao.github.io/klse-poke-dashboard/
- https://spothao.github.io/klse-poke-dashboard/compare.html
- https://spothao.github.io/klse-poke-dashboard/compare-2.html
- https://spothao.github.io/klse-poke-dashboard/moneylens.html
- https://spothao.github.io/klse-poke-dashboard/screener.html
- https://spothao.github.io/klse-poke-dashboard/watchlist.html
- https://spothao.github.io/klse-poke-dashboard/watchlist-2.html

Skip a URL only if that page was not generated. After a new deploy, allow a short cache delay and retry a 404 once.

## Local helper

`./publish.sh` also rebuilds and, if this directory is a git repo, commits changed `*.html`. It will **not** finish a publish until `gh` or git credentials exist. Follow sections 3–5 above instead.
