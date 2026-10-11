---
name: b-roll-finder
description: Source b-roll for a talking-head edit made with Roughcut and finished in Final Cut Pro — classify each moment, source free-licence visuals first (Wikimedia Commons, official page captures, Hyperframes graphics), show a contact sheet with licence badges, then place the picks as connected clips in a new FCPXML, each landing on its word. Use when the user says "find b-roll", "/find-broll", "/b-roll-finder", "cutaways for this footage", or wants footage, screenshots or graphics over a talking head.
---

# B-Roll Finder — skill methodology

> Methodology for sourcing and placing b-roll on talking-head video, from
> [louisedesadeleer/b-roll-finder](https://github.com/louisedesadeleer/b-roll-finder) (MIT, see LICENSE; upstream
> commit 36b8a56), adapted to Roughcut. What changed from upstream:
> - **Transcript:** Roughcut's word-level transcript (whisper.cpp), never mlx-whisper.
> - **Output:** the picks become **connected clips above the main storyline in a new Roughcut FCPXML**, landing on the
>   word (`scripts/place_broll.py`). Nothing is burned into an MP4. The edit is finished in Final Cut Pro.
> - **Sources:** free licences first (Wikimedia Commons, captures of official pages, graphics made here). Any YouTube
>   excerpt from someone else's channel carries a red **CLAIM RISK** badge on the contact sheet.
> - **Contact sheet before ANY placement**, every run (stricter than upstream's lean default).
> - **Concept graphics:** Hyperframes (HTML + GSAP, rendered locally), not Remotion.
>
> Scripts are run with `scripts/run NAME.py …` (the skill's own Python, set up by `setup.sh`). Paths below are
> relative to this skill's folder (`modules/b-roll-finder/` in fcpxml-roughcut, or wherever it was linked).

## ⛔ USER OVERRIDES ARE LAW — and they persist

When the user states a preference or ban mid-run ("no memes", "no text cards", "nothing political"), it applies IMMEDIATELY and for the rest of the session, AND gets written into the profile's Guardrails right then — not at the end. A banned category is never sourced again, never proposed again, never "just one candidate to check". Violating a stated ban is the worst failure this skill can make: it tells the user the agent doesn't listen.

## ⛔ STEP 0 — ONBOARDING GATE (run this check before ANYTHING else, every session)

Open the profile ([TASTE.md](TASTE.md)). **The ONLY thing that skips onboarding is a literal `Confirmed-by: <name> (<date>)` line at the top of the profile.** No line → ask the onboarding questions (below) out loud, write the answers into the profile, add the line. This is NOT optional and NOT inferable:

- Do **NOT** infer the answers from CLAUDE.md, memory, prior conversations, or this profile's prose — even if you are confident you know them. Asking IS the feature.
- [TASTE.example.md](TASTE.example.md) is upstream's EXAMPLE profile (Louise's). It is a reference, never the user's answers.
- Answers already in TASTE.md are not re-asked; everything still marked "_to ask_" is.
- "redo my profile" re-triggers onboarding at any time.

## The one rule that governs everything

**The agent NEVER picks the final b-roll. The user does.** The right clip is often a taste call. This skill's job is to **narrow the funnel** — classify each moment, scope the search to free and authoritative sources, score candidates, and hand back a tight contact sheet. The user makes the final pick. **Nothing is placed in an FCPXML before the user has answered the contact sheet.**

## The taste profile — load it before sourcing anything

Curation is half the skill. Every run starts by loading **[TASTE.md](TASTE.md)**: the b-roll fingerprint (which types, how fast to cut), the trusted sources tagged by topic, the guardrails and the brand tokens for graphics. The profile starts empty; it fills up from onboarding and from what the user says during runs. TASTE.md's "Make it yours" section (copied from upstream in TASTE.example.md) covers revealing a fingerprint from published videos.

## Onboarding preferences (ask once, store in the profile)

Ask on first run and write the answers into TASTE.md:

1. **Audio** — talk over b-roll (prepared clips are silent: `prepare_clip.py` default) or keep the clip's sound (`--keep-audio`; Roughcut lays it at -96 dB with the Effects role, so it can be raised in Final Cut Pro)?
2. **Stills motion** — static images get a **very subtle zoom-in by default** (~1.5%/sec, centred, capped ~2%/sec, rendered sub-pixel); offer opt-out to fully static (`--static`), or to leave stills static and add Final Cut Pro's own Ken Burns by hand.
3. **On-screen source credit** — credit each b-roll bottom-right? `off` · `white` · `black` · `auto` (contrast-picked). Short canonical name ("Wikimedia Commons", "economie.gouv.fr"), never a URL. Whatever the answer, **CC BY and CC BY-SA require a credit somewhere**: `place_broll.py` always writes `CREDITS.txt` for the video description.
4. **Concept motion graphics (Hyperframes)** — generate graphics for concept/stat/chart beats? If yes: brand tokens (background, text, accent colours, font) for TASTE.md. Rendering is local (`scripts/hf.sh render`); never `hyperframes cloud` or `publish` (they upload).
5. **AI-generated b-roll** — off by default; opt-in only for beats with no real footage, via a quality model the user names. Never silently substitute cheap AI stock.
6. **Own YouTube channel(s)** — the channel name(s) whose excerpts are the user's own (no claim risk). Everything else on YouTube is flagged.

## 🔓 Standing authorization — invoking the skill IS the permission

Do NOT ask conversational permission for access or searches while sourcing — running the skill already authorizes it to act on the user's behalf. Never ask "want me to search/look up/access X?" for:

- Web searches, news/headline lookups, oEmbed/embed endpoints, the Wikimedia Commons API
- `yt-dlp` metadata searches and downloads of public videos (as **candidates**: they are flagged, see Sources & rights)
- Headless-browser captures of public pages (incl. clicking through cookie/consent walls)
- The user's logged-in browser or browser cookies **for sourcing/reading** (profiles, posts, walled pages)
- Reading local files, transcripts, Roughcut project folders, the user's asset library
- Rendering Hyperframes graphics locally

Just do it and show the result. The user's taste checkpoints stay: the **plan approval** before sourcing and the **contact-sheet pick** before placing. The ONLY things that still warrant a question: publishing/posting anything outward (incl. Hyperframes `publish`/`cloud`), paid actions, deleting user files, and installing heavy dependencies. Ask-permission friction everywhere else is a bug, not politeness.

## ⚡ LEAN BY DEFAULT — the funnel is for taste calls, not everything

The skill earned trust through restraint; heavyweight process makes output WORSE, not better (upstream, tested 2026-06-11: a full-funnel run took ~10× longer and shipped worse composition than the lean run). Defaults:

- **Plan approval → 1 to 3 candidates per beat → contact sheet → pick → place.** The user always sees the contact sheet before anything is placed. Keep it tight: one clearly-right free candidate beats three mediocre ones; add a second or third only when they are genuinely different options (a capture vs a graphic, a still vs a clip). Meme/cultural beats are never auto-sourced (see routing).
- **Research depth is bounded at plan time.** Verify referents enough to source accurately; do NOT exhaust every sourcing route before the plan. The escalation ladder fires only AFTER the user has agreed a beat and the easy routes failed — never speculatively.
- **Per-beat time box:** if a single beat's sourcing exceeds ~5 minutes, put the best-available candidate on the sheet or drop the beat and flag it. One stubborn artifact must not stall the edit.
- **One verify pass + targeted fixes** beats endless polishing.
- **NO RETRIES — first failure switches, second failure drops.** A method that fails once is NOT retried (not with tweaked flags, not "one more time") — switch to a different method immediately. If the second method also fails, DROP the beat, flag it, move on. A beat is never worth a third attempt. This applies to everything: captures, downloads, renders, browser actions.
- **Never auto-install heavy dependencies mid-run** (conda, aligners, a Whisper, etc.) — use the fallback, note it, offer the install after delivery.

### Source by SOURCE, not by beat (the big sourcing speed-up)
After the plan is approved, **cluster beats by where their asset lives, then fetch each source ONCE**:
- One full-page capture of the subject's website (`CDP_FULLPAGE=1`) often covers 4–6 beats — crop per beat from the same capture.
- One Commons category or one official film often covers several beats — fetch once, cut multiple windows.
- Run all downloads as ONE batched background job (metadata-only search first, download only the chosen items); run captures as a second batch in parallel. Never fetch serially beat-by-beat.
- Cache raw downloads in the project's `b-roll/raw/` so re-renders and v2s never re-fetch.

## Accuracy over volume

Fewer, perfectly-accurate beats beat lots of mediocre ones. The habit to kill is keyword-matching to hit a quota.

- **Every beat passes a per-citation interpretation** — write *"what is this line actually about?"* first, then source THAT. If you can't source something that accurately illustrates the real point, **drop the beat — don't pad.** No b-roll is better than wrong b-roll.
- **Default cadence = front-loaded:** dense, punchy b-roll in the intro/hook, then sparse and precise through the body.
- **But "skip" is the LAST resort, not the default.** Before proposing skip for a beat, walk the full palette out loud — receipt? headline? face? product UI? concept graphic? — and only skip when every lane genuinely fails. A rhetorical line with no proper noun is exactly when a *sentiment receipt* or a *concept graphic* shines.

## Understand the TOPIC first, then source the MEANING — not the keywords

1. Read the **whole transcript** and understand the thesis BEFORE sourcing anything. Context decides what illustrates each line.
2. **B-roll illustrates the POINT, not the words.** The proper noun in a sentence is often NOT the referent:
   - *"X from Company"* → the person and their work, not the company logo.
   - *"like Company did"* → the *thing* Company did.
   - *"what Person said"* → the said-thing (their quote/post/essay), not their face.
3. **Literal vs evocative:** concrete nouns (a product, a named person, an event) → show the actual thing. Actions & abstractions → show something that *conveys* it; don't reenact literally.
4. **Mandatory reference sweep — every beat:** *is there a REAL ARTIFACT behind this line?* A quote, a stat, "people are saying", a named company event, "I saw/read" — almost always means a post, headline, report or page exists. That artifact is a default candidate; sourcing it is the skill's job, not an extra the user must request.

## Phrase-type → what to source (the judgment table)

| When the narration is… | Source this |
|---|---|
| "people are saying" / discourse | posts, headlines, search-result screenshots |
| a number / stat ("$24 a month", "750 sign-ups") | the figure ON SCREEN — the official page/report, or a Hyperframes stat card |
| growth / "went viral" | a graph going UP (Hyperframes), or the viral post itself with its metrics |
| decline / "tanking" | a graph going DOWN, error screens, the failing thing |
| "research shows" / authority | the study/paper headline (capture), or the expert on camera |
| a direct quote | the authentic post/page screenshot (beats a synthetic quote-card), or them saying it |
| an era / "back in the day" | archival footage/photos of that period — Commons has a lot in the public domain |
| a place | establishing shots of that place — Commons first |
| comparison / "X vs Y" | sequential full-bleed singles — never an agent-built split-screen |
| "how it works" / step-by-step | screen recording or Hyperframes graphics of the steps |
| a story with actions ("every morning he'd…") | the PLACES & ACTIONS, evocatively — never a literal reenactment |
| a metaphor ("like a rocket") | the metaphor object ONLY if it lands — metaphors default to SKIP |
| an emotion / reaction beat | propose the moment + register; the user supplies the meme |
| the creator's own content ("my video") | self-source their actual clip/post — not a search (no claim risk: own channel) |

## Genre → coverage defaults (what type, how much)

Genre predicts b-roll TYPE and density better than topic does (style + cadence answers from intake override these):
- **Competitor / drama** → receipts dominant, heavy (~40–60% coverage)
- **Opinion / explainer** → text/concept cards + receipt bursts (~20–30%)
- **Storytelling / listicle / vlog** → places, people, archival, products (~40–60%)
- **Podcast / webinar / interview** → light punctuation (~5–10%); b-roll marks references & claims, the talking head carries the rest

## Routing — three+ kinds of b-roll go to three+ sources

Classify EVERY moment before searching:

| Route | Trigger | Source |
|---|---|---|
| **Receipts** | Time-sensitive — drama, news, complaints, a current claim/stat | Official pages / reports / posts / headlines, recency-sorted, captured as clean screenshots |
| **Entity** | A **person**, a **place**, a **physical product**, or a **historical moment** | Wikimedia Commons first, then the official / authoritative source — the *canonical* image or clip, not a random upload |
| **Concept** | An abstract idea you'd have to *draw* (a process, a mental model, a stat) | Hyperframes graphics in the brand tokens of TASTE.md — the DEFAULT — or real footage from the authoritative source |
| **Cultural / Meme** | A creator clip or joke where *taste* decides | **NEVER searched or fetched by the agent.** Propose the MOMENT + register ("punchline at 2:31, deadpan") in the plan; the user supplies the meme from their library — or strikes the beat. |

**⛔ Cards never replace real footage of a literal thing.** If the beat names a concrete entity (a place, a product, a building) and real free footage exists, a generated card is a FAIL. Cards are for ideas with NO literal footage.

**Litmus (in order):** *Happening now?* → Receipts. *A person / place / product / event?* → Entity. *An abstract idea?* → Concept (Hyperframes). *A reaction beat?* → Meme (user's library).

## Sources & rights — free first, risk on the sheet

Every candidate carries its licence, and the contact sheet shows the risk. **Search in this order and stop at the first lane that has the right visual:**

1. **Wikimedia Commons** (`scripts/run commons.py search "…" [--video]`, then `get`) — public domain / CC0 first, then CC BY, then CC BY-SA. `commons.py` reads the licence from the file page and **drops anything else** (non-commercial, no-derivatives, fair use, unknown). Each download gets a `.json` sidecar (licence, author, page, restrictions, credit line) that the contact sheet and `place_broll.py` read. A `personality` restriction (recognisable person) or `trademarked` shows as an amber warning: say what it implies.
2. **Captures of official pages** (`scripts/run cdp_capture.py URL out.png`, `CDP_SCALE=2` for crisp text, `CDP_FULLPAGE=1` for the whole page) — the subject's own site, the ministry, the institution, the company, the official post. Licence `"capture"` on the sheet (blue). A page that is not the subject's own or an official one is not an official capture: leave its licence empty (amber "Licence to check").
3. **Graphics made here** (Hyperframes, see below) — licence `"own"` (green).
4. **YouTube** — only when the three lanes above have nothing right for a beat that needs motion. Channel-scoped search first (see Search syntax). **Every excerpt from a channel that is not the user's own gets the red "CLAIM RISK" badge** (Content ID claims hit official channels too). It is on the sheet so the user can decide; it is never the only candidate when a free one exists.
5. **Never:** stock-site previews (watermarked), news-agency photos, images from search results with no known licence, AI-generated footage (unless opted in).

The badge is computed by `contact_sheet.py` from the candidate's `source`, `channel`, `licence` and `url` — never typed by hand. Set them honestly; the user decides on that basis.

## Person clips — source the MOMENT, not the NAME

A clip merely *containing* the person is NOT relevant b-roll (the #1 person-clip failure):

1. Write the **mention-context** first: what is the line saying ABOUT the person — the trait, the action, the claim, the event?
2. **Query = person + mention-context, never the bare name.** "Christine Lagarde ECB press conference rates 2026", not "Christine Lagarde".
3. **Verify before presenting:** for each candidate, fill in *"in this image/clip, X is <doing/saying what, where, when>"* from the title/date/description. Can't fill it in → not a candidate.
4. No context match found → say so; offer the canonical image explicitly flagged "generic", or switch palette to their work/quote/headline (often better anyway).
5. **"The show/podcast" ≠ "this episode"** — a reference to a show sources the show's persistent branding, not one episode's guest art.
6. Official portraits of public officials are often on Commons in the public domain or CC BY — check there before any video.

## The palette — MIX it, never default to website screenshots

- **Faces** — for a named person, a live clip of them talking beats a frozen headshot when a free/official one exists. **A headshot of someone the audience won't recognize is DEAD b-roll** — for niche people, show their WORK (their post, product, talk). **⛔ NEVER cut to the speaker's own face as b-roll inside the same video.**
- **Product / UI** — the actual app UI or a real screen-recording (own recording > official channel > nothing). **Demos must be the MOST RECENT available.**
- **Receipts** — posts, headlines, reports, search results. For a named organisation, prefer **a page about a real event** (a publication, a decision, a figure) over the homepage.
- **Reference screenshots** — the real page cited (an authentic capture beats a synthetic quote-card). **The subject's own website, captured full-page and cropped per viewport, is a goldmine.**
- **Concept graphics (Hyperframes)** — for ideas, charts, stats. On-brand; never synthetic-looking stock.
- **Real / evocative footage** — places and actions from Commons (lots of public-domain and CC footage of cities, institutions, archives). Eyeball every frame for watermarks, burned-in captions, and AI-slop.
- **Memes / reactions** — from the user's library only; the agent proposes the moment, never the meme.

If a plan is >60% website screenshots, it's wrong.

## Cadence guardrails — restraint reads as taste

- **Beats run ~2–4s.** Anything under ~1.2s is unreadable (`place_broll.py` warns).
- **Max ONE burst montage per intro** (3–4 quick stills on a list-beat). Two+ bursts = chaos.
- **Max ONE long hold (>5s) per intro**, and only on a genuine explainer beat.
- When in doubt: fewer, real, obvious. The strongest edits use one clearly-right asset per beat.

## Motion-first — video beats a static page

When BOTH a moving and a static version of a free source exist, take the moving one. Stills stay right where READING is the point (a post, a headline, a report). Rubric tie-breaker: equal relevance AND equal rights → motion wins. A free still beats a claim-risk video.

## Stills motion — sub-pixel subtle zoom only; ffmpeg `zoompan` is banned

- **ffmpeg `zoompan` / crop-pans / scroll-pans are BANNED, at any speed, with any supersampling** — integer pixel steps stutter. Don't burn time re-attempting.
- **Sub-pixel rendering is the legitimate path:** `prepare_clip.py` → `zoom_still.py` (PIL float-box Lanczos per frame → x264).
- Defaults: **zoom-IN only, centered, ~1.5%/sec, cap ~2%/sec, stills only** — no pans, no zoom-outs, never synthetic motion on top of real video. Per-profile opt-out → `--static`.

## Eval rubric — score every candidate before showing it

Score 1–5 and drop anything below the bar:

1. **Recency fit** — is the beat time-sensitive or evergreen? Time-sensitive + old image = FAIL.
2. **Source authority** — primary/official/reputable vs random.
3. **Relevance** — depicts the exact named thing, not a loose association. Person clips: relevance = context-match.
4. **Recognizability / impact** — reads instantly, screenshots clean.
5. **Format fit** — ~2–6s, full-bleed-able, ≥1080p for stills used full-frame (≥720p video).
6. **Rights** — public domain / CC0 / own graphic > CC BY > CC BY-SA > official capture > YouTube from another channel (claim risk).

Check **time-sensitivity first**, then **rights as the tie-breaker**.

## Search syntax — scoped beats open, always

- **Commons:** `scripts/run commons.py search "Palace of Westminster"` (stills) / `--video` (clips). Search in the video's language AND English; Commons categories (`Category:…`) are often richer than free text — open the category page when a search is thin.
- **Captures:** `scripts/run cdp_capture.py https://… b-roll/raw/x.png`. Verify every capture by looking at it.
- **Posts on X without login:** `https://platform.twitter.com/embed/Tweet.html?id=<TWEET_ID>&hideThread=true` renders a clean card headlessly (capture it with `cdp_capture.py`). Licence: `"capture"` only for an official account.
- **YouTube, channel-scoped:** `yt-dlp "https://www.youtube.com/@<handle>/search?query=<q>" --flat-playlist --print "%(title).75s ||| %(duration_string)s ||| %(channel)s ||| %(id)s"` — `ytsearchN:<query>` is the fallback, flagged "⚠️ outside trusted sources". **Metadata first, download later** (`-f "bv*[height<=1080]+ba/b"`). Record `channel` in the candidate so the badge is right.

## Once a beat is agreed, sourcing it is the AGENT's job — the escalation ladder

"Login-walled" is a claim you prove by attempting, not a label for punting. Exhaust ALL of these before handing a beat back to the user:

1. **Local artifacts** — grep the user's notes/downloads/prior project folders for the exact link or handle.
2. **Identity hunt** — web-search via mention-context (distinctive phrases beat bare names); resolve shortlinks; oEmbed endpoints identify authors without auth.
3. **Commons** (search + categories) and the official site.
4. **Plain `yt-dlp`** — public videos usually need NO login (claim-risk badge applies).
5. **`yt-dlp --cookies-from-browser`** — the user's logged-in cookies beat most walls.
6. **Headless Chrome + CDP** — for captures behind consent walls (`cdp_capture.py` clicks "accept" in every frame context, CMP-iframe aware); then VERIFY the capture by looking at it.
7. **The user's logged-in browser** (browser-automation tools) when available.
8. Only if ALL genuinely fail → a precise you-source list, with a note of what was tried.

## Concept graphics with Hyperframes

- New graphic from a template: `scripts/new_graphic.sh stat-card b-roll/graphics/b04` (copies the template and a local GSAP: renders work offline and never change under you). Templates live in `templates/` (`stat-card`: a number counting up with a label and an optional source line; colours from variables).
- Render at the **timeline's frame rate** (the rush's: 25, 29.97 = `30000/1001`…): `scripts/hf.sh render b-roll/graphics/b04 -o b-roll/graphics/b04.mp4 --fps 30000/1001 --variables-file b-roll/graphics/b04.json`. Brand colours come from TASTE.md.
- A custom graphic is a new folder with an `index.html`: one paused GSAP timeline registered on `window.__timelines`, every timed element with `data-start`/`data-duration`, deterministic only (no `Math.random`, no `Date.now`, no network), GSAP from `vendor/`. Check with `scripts/hf.sh lint DIR` before rendering.
- `hf.sh` pins Hyperframes (0.8.79) with self-update and telemetry off: the CLI otherwise updates itself in the background on first run. Never run `hyperframes cloud` or `publish`.
- Look at a frame of every render before it goes on the sheet.

## Placement timing — land ON or just AFTER the word, never before

- Anchor to when the keyword is **spoken**, then a small lead (**+0.3 s** by default) so the cut lands as/just after it. B-roll *before* the word reads as a mistake.
- **Timing source: Roughcut's word-level transcript** (`<project>/transcripts/<rush>.words.json`: whisper.cpp with one segment per word, then fitted to the pauses heard). `place_broll.py` finds the word itself (accents, case, apostrophes and punctuation ignored: "l'Élysée" matches "Élysée") near the `at` time given, and drops the beat if the word isn't there or was cut out of the edit. Whisper word-ends run slightly early, so **bias LATER when unsure**.
- With music snapping (`"music": {"snap_broll": true}` in Roughcut's edl), Roughcut moves each start to a beat up to 0.35 s away: `place_broll.py` raises the lead to 0.4 s so a snap never lands before the word.
- For punchlines, land on the beat *after* the punchline (anchor on the word after it).
- **Connect adjacent b-rolls:** a gap shorter than 2 s between two cutaways is closed by stretching the first (prepared clips carry 3 s of handle for this); an overlap is trimmed. Never start the next one before its keyword.

## Composition — full-bleed, no composites

- **Cover-crop, never letterbox** (`prepare_clip.py` default).
- **No agent-built composites:** no split-screens, no 2-ups, no clever framing. Two referenced people → sequential full-bleed singles. The ONE allowed treatment beyond cover-crop is **blurred-fill** (`--blurfill`: enlarged blurred copy behind a fitted image — posts, portrait images, 4:3 archives).
- Don't upscale a tiny source to full-bleed — find a higher-res source (Commons often has several resolutions of the same subject).

## Preparing the picks — clips Final Cut Pro takes as they are

Every pick becomes an H.264 clip at the timeline's size and frame rate, silent, Rec. 709, with handles:

```
scripts/run prepare_clip.py b-roll/raw/westminster.jpg b-roll/clips/b04.mp4 --seconds 3 --match "<rush>.MP4" [--credit "Wikimedia Commons"]
scripts/run prepare_clip.py b-roll/raw/x.webm       b-roll/clips/b02.mp4 --in 12.5 --seconds 3 --match "<rush>.MP4" [--blurfill]
```

`--match` reads the rush's frame rate and size (what Roughcut builds its timeline from). Commons videos are WebM/Ogg, which Final Cut Pro cannot import: they always go through `prepare_clip.py`. Proxy first for big sources: a 4K/HEVC source >500 MB gets a 1080p working copy before frame scans (`ffmpeg -hwaccel videotoolbox -i src -vf scale=1920:-2 -c:v h264_videotoolbox -b:v 6M -c:a aac proxy.mp4`).

## Placing in Final Cut Pro — the Roughcut FCPXML

`picks.json` (in the project's `b-roll/` folder) lists the picked beats:

```json
{"beats": [{"id": "b04", "clip": "clips/b04.mp4", "word": "Westminster", "at": 19.8, "seconds": 3,
            "note": "Palace of Westminster", "licence": "CC BY-SA 4.0", "credit": "\"Palace of Westminster\" by …, CC BY-SA 4.0, via Wikimedia Commons"}],
 "removed": ["b09 — map of the country, too generic (cut by the user)"]}
```

`at` is when the word is said **in the rush** (from the transcript); `source` names the rush when the edit uses several. Then:

```
scripts/run place_broll.py "<Roughcut project folder>" b-roll/picks.json --dry-run   # where everything lands
scripts/run place_broll.py "<Roughcut project folder>" b-roll/picks.json --open      # writes it, opens Final Cut Pro
```

What it does: reads Roughcut's `edl.json` and transcript, anchors each beat on its word, connects/trims neighbours, writes `edl-broll.json` (Roughcut's own `edl.json` is never touched), and builds the project with **Roughcut's own `make_fcpxml.py`** and the options the edit was made with (read in `montage.log`: levels, J/L cuts, zooms, dressing, captions overlay kept on top). Each b-roll is a **connected clip in lane 1 above the talking head**, with a marker "B-roll: <id> <note>" to check each placement; Roughcut's captions and titles stay above. The project is named "<edit> – b-roll" and is merged into the edit's own event (`<edit> – b-roll.fcpxml` next to Roughcut's FCPXML), checked against Final Cut Pro's DTD. Roughcut's own b-roll (shots from the rushes) is kept unless `--drop-roughcut-broll`; overlaps are reported.

It also writes `BROLL-MANIFEST.md` (every beat: placed, stretched, trimmed or dropped, and why) and `CREDITS.txt` (credits the free licences require, with timecodes, for the YouTube description).

Final Cut Pro imports an FCPXML as a new project: changes made by hand in the edit's project are not in the b-roll one. Place the b-roll **before** finishing by hand, or copy the connected clips across.

Built somewhere else than the Mac that opens it (a cloud session)? `--map /local/project=/path/on/the/Mac/<project>` rewrites the media paths; the files must then be at those paths on the Mac.

## Iteration discipline — the manifest

Across versions the #1 failure is silently DROPPING beats the user already approved. `picks.json` is the source of truth and `BROLL-MANIFEST.md` its report. Before EVERY re-placement: read the manifest, verify every approved beat is still in `picks.json`, add the new ones, move cut ones to `"removed"`. Approved b-roll never disappears without the user explicitly cutting it.

## Self-verification — look at every clip before the user does

Before the contact sheet: look at every candidate's picture. After `prepare_clip.py`: extract frames at the start, middle and end of every prepared clip, tile them, and LOOK:

```
mkdir -p b-roll/check
for f in b-roll/clips/*.mp4; do for p in 0.1 0.5 0.9; do d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"); ffmpeg -v error -y -ss $(echo "$d*$p" | bc) -i "$f" -frames:v 1 -vf scale=480:-2 "b-roll/check/$(basename "$f" .mp4)_$p.png"; done; done
montage b-roll/check/*.png -tile 6x -geometry +4+4 b-roll/check.png
```

After import, the "B-roll:" markers in Final Cut Pro mark every placement to check (joints included).

**Explicit AUTO-REJECT checklist — a frame containing ANY of these fails, no judgment call:**
1. Burned-in captions/subtitles from the source clip
2. Name-tags / lower-thirds identifying strangers
3. Watermarks or channel logo bugs (corner badges from official bodies are a flagged exception, not a free pass)
4. The speaker's own face appearing as b-roll in their own video
5. A generated card where the beat names a literal, filmable thing
6. Template-looking composites (two portraits side by side, collage cards)
7. Letterboxing, tiny floating content, or visible blur-edge framing errors
8. A split-second sliver of talking head (<1s) between two cutaways (`place_broll.py` connects gaps under 2 s; check its WARNINGs)

Grade the grid against this list line by line — "looks fine" without the list is how watermarked clips ship.

## Workflow summary

1. **Load the profile** — [TASTE.md](TASTE.md): onboarding gate, guardrails, brand tokens, own channels.
2. **Ask style + cadence** — format (vlog / explainer / interview / heavy-intro) and density. These override genre defaults.
3. **Get the transcript** — the rush goes through Roughcut first (the app, or the Finder Quick Action): its project folder (`~/Movies/Roughcut/<name> <date>/`) holds `transcripts/<rush>.txt` (readable, timed) and `<rush>.words.json` (word level), made with the whisper.cpp model already on the Mac. Read the `.txt` for understanding, the words for anchors. A rush not yet through Roughcut, for planning only: `bash /Applications/Roughcut.app/Contents/Resources/engine/roughcut transcribe.sh RUSH OUT_DIR LANGUAGE`, then `… roughcut words.py OUT_DIR/<rush>.json --audio RUSH`. Never install mlx-whisper or another Whisper. Long-form (>~10 min): score segments for b-roll value first.
4. **Classify + propose (no fetching yet)** — annotate each beat with its interpretation, route, the reference sweep result, the planned source lane (Commons / capture / graphic / YouTube-flagged). **Present the plan and wait for the user to react.**
5. **Constrained search** — free sources first; score candidates; verify person clips against mention-context; drop the weak ones. Work in `<project>/b-roll/` (`raw/`, `graphics/`, `clips/`).
6. **Contact sheet → user picks.** Write `b-roll/candidates.json`, run `scripts/run contact_sheet.py b-roll/candidates.json` (→ `contact-sheet.png`, paginated), show it, wait. Never place before the answer.
7. **Prepare** the picks (`prepare_clip.py`), verify the grid.
8. **Place** (`place_broll.py --dry-run`, then for real), show the table, the manifest and the credits; open in Final Cut Pro if asked.

## Tools

- **Transcript:** Roughcut's word-level transcript (whisper.cpp, with the model Roughcut uses).
- **Search / download:** `commons.py` (Wikimedia Commons, licence-checked), `yt-dlp` (flagged), `cdp_capture.py` (headless Chrome via CDP; Google Chrome, Chromium or Hyperframes' headless shell).
- **Graphics:** Hyperframes via `hf.sh` (pinned) + `new_graphic.sh` + `templates/`.
- **Stills zoom:** `zoom_still.py` (sub-pixel PIL float-box → x264; never `zoompan`), called by `prepare_clip.py`.
- **Prepare:** `prepare_clip.py` (cover-crop / blurred fill, timeline fps and size, silent, credit).
- **Contact sheet:** `contact_sheet.py` (licence badges, CLAIM RISK).
- **Placement:** `place_broll.py` → Roughcut's `make_fcpxml.py` + `fcpxml_merge.py` → FCPXML checked against Final Cut Pro's DTD.
- **Setup:** `setup.sh` (installs only what is missing; `--check` to only look).
