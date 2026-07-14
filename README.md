# Job Search Agent

A personal job-search pipeline: fetch listings, rank them against your resume, draft
tailored application materials, and track application status.

## How it works

1. **Fetch** — pulls postings from Adzuna, RemoteOK, WeWorkRemotely, and configured
   Greenhouse/Lever company boards into a local SQLite database.
2. **Match** — screens each posting through a title keyword filter and a
   work-eligibility classifier (skips roles restricted to countries where you lack
   work authorization), then scores survivors with a local embedding similarity
   prefilter and an LLM fit rating 1-10. Set `RANK_PROVIDER=openai` in `.env` to
   rate with `gpt-4o-mini` (much better judgment than a small local model, costs
   pennies) or leave as `ollama` for fully free local rating.
3. **Review** — lists your top-ranked unreviewed jobs in the terminal.
4. **Draft** — for a job you pick, calls the Claude API to generate a tailored cover
   letter and resume bullet suggestions, written to `output/<company>-<title>/`.
5. **Status** — tracks each job through new -> matched -> drafted -> applied ->
   interviewing -> rejected/offer.

This tool never auto-submits applications anywhere — drafts are for you to review and
submit yourself.

## LinkedIn / Indeed

These sites disallow bulk scraping in their Terms of Service. Instead of scraping search
results, this tool supports `jobagent fetch --url <job-url>`: paste a single job posting
URL you found manually and it will be fetched and parsed just like any other source.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# fill in ANTHROPIC_API_KEY (required for `draft`)
# fill in ADZUNA_APP_ID / ADZUNA_APP_KEY (optional, only for the Adzuna source)

# drop your resume (PDF or DOCX) into resumes/
cp ~/path/to/resume.pdf resumes/

# edit config/preferences.yaml for target titles, countries, ATS boards to watch
```

Local models used for matching (via [Ollama](https://ollama.com)):

```bash
ollama pull llama3.2
ollama pull nomic-embed-text
```

## Usage

```bash
jobagent fetch                      # pull from all configured sources
jobagent fetch --url <job-url>      # add a single manually-found posting (LinkedIn/Indeed)
jobagent match [--limit N]          # score fetched jobs against your resume
jobagent prepare [--top N]          # fetch + match + draft the top N new matches (default 3)
jobagent review                     # list top-ranked unreviewed jobs
jobagent gaps <job_id>              # missing requirements/keywords vs a posting, before you apply
jobagent draft <job_id>             # generate cover letter + tailored resume bullets
jobagent status                     # list jobs by pipeline stage
jobagent status <job_id> <stage>    # update a job's pipeline stage
```

## Daily automation (macOS)

`jobagent prepare` runs the whole pipeline hands-off: it fetches new jobs, matches
them, and drafts full application materials (cover letter + tailored resume PDF + gap
analysis) for the top N new matches — leaving a ready-to-review queue in the "drafted"
column. **It never submits anything; you review each and submit yourself.**

To run it automatically every morning, install the launchd schedule (runs at 07:00 daily,
and at next wake if the Mac was asleep):

```bash
cp scripts/com.sunilnjc.jobagent.prepare.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.sunilnjc.jobagent.prepare.plist
```

Output is logged to `logs/prepare.log`. Requires the Ollama app running (used for
embeddings) — it starts at login, so being logged in is enough. To stop:
`launchctl unload ~/Library/LaunchAgents/com.sunilnjc.jobagent.prepare.plist`.

## Web UI

A kanban board (columns = pipeline stages) for reviewing matches, triggering
fetch/match runs, and generating drafts/gap-analyses without touching the CLI. It's a
thin FastAPI layer over the same `jobagent` package (no logic duplication) plus a
React + TypeScript frontend.

**Backend:**

```bash
pip install -e ".[web]"
uvicorn jobagent.api.main:app --host 0.0.0.0 --port 8842
```

**Frontend:**

```bash
cd web
npm install
npm run dev -- --host
```

Open the printed `http://localhost:5173` URL, or the printed LAN address (e.g.
`http://192.168.1.x:5173`) from your phone on the same WiFi. The frontend talks to the
API at `<same-host>:8842`, computed automatically so it works from either address.

Port 8842 was chosen because it's uncommon — a more typical port (8420, 8000, 3000...)
is likely to collide with some other local dev server on a shared machine, and since
`localhost` often resolves to IPv6 first, such a collision silently routes your requests
to the *other* server instead of failing loudly. If you change the port, update it in
both the `uvicorn` command above and `web/src/api/client.ts`.

**Security note:** there is no authentication. This is fine on a trusted home network
(the intended use), but do not expose this port to the public internet.
