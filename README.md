# Automated Micro-Influencer Outreach System

EDXSO AI Engineer Intern - Assignment 1

A Python pipeline that discovers fitness micro-influencers on YouTube, filters and classifies them with explainable rules, enriches each profile with public contact details, writes a personalised email and Instagram DM with an LLM, and runs a sending layer with duplicate protection and an outreach tracker.

```
YouTube Data API  ->  Discovery  ->  Metrics  ->  Filter & classify  ->  Enrichment
                                                                              |
Outreach tracker  <-  Sending layer  <-  Review  <-  AI personalisation  <----+
```

Everything runs on free tiers. No paid service or credit card is needed.

## Test run results (10 November 2026)

| Stage | Result |
| --- | --- |
| Creators discovered | 174 |
| Passed all filters | 26 |
| Failed (reason recorded for each) | 148 |
| Shortlisted with a public email | 6 (the other 20 are marked `Not Found`) |
| Personalised messages generated | 26 emails + 26 Instagram DMs, all by LLM |
| Emails simulated (dry run) | 6 |
| Duplicate check on second run | 6 skipped as duplicates, 0 sent |
| Real test emails (redirected to my own inbox) | 3 |

No email was sent to any creator. All files are in `data/`.

## 1. Technology stack

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.10-3.13 | standard for data + API work |
| Discovery data | YouTube Data API v3 (official) | free, legal, real subscriber and engagement numbers |
| Storage | SQLite (`data/outreach.db`) | zero setup, resumable, unique index stops duplicate sends |
| LLM | Gemini API free tier, Groq free tier as fallback | no cost; provider is a config switch |
| Email | SMTP (Gmail app password) | free; dry-run mode needs no account at all |
| Config | `config.yaml` + `.env` | niche, thresholds and brand change without code edits |
| Tests | pytest | 10 tests covering the whole flow with a fake API |

Libraries: `requests`, `PyYAML`, `python-dotenv`, `pytest`. Everything else is the standard library.

## 2. Setup

