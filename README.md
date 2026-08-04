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

## Private Telegram companion

The optional Telegram companion is a private mobile control surface: it sends high-score roles
that survived the eligibility screen, can deliver that job's cover letter and tailored resume
straight into the chat, opens the existing private PWA for full document review, and lets you
mark a job applied or exclude it. It does **not** send API keys through Telegram and it does
**not** silently submit applications on external sites.

Eligibility is enforced by *excluding* roles that need work authorization you don't have — the
same screen `jobagent match` applies — rather than by requiring a posting to explicitly advertise
sponsorship. Most descriptions never say either way (Adzuna serves truncated snippets), so
demanding an explicit "we sponsor" tag would filter the queue down to almost nothing. Roles that
*do* advertise sponsorship or worldwide-remote are ranked first.

1. Create a bot with `@BotFather`, then add its token to the local `.env`:

   ```bash
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_DASHBOARD_URL=http://your-tailscale-ip:8842
   ```

2. Start the bot once and message it `/start`. It will reply with your private chat ID:

   ```bash
   jobagent telegram run
   # add the returned value to .env as TELEGRAM_ALLOWED_CHAT_ID=...
   ```

3. Install the two local launch agents after the chat ID is configured:

   ```bash
   cp scripts/com.sunilnjc.jobagent.telegram.plist ~/Library/LaunchAgents/
   cp scripts/com.sunilnjc.jobagent.telegram-notify.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.sunilnjc.jobagent.telegram.plist
   launchctl load ~/Library/LaunchAgents/com.sunilnjc.jobagent.telegram-notify.plist
   ```

The bot uses long polling, so it needs no public webhook or additional exposed port. It accepts
commands and buttons only from `TELEGRAM_ALLOWED_CHAT_ID`. Use `/today`, `/matches`, and `/status`
from Telegram; the daily notification runs at 07:20 after the existing morning preparation task.

Each job card carries **📄 Send documents**, which uploads that role's `cover_letter.pdf` and
`tailored_resume.pdf` into the chat — useful when you want to read them on the phone without the
dashboard. The button only appears once those files have actually been drafted.

After changing bot code, restart the running agent so it picks the change up:

```bash
launchctl kickstart -k gui/$(id -u)/com.sunilnjc.jobagent.telegram
```

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

### Keep the server running

For the phone to reach the board at any time, the API server has to be up — not just
while a terminal is open. A second launchd agent starts it at login and restarts it if
it ever crashes:

```bash
cp scripts/com.sunilnjc.jobagent.server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.sunilnjc.jobagent.server.plist
```

Logs go to `logs/server.log` / `logs/server.err.log`. Note it starts at *login*, so
after a reboot the Mac needs to be logged in (not just powered on) for the phone to
connect.

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

## Access from your phone (Tailscale)

To reach the board from your phone when you're *not* on the home WiFi, use
[Tailscale](https://tailscale.com) rather than port-forwarding your router.

**Why not port-forwarding / ngrok?** The API has no authentication (see the security
note above) — anything that can reach port 8842 can read your resume and drafted cover
letters, and can trigger runs that spend your OpenAI/Anthropic credits. Tailscale builds
a private WireGuard mesh between *your own* devices: the Mac and the phone get
`100.x.y.z` addresses that only exist inside your account's network, and nothing is
published to the public internet. Free tier covers personal use.

**One-time setup — Mac:**

```bash
brew install --cask tailscale     # installs Tailscale.app (needs your sudo password)
open -a Tailscale                 # then sign in from the menu-bar icon
```

Sign in with whatever identity provider you prefer (Google, GitHub, …) — just remember
which one, the phone has to match. Approve the system extension prompt if macOS asks.

**One-time setup — iPhone:** install **Tailscale** from the App Store, open it, and sign
in with the **same account** you used on the Mac. Both devices should now be listed in
each other's device list.

**Find the Mac's Tailscale IP:**

```bash
tailscale ip -4                   # e.g. 100.101.102.103
```

(If `tailscale` isn't on your `PATH`, use the full path
`/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4`, or just read it from the
menu-bar icon — the Mac's own address is shown at the top of the menu.)

**Then, on the phone:** start the backend on the Mac as usual
(`uvicorn jobagent.api.main:app --host 0.0.0.0 --port 8842` — `0.0.0.0` matters, it's
what makes the server listen on the Tailscale interface too) and open

```
http://100.101.102.103:8842
```

in Safari, substituting your own address. Port 8842 serves the built frontend as well as
the API, so that single URL is the whole app — no separate `npm run dev` needed, though
you do need `web/dist` to exist (`cd web && npm run build`).

**Add it to the home screen:** in Safari, tap **Share** → **Add to Home Screen**. It gets
an icon and launches full-screen without the browser chrome, like a native app.

**Caveat — the Mac must be awake.** Tailscale doesn't wake a sleeping machine; if the Mac
at home is asleep, the phone gets a connection error. Either leave it awake via
**Settings → Lock Screen** (never turn display off / prevent sleep) and **Settings →
Battery → Options → Prevent automatic sleeping on power adapter**, or keep it up only for
as long as you need:

```bash
caffeinate -s        # blocks sleep until you Ctrl-C
```

**On HTTPS:** Tailscale already encrypts the traffic at the network layer (WireGuard), but
the browser only sees `http://` and so treats the page as an insecure origin. That means
no service workers, and therefore no true offline PWA mode — Add to Home Screen still
works, it just needs the Mac reachable each time. If offline caching becomes worth it
later, `tailscale serve` can put a real, trusted HTTPS certificate in front of port 8842
on a `<machine>.<tailnet>.ts.net` hostname.
