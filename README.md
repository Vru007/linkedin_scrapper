# LinkedIn Profile API

A public HTTPS API that accepts a **LinkedIn profile URL** and returns that profile's
data as clean, structured JSON — **name, headline, location, about, experience,
education, skills, projects, courses, honors, certifications, languages, contact info,
and images**.

The whole thing is **purely reverse‑engineered**: no browser, no headless Chrome, no
Selenium/Playwright in the request path, no third‑party scraping product. It is a direct,
authenticated call to a LinkedIn endpoint, plus a parser that turns the response into
JSON. A tiny web UI is bundled so you can try it without touching a terminal.

---

## Table of contents

- [1. The problem statement](#1-the-problem-statement)
- [2. How this solution satisfies it](#2-how-this-solution-satisfies-it)
- [3. The journey — how we started and where we landed](#3-the-journey--how-we-started-and-where-we-landed)
- [4. Key decisions and trade‑offs](#4-key-decisions-and-trade-offs)
- [5. Architecture](#5-architecture)
- [6. What the code does (`app.py` walkthrough)](#6-what-the-code-does-apppy-walkthrough)
- [7. The frontend (`static/index.html`)](#7-the-frontend-staticindexhtml)
- [8. API reference](#8-api-reference)
- [9. Response schema](#9-response-schema)
- [10. Run it locally](#10-run-it-locally)
- [11. Deploy (Render free tier)](#11-deploy-render-free-tier)
- [12. Security notes](#12-security-notes)
- [13. Known limitations](#13-known-limitations)
- [14. Legal / ethical note](#14-legal--ethical-note)
- [15. Repository layout](#15-repository-layout)

---

## 1. The problem statement

Build and deploy a public HTTPS API that, given a LinkedIn profile URL, returns the
profile's data as structured JSON. The interesting part is **not** CRUD — it's the
reverse engineering. The role this was built for explicitly values:

> *"If you see a weird system, your first instinct should be to figure out how it works."*
> Reverse engineering / web‑security instinct, backend/platform skill, high agency, and
> owning a cloud deployment end‑to‑end.

Hard constraint added along the way: the solution must be **purely reverse‑engineered and
hit LinkedIn endpoints directly — no browser automation**. The response schema is ours to
design.

---

## 2. How this solution satisfies it

| Requirement | How it's met |
|---|---|
| Public HTTPS API | FastAPI app deployed on Render, served over HTTPS. |
| Input = LinkedIn profile URL | `GET /api/v1/profile?url=https://www.linkedin.com/in/<slug>/` |
| Output = structured JSON | A designed [`Profile` schema](#9-response-schema) with 15+ fields and nested sub‑objects. |
| Reverse engineering | We discovered that a **mobile User‑Agent** makes LinkedIn serve a fully server‑rendered "mwlite" page containing *all* profile data inline, and that a **TLS‑fingerprint** block (not a cookie block) was what stopped naive HTTP clients. |
| **No browser** | The request path is a single raw HTTP GET via `curl_cffi`. No Chromium, no JS execution, no automation framework. The "mobile browser" is just a User‑Agent *string*. |
| Cloud ownership | Env‑var secrets, health check, single‑command deploy, graceful cold‑start handling. |
| High agency / figuring out a weird system | The journey below is the whole point. |

---

## 3. The journey — how we started and where we landed

This is the honest story of how the solution evolved, because the reverse‑engineering path
*is* the deliverable.

### Step 0 — The naive attempt: official API
LinkedIn's official Profile/Marketing APIs require partner approval and only return the
authenticated user's *own* limited data. They cannot fetch an arbitrary profile. **Dead end**
for this challenge — rejected immediately.

### Step 1 — The obvious attempt: hit the desktop site with `requests`
Sending a normal `requests`/`httpx` GET to `linkedin.com/in/<slug>` with valid session
cookies got **blocked** (HTTP `999` / auth‑wall) — *even though the exact same cookies worked
in a browser and in command‑line `curl`*.

**Insight #1 — it's a TLS fingerprint block, not an auth block.** LinkedIn fingerprints the
TLS/JA3 handshake and HTTP/2 frame order *before* it ever looks at the cookie. Python's
default clients have a recognizable, non‑browser fingerprint, so they're dropped. Browsers
and `curl-impersonate` are not.
→ **Fix:** use [`curl_cffi`](https://github.com/lexiforest/curl_cffi) with
`impersonate="chrome"`, which replays Chrome's real TLS/JA3 + HTTP2 fingerprint. The block
disappears.

### Step 2 — What does the desktop page actually contain?
We captured the modern desktop profile page (see `capture_linkedin.py`, a Playwright CDP
capture tool used **only for recon**, never in production). Finding: the desktop site is now
a **Server‑Driven‑UI (SDUI) / React‑Server‑Components** app. The HTML is basically empty
placeholders; the real data arrives later via
`/flagship-web/rsc-action/actions/component?...` calls that return a messy RSC "flight"
payload, and those require browser‑generated tokens.
→ Parsing that without a browser is painful. **We needed a cleaner surface.**

### Step 3 — The breakthrough: the mobile (mwlite) surface
Sending a **mobile Android User‑Agent** to the same profile URL makes LinkedIn serve its
lightweight **`mwlite` (mobile‑web‑lite)** page instead. Confirmed by the response's
`<meta name="pageKey" content="p_mwlite_profile_view">`.

**Insight #2 — mwlite is fully server‑side rendered.** Every section (header, about,
experience, education, skills, projects, courses, honors, certifications, languages,
contact) is present **inline in the HTML** on the *single* main profile page. One
authenticated GET returns everything.

### Step 4 — "Only 2–3 experiences show up" bug
Early parsing returned a fraction of the experience list. We first assumed the rest lived on
separate detail pages (`/details/experience/`, `/details/skills/`, …) and built a tool to
fetch them all.

**Insight #3 — the detail pages are a dead end.** Every mwlite `/details/<section>` URL
returns HTTP 200 but a body that says *"This page doesn't exist"* + an app‑install upsell.
The *real* cause of the missing items: **all entries were already in the main page's HTML**,
just visually collapsed behind a `collapsed` CSS class ("See N more" is a CSS toggle, not a
network call). The parser simply wasn't reading the hidden nodes, and it only understood one
of LinkedIn's *two* experience layouts.
→ **Fix:** parse the full DOM (including collapsed nodes) and handle both layouts:
- **Single‑role** company → `a[data-tracking-control-name='profile-position']`
- **Grouped** company with multiple roles → `a[data-tracking-control-name='experience-timeline']`
  + nested `li.role-container` items, emitted as a nested `positions[]` array.

### Step 5 — The freelance / no‑logo edge case
Self‑employed or "Freelance" experience entries (and companies with no LinkedIn page) were
being skipped, because the parser keyed off the `<a>` company link — which those entries
don't have. LinkedIn renders them as a plain `<div>` instead.
→ **Fix:** iterate the `<li>` rows and key off the always‑present content container
(`div.flex-1.self-center`), treating the company `<a>` as *optional* (only used to fill
`companyUrl`). Also added `logo_url()` to null out LinkedIn's **ghost placeholder logos**
(served from `static.licdn.com`) so those entries return `companyLogoUrl: null` instead of a
meaningless asset URL.

### Step 6 — Frontend + token self‑service
LinkedIn session cookies expire. Rather than redeploy every time, the API accepts the session
tokens as **per‑request headers**, and a small bundled web UI stores them in the browser's
`localStorage` and attaches them on each call. When a session dies, you paste a fresh `li_at`
in **Settings** and keep going — no redeploy.

### Step 7 — Deploy
Deployed on **Render's free tier** (a real Linux container), because `curl_cffi` ships a
native compiled binary that is unreliable on Vercel/Lambda‑style Python runtimes. Render runs
it with zero fuss.

**Where we are now:** a working, deployed, browser‑free API + UI that returns complete
structured profile JSON, with graceful handling of expired sessions and tricky profile
layouts.

> There is a known *even‑purer* future upgrade — calling LinkedIn's own internal JSON API
> (`/mwlite/profile/api/non-self/runQuery`, discovered via the page's
> `<meta name="queryEndpoint">`) to skip HTML parsing entirely. It's documented as a next
> step but intentionally **not** shipped, because the current HTML‑parsing path is already
> browser‑free, complete, and robust. See [Known limitations](#13-known-limitations).

---

## 4. Key decisions and trade‑offs

| Decision | Why | Alternatives rejected |
|---|---|---|
| **Reuse own session cookie** (`li_at` + `JSESSIONID`) | The challenge allows using your own credentials; simplest, stable, no login automation, no 2FA/captcha loops. | Automating email/password login (fragile, needs a browser, triggers checkpoints); OAuth (can't fetch arbitrary profiles). |
| **`curl_cffi` with Chrome impersonation** | The block is a TLS/JA3 fingerprint, not a cookie check. Only a client that mimics a browser's TLS passes. | `requests`/`httpx` (blocked); Node `undici` (also non‑browser TLS, weaker impersonation options → pushed the stack to Python). |
| **mwlite mobile surface** | Fully server‑rendered → all data inline in one HTML response. No SDUI/RSC, no JS, no detail pages. | Desktop SDUI/RSC (empty placeholders, needs a browser); official API (no arbitrary profiles). |
| **HTML parsing (BeautifulSoup + lxml)** | Data is delivered as HTML on this surface; parsing a response is normal API consumption (HTML vs JSON is just a format). | The internal `runQuery` JSON API — cleaner but adds CSRF/query‑id reverse engineering; kept as a future step. |
| **Python + FastAPI** | Best‑in‑class parsing (BeautifulSoup) + the only strong TLS‑impersonation client (`curl_cffi`) are both Python‑native. | Node/TS (weaker TLS impersonation for this specific block). |
| **Per‑request token headers + `localStorage` UI** | Sessions expire; lets a user refresh credentials without a redeploy. | Hardcoding cookies (needs redeploy on every expiry). |
| **Render free tier** | Real Linux container runs the `curl_cffi` native binary reliably; free managed HTTPS. | Vercel/Lambda (native binary risk). |
| **API‑key guard + SSRF URL allow‑list** | The endpoint is otherwise an open scraping/SSRF proxy on your LinkedIn session. | No auth (abuse, session‑ban risk). |

Trade‑offs we consciously accepted:
- **Cookies expire** (weeks–months) and can hit checkpoints → we detect it and return a clear
  "session expired — update your token" error instead of failing silently.
- **HTML selectors can change** if LinkedIn restructures mwlite → parser is defensive
  (optional fields, graceful nulls) and each section is isolated so one break doesn't crash
  the rest.
- **No browser fallback** by design → if LinkedIn ever hard‑blocks raw HTTP, we surface a
  clean error rather than smuggling in a headless browser.

---

## 5. Architecture

```
                 ┌──────────────────────────────┐
   Browser  ───▶ │  static/index.html (bundled)  │
   (UI)          │  • URL box + Settings (tokens) │
                 │  • stores li_at/JSESSIONID in   │
                 │    localStorage                 │
                 └───────────────┬────────────────┘
                                 │ fetch  /api/v1/profile?url=...
                                 │ headers: x-li-at, x-jsessionid, x-api-key
                                 ▼
                 ┌──────────────────────────────┐
                 │        FastAPI (app.py)        │
                 │  1. validate URL (SSRF guard)  │
                 │  2. pick tokens (headers|.env) │
                 │  3. fetch mwlite HTML           │───────┐
                 │  4. parse → Profile JSON        │       │ curl_cffi GET
                 │  5. error mapping               │       │ mobile UA + cookies
                 └───────────────┬────────────────┘       │ impersonate="chrome"
                                 │                          ▼
                                 │            https://www.linkedin.com/in/<slug>/
                                 ▼                  (serves mwlite SSR HTML)
                        structured JSON
```

Everything is same‑origin: FastAPI serves the UI at `/` and the API at `/api/v1/profile`, so
there's no CORS to configure.

---

## 6. What the code does (`app.py` walkthrough)

`app.py` is the entire backend. It has five concerns:

### a. Config & constants
- Loads `LINKEDIN_LI_AT`, `LINKEDIN_JSESSIONID`, `API_KEY` from environment (`.env` locally,
  Render env vars in prod).
- `MOBILE_UA` — the Android Pixel Chrome User‑Agent string that triggers the mwlite surface.
  **This string is the whole "mobile browser" — there is no actual browser.**

### b. Schema (Pydantic models)
Defines the exact JSON shape and validates every response:
- `Position` — one role: `title`, `dateRange`, `duration`, `location`, `description`.
- `Experience` — a company: `company`, `companyUrl`, `companyLogoUrl`, `totalDuration`, and a
  nested `positions[]` (so a company with multiple roles groups them correctly).
- `Education`, `Honor` (used for both honors and certifications), `ContactItem`.
- `Profile` — the top‑level object tying all sections together plus a `retrievedAt` timestamp.

### c. Helpers
- `clean()` — collapses whitespace, returns `None` for empties.
- `extract_slug()` — pulls the `<slug>` out of any LinkedIn profile URL (also accepts
  `/mwlite/profile/in/...`).
- `is_linkedin_url()` — **SSRF guard**: only `*.linkedin.com` URLs are allowed.
- `img_url()` — LinkedIn lazy‑loads images, so the real URL is in `data-delayed-url`, not
  `src`. This reads the right attribute.
- `logo_url()` — returns `None` for **ghost placeholder** logos (`static.licdn.com`) so
  logoless companies don't get a junk URL.
- `parse_date_range()` — turns an mwlite date container into `(dateRange, duration)`.

### d. The fetcher — `fetch_mwlite_html(slug, li_at, jsessionid)`
The reverse‑engineering core. Issues a single GET to
`https://www.linkedin.com/in/<slug>/` with:
- the **mobile UA** (→ mwlite SSR page),
- the session **cookies** (`li_at`, `JSESSIONID`),
- `impersonate="chrome"` (→ defeats the TLS‑fingerprint block),
- redirects followed so an expired session lands on the auth‑wall URL we can detect.

### e. The parsers — one function per section
`parse_header`, `parse_about`, `parse_experience` (handles **grouped**, **single**, and
**freelance/no‑link** layouts), `parse_education`, `parse_skills`, `parse_projects`,
`parse_courses`, `parse_languages`, `_honor_like` (honors + certifications), `parse_contact`.
`parse_profile()` orchestrates them and builds the final `Profile`. Each section is
independent, so a missing/renamed section degrades to `null`/`[]` instead of failing the
whole request.

### f. Routes
- `GET /healthz` → `{"status": "ok"}` — liveness probe for Render.
- `GET /` → serves the bundled `static/index.html` UI.
- `GET /api/v1/profile` → the main endpoint. Order of operations:
  1. **Token selection** — if the caller sends `x-li-at` + `x-jsessionid` headers, use those;
     otherwise fall back to the server's `.env` tokens (and, in that case, enforce the
     `x-api-key` guard).
  2. **Validate** — SSRF check + slug extraction (`400` on bad input); require a session
     (`400` if none).
  3. **Fetch** the mwlite HTML.
  4. **Error mapping** — auth‑wall / `401` / `403` / `999` / `login` redirect → `502`
     "session expired — update your token"; any other non‑200 → `502`.
  5. **Parse** → `Profile`. If no `name` was found (bad slug / private) → `404`.
  6. Return validated JSON.

---

## 7. The frontend (`static/index.html`)

A single, dependency‑free HTML file (dark theme, vanilla JS) served at `/`. It exists so the
API is testable without curl/Postman and so sessions can be refreshed without a redeploy.

- **URL box + Fetch** — calls `GET /api/v1/profile?url=...` on the same origin.
- **Settings (tokens)** — paste `li_at`, `JSESSIONID`, and optional API key; stored in the
  browser's `localStorage` and sent as `x-li-at` / `x-jsessionid` / `x-api-key` headers on
  every request. Nothing is sent anywhere except your own API.
- **Results** — a rendered **Profile** view (avatar, experience timeline with nested roles,
  education, skills/courses/languages as chips, projects, honors, contact) and a **Raw JSON**
  tab, plus an **Export JSON** button.
- **UX** — loading spinner and clear error banners (e.g. the "session expired" case tells you
  to update your token).

Because the UI and API share an origin, there is **no CORS** setup.

---

## 8. API reference

### `GET /api/v1/profile`

**Query params**

| Param | Required | Description |
|---|---|---|
| `url` | yes | A LinkedIn profile URL, e.g. `https://www.linkedin.com/in/<slug>/` |

**Headers**

| Header | Required | Description |
|---|---|---|
| `x-li-at` | conditional | Your LinkedIn `li_at` cookie. If provided with `x-jsessionid`, overrides server tokens. |
| `x-jsessionid` | conditional | Your LinkedIn `JSESSIONID` cookie (e.g. `ajax:123…`). |
| `x-api-key` | conditional | Required only when relying on the server's `.env` tokens and an `API_KEY` is configured. |

**Example**

```bash
curl "https://<your-app>.onrender.com/api/v1/profile?url=https://www.linkedin.com/in/some-slug/" \
  -H "x-li-at: <YOUR_LI_AT>" \
  -H "x-jsessionid: ajax:1234567890"
```

**Status codes**

| Code | Meaning |
|---|---|
| `200` | Profile returned. |
| `400` | Missing/invalid URL, non‑LinkedIn host, or no session provided. |
| `401` | Bad/missing API key (when using server tokens). |
| `404` | Profile not found / private / no parseable data. |
| `502` | LinkedIn session expired or blocked (refresh your `li_at`), or upstream error. |

### `GET /healthz`
Returns `{"status":"ok"}`.

### `GET /`
Serves the web UI.

---

## 9. Response schema

```jsonc
{
  "profileUrl": "https://www.linkedin.com/in/<slug>",
  "vanityName": "<slug>",
  "memberUrn": "urn:li:member:...",
  "name": "Jane Doe",
  "headline": "Software Engineer",
  "location": "Bengaluru, Karnataka, India",
  "connections": "500+ connections",
  "currentCompany": "Acme",
  "about": "…",
  "profileImageUrl": "https://media.licdn.com/…",
  "backgroundImageUrl": "https://media.licdn.com/…",   // null if no custom cover
  "experience": [
    {
      "company": "Acme",
      "companyUrl": "https://www.linkedin.com/company/acme",  // null for freelance/no page
      "companyLogoUrl": "https://media.licdn.com/…",          // null for ghost placeholder
      "totalDuration": "2 yrs 3 mos",                          // set for grouped companies
      "positions": [
        {
          "title": "Senior Engineer",
          "dateRange": "Jan 2024 - Present",
          "duration": "1 yr 8 mos",
          "location": "Remote",
          "description": "…"
        }
      ]
    }
  ],
  "education":    [ { "school": "…", "schoolUrl": "…", "degree": "…", "dateRange": "…", "logoUrl": "…" } ],
  "skills":       [ "Python", "FastAPI" ],
  "projects":     [ { "name": "…", "detail": "…" } ],
  "courses":      [ "Distributed Systems" ],
  "honors":       [ { "name": "…", "issuer": "…", "date": "…" } ],
  "languages":    [ "English", "Hindi" ],
  "certifications":[ { "name": "…", "issuer": "…", "date": "…" } ],
  "contact":      [ { "type": "Email", "value": "…" } ],
  "retrievedAt":  "2026-08-30T12:34:56.789+00:00"
}
```

Any absent section degrades gracefully to `null` (scalars) or `[]` (lists).

---

## 10. Run it locally

**Prereqs:** Python 3.11+.

```powershell
# 1. install deps
pip install -r requirements.txt

# 2. create a .env file next to app.py
#    (get li_at + JSESSIONID from your browser's LinkedIn cookies)
```

`.env`:

```dotenv
LINKEDIN_LI_AT=<your full li_at cookie ~150+ chars, one unbroken line, no quotes>
LINKEDIN_JSESSIONID=ajax:1234567890
API_KEY=<any secret you choose>   # optional

FOR NOW WHILE RUNNING IT FROM LIVE URL PROVIDE "hello" as API_KEY
```

```powershell
# 3. run
uvicorn app:app --reload

# 4. open the UI / docs
#    http://127.0.0.1:8000/          (web UI)
#    http://127.0.0.1:8000/docs      (Swagger)
#    http://127.0.0.1:8000/healthz   (health check)
```

> **Common gotcha:** the `li_at` cookie is ~150+ characters. If it gets truncated by editor
> line‑wrapping on paste, LinkedIn returns `999`/auth‑wall and you'll get a `502`. Keep it on
> one line.

---

## 11. Deploy (Render free tier)

1. Push the repo to GitHub (`static/`, `app.py`, `requirements.txt`, `.gitignore`).
2. On Render → **New → Web Service** → connect the repo.
3. Configure:
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. **Environment variables:** add `LINKEDIN_LI_AT`, `LINKEDIN_JSESSIONID`, and optionally
   `API_KEY`. **Do not** add `PORT` — Render injects it automatically; your start command
   just reads `$PORT`.
5. Deploy, then verify `https://<app>.onrender.com/healthz` and `/`.

> Free tier sleeps after ~15 min idle; the first request after a sleep has a ~30–50s cold
> start. `curl_cffi`'s native binary runs fine on Render's standard Linux image.

---

## 12. Security notes

- **Secrets never in the repo.** `li_at`, `JSESSIONID`, and `API_KEY` come only from env
  vars; `.env` is git‑ignored.
- **SSRF guard.** `is_linkedin_url()` rejects any host that isn't `*.linkedin.com`, so the
  endpoint can't be abused to fetch arbitrary internal/external URLs (OWASP A10).
- **API‑key gate.** When using the server's own session, a caller must present `x-api-key`,
  so the deployment isn't an open scraping proxy burning your LinkedIn session.
- **No secret logging.** Cookies are only ever sent to LinkedIn, never logged.
- **User‑supplied tokens stay client‑side.** The UI keeps tokens in `localStorage` and sends
  them straight to your own API over HTTPS.

---

## 14. Legal / ethical note

This project is a technical demonstration of reverse engineering for a hiring challenge. It
uses the operator's own authenticated session to read profile pages that the operator is
already permitted to view, and scraping LinkedIn may be contrary to LinkedIn's Terms of
Service. Use it responsibly, only against data you're authorized to access, and do not use it
for bulk harvesting or any purpose that violates LinkedIn's terms or applicable law.

---

## 15. Repository layout

```
.
├── debug_fetch.py    # its a debugging script made during development (pushed here for reference work)
├── app.py            # FastAPI backend: fetcher + parsers + routes (the whole API)
├── static/
│   └── index.html      # bundled web UI (token settings, rendered profile, raw JSON, export)
├── requirements.txt    # fastapi, uvicorn[standard], curl_cffi, beautifulsoup4, lxml, pydantic, python-dotenv
├── .gitignore          # keeps .env / secrets and caches out of git
└── README.md           # this file
```
## 16. HOW TO GET li_at and jsessionID from inspect

 <img width="1078" height="112" alt="image" src="https://github.com/user-attachments/assets/7e39bf07-8eec-4c51-935c-ee451ca55344" />

 

 navigate to cookies section of this api call you will get your li_at (Authentication_token) and JSESSIONID.

 ## DONT FORGOT TO PUT API_KEY AS <hello> after filling all the values click save tokens and use the service
```
```