```bash
git clone <https://github.com/Niyatiiii06/micro-influencer-outreach> && cd influencer-outreach
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Use Python 3.10 to 3.13. On very new Python versions some packages have no ready-made build yet and the install fails (on Windows: `py -3.13 -m venv .venv`).

Get the free keys and paste them into `.env`:

1. **YouTube** (required): [Google Cloud Console](https://console.cloud.google.com) -> create a project -> APIs & Services -> enable *YouTube Data API v3* -> Credentials -> Create API key.
2. **LLM** (one of): Gemini key from [AI Studio](https://aistudio.google.com/apikey), or Groq key from [console.groq.com/keys](https://console.groq.com/keys). No card needed for either.
3. **Email** (only for real sending): Gmail address + [App Password](https://myaccount.google.com/apppasswords), and your own address as `TEST_RECIPIENT`.

Then edit the `brand:` block in `config.yaml` (it ships with a demo brand).

## 3. Run

```bash
python -m pytest -q            # 1. prove the logic works (no keys needed)
python main.py run             # 2. discover -> metrics -> filter -> enrich -> personalise -> export
python main.py review          # 3. list messages; add --approve-all or --approve <channel_id>
python main.py send            # 4. dry run: writes .eml files to data/outbox, updates tracker
python main.py send            # 5. run again: every creator is skipped as a duplicate
python main.py send --mode test    # optional: real SMTP send, redirected to your own inbox
python main.py status
```

Every stage is also a separate command (`discover`, `metrics`, `filter`, `enrich`, `personalize`, `queue-dms`, `dm-sent`, `export`) and is safe to re-run; finished work is skipped.

### Output files (in `data/`)

| File | Assignment deliverable |
| --- | --- |
| `influencers_all.csv` | B. Dataset: every discovered creator with Status and the reason it passed or failed |
| `shortlisted_enriched.csv` | Enriched profiles (all mandatory + optional fields) |
| `messages.md`, `messages.csv` | C. Email pitch + Instagram DM per shortlisted creator, with word counts |
| `outreach_tracker.csv` | D. Influencer, Email, Message Generated, Sent, Date, Status |
| `dm_queue.csv` | Manual Instagram DM workflow |
| `outreach_log.csv` | Raw log of every send attempt |
| `outbox/*.eml` | Simulated emails (open in any mail client) |

## 4. How each stage works

### Discovery (`outreach/discovery.py`)

- Runs each query in `niche.search_queries` through `search.list` restricted to **videos uploaded in the last 90 days**, biased to region `IN`. Searching recent videos, rather than channels, surfaces small active creators instead of the famous channels that dominate channel search.
- Collects the unique channel IDs and loads them in batches of 50 through `channels.list`.
- Stops at `discovery.target_candidates` (150 by default, so well over the required 50).
- Quota: a search page costs 100 units, everything else 1 unit. A default run uses roughly 1,700-2,000 of the free 10,000 daily units.

### Metrics

For channels inside the follower band only (saves quota), the last 8 uploads are fetched.

```
engagement rate (%) = mean over recent videos of (likes + comments) / views x 100
```

Videos with hidden likes or zero views are skipped, not counted as zero. If no video qualifies, engagement is stored as empty and shown as "Not Available". This is a view-based rate; follower-based rates from other tools will differ.

### Filtering and classification (`outreach/filtering.py`)

Complete category implemented: **Fitness**. Seven checks, each recorded as `PASS`/`FAIL` with the actual numbers:

| Check | Rule (default) |
| --- | --- |
| Platform | in `allowed_platforms` |
| Followers | 5,000-100,000; hidden subscriber count fails |
| Activity | last upload within 60 days, at least 10 videos |
| Engagement | at least 1.5% |
| Content relevance | at least 50% of recent videos match the niche keyword taxonomy |
| Brand fit | no blocked keywords (steroids, betting...), not a made-for-kids channel |
| Geography | optional country allow-list; unknown country allowed by default |

Classification adds sub-niches (strength training, home workouts, yoga and mobility, calisthenics, running, nutrition), a size tier, and a 0-100 fit score (40% engagement, 40% relevance, 20% recency) used to order outreach.

### Enrichment (`outreach/enrichment.py`)

- **Email**: regex extraction from the channel description, then recent video descriptions, then the creator's own linked website (home, `/contact`, `/about`; robots.txt respected, honest User-Agent, 3 requests maximum). The source is stored next to the email.
- If nothing is found the value is `Not Found`. No address is ever guessed or built from a name. YouTube's captcha-protected "View email address" button is not touched.
- Instagram / TikTok / website links are taken from the same public text.
- Content themes = sub-niches + the creator's most repeated video tags. Content tone is inferred from title patterns.
- Audience age and gender are `Not Available`: they exist only in a creator's private analytics. Geography is reported as the channel's country when published.

### AI personalisation (`outreach/personalize.py`, `outreach/llm.py`)

- **Model**: Gemini Flash (free tier) by default. The client reads the model list for your key and uses the first entry in `llm.models` that is available, so a renamed or retired model does not break the run. Groq is used automatically if only a Groq key is present, or when `llm.provider` is set to `groq`.
- **In this test run**: Gemini wrote 14 messages before its free daily limit was reached; `llm.provider` was then switched to `groq`, which wrote the remaining 12. The model used for each message is recorded in the `Model` column of `data/messages.csv`.
- **Prompt**: the system prompt is `SYSTEM_PROMPT` in `personalize.py`. The user message is a JSON object holding only verified signals: creator name, themes, tone, last 3 video titles, follower count, engagement rate, country, the brand, and the collaboration offer.
- **Collaboration angle** is chosen by a transparent rule from size and engagement (UGC, affiliate, paid placement or barter), then passed to the model.
- **Guardrails**: the model is told to use only supplied facts. Every answer is checked in code: email 60-90 words, DM 15-30 words, creator named, no placeholders, no links in the DM. A failed check is sent back to the model with the reasons, up to 3 attempts.
- **Fallback**: with no key, or if the LLM keeps failing, an offline rule-based writer builds messages from the same signals. These are marked `offline_rule_based` and are not approved automatically.
- The signals used for each message are stored, so any sentence can be traced to real data.

### Review

Messages from the LLM that pass all checks are approved automatically; everything else waits. `python main.py review` lists, approves or rejects. Only approved messages are sent.

### Sending layer (`outreach/sender.py`)

| Mode | What happens |
| --- | --- |
| `dry_run` (default) | email is built and saved as `.eml`; logged as `simulated` |
| `test` | real SMTP delivery, but every email goes to `TEST_RECIPIENT` |
| `live` | real delivery to the creator; needs `--confirm-live` |

- Selects shortlisted creators with a syntactically valid email and an approved message.
- Duplicate protection twice: a check in code (same creator or same address), and a unique index in SQLite, so even a crashed or parallel run cannot send twice.
- Failures are logged as `failed` and retried on the next run. Daily cap and delay are configurable.
- **Instagram DMs are not automated.** Meta's API only allows replying to users who messaged the business first, so cold DMs would mean breaking platform rules. Each DM goes to `dm_queue.csv` with the profile link and text; a person sends it and runs `python main.py dm-sent <channel_id>`.

## 5. Error handling

- YouTube quota exhausted: the run stops cleanly, keeps what it has, and continues the next day.
- Network errors and HTTP 429/5xx: retried with exponential backoff (YouTube and LLM).
- Hidden subscribers, hidden likes, disabled comments, deleted playlists, missing country: stored as empty and reported, never filled in.
- One broken profile never stops a batch; it is logged and skipped.
- Missing keys produce a one-line instruction instead of a stack trace.

## 6. Scaling from 50 to 500+

- Raise `target_candidates` and add queries. 500 candidates costs about 1,500-2,000 search units, still inside the free daily quota; larger runs resume across days because progress is stored.
- All per-channel calls are batched (50 IDs per request).
- A new niche is a new keyword block in `config.yaml`. A new platform is one new file like `youtube.py` that returns the same record shape.
- SQLite can be swapped for Postgres by replacing `storage.py`; nothing else touches SQL connections.
- LLM calls are rate-limited to free-tier limits; with a paid key raise `requests_per_minute`.

## 7. Limitations

- YouTube only. Instagram and TikTok offer no free official discovery API, and scraping them breaks their terms.
- Many creators do not publish an email; expect a meaningful share of `Not Found`. Those creators still get a DM in the manual queue.
- Email validity is a syntax check only (no mailbox verification).
- Engagement is view-based and uses only the latest 8 uploads.
- Relevance and tone use keyword rules, so unusual phrasing can be misclassified. Thresholds are in `config.yaml`.
- Audience demographics are not publicly available and are reported as such.
- Free LLM tiers have daily limits, and model names change; check `llm.models` if generation fails.
- Cold email is regulated (for example CAN-SPAM, GDPR). Keep `dry_run`/`test` for the demo and only use `live` with a real brand, an opt-out line and a physical address.

## 8. Project layout

```
main.py                 CLI
config.yaml             niche, thresholds, brand, LLM, sending
outreach/
  youtube.py            API client with quota tracking and retries
  discovery.py          search, channel parsing, engagement metrics
  filtering.py          7 checks, sub-niche classification, fit score
  enrichment.py         email / links / themes / tone
  llm.py                Gemini + Groq clients, model auto-selection
  personalize.py        prompt, validation loop, offline fallback
  sender.py             dry-run / test / live email, DM queue, dedupe
  export.py             CSV + Markdown deliverables
  storage.py            SQLite tables and save/load helpers
tests/test_pipeline.py  10 tests (fake channels, fake YouTube functions)
```

## 9. Data integrity statement

Every record in `data/` comes from a live API response or from text the creator published. The synthetic channels in `tests/` are used only by the test suite and are never written to the dataset.#   m i c r o - i n f l u e n c e r - o u t r e a c h  
 