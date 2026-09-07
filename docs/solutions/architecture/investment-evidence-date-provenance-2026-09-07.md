---
title: Date provenance and the investment-topic contract for supplementary investment evidence
date: 2026-09-07
category: docs/solutions/architecture
module: skills/last30days/scripts/lib/grounding.py
problem_type: design_decision
component: evidence_freshness
severity: medium
applies_when:
  - an external workflow wants to consume last30days output as dated evidence and needs to know which dates it can trust
  - a web backend (Serper first) labels results relatively ("5 days ago") and the engine has to decide whether that counts as in-window
  - a YouTube item ships with published_at null and someone asks whether the provider actually supplied a date
  - someone proposes raising the web result count, or researching a listed company by ticker
related_components:
  - grounding
  - youtube_yt
  - schema.to_agent_export
  - investment_topic
  - freshness_verification
tags:
  - date-provenance
  - freshness
  - serper
  - youtube
  - investment-topic
  - cashtag
  - agent-export
  - design-decision
---

# Date provenance and the investment-topic contract for supplementary investment evidence

## Context

An external investment workflow evaluated last30days as a supplementary evidence collector for three listed companies (Generac `$GNRC`, FuelCell Energy `$FCEL`, Whirlpool `$WHR`) with hand-authored `--plan` query plans and Serper as the web backend. The evaluation gate was: at least two of the three tickers reach `EXPLORATORY` (20-49 ranked items) or better, every item used to satisfy a freshness requirement carries a verifiable or explicitly derived date, unknown dates never pass a freshness gate silently, and entity precision does not regress.

The pre-change experiment (raw reports and logs stay in the gitignored `docs/comparison-results/iios-serper-2026-09-07/`) established four engine behaviors that decided the outcome more than the plans did:

1. Entity grounding keys on the topic's first token (`rerank._primary_entity` / `_entity_grounded`), so a ticker-first topic (`WHR Whirlpool ...`) demoted every Reddit and StockTwits post that named the company but not the symbol. Whirlpool measured 1 ranked item ticker-first and 22 company-first with an identical plan.
2. StockTwits resolves a symbol only from an explicit cashtag (`stocktwits.detect_symbols`); without one it failed with `no symbol resolved`.
3. `grounding.serper_search` discarded every Serper result whose `date` label was relative (`10 hours ago`, `5 days ago`, `2 weeks ago`) because `_parse_serper_date` only knew absolute formats. A direct replay of the 12 plan subqueries showed 54 organic results, 43 dated, 23 kept, and 16 lost solely because the label was relative. Those were the freshest results, including FuelCell Energy's fiscal Q3 8-K coverage.
4. The ScrapeCreators YouTube search parser read `upload_date` / `date` / `published_at`, none of which the provider sends; it sends `publishedTime` (ISO 8601) and `publishedTimeText` (relative). Every YouTube item therefore exported `published_at: null`, and the lane's soft date filter logged "0 within date range, keeping all", which let videos of unknown age into the results.

## What changed

Four bounded changes, all behind existing seams:

- **Serper relative dates are resolved, not discarded.** `dates.parse_relative_date` turns `N minutes|hours|days|weeks|months ago` (singular, plural, `a`/`an`/`one`) into an interval against an explicit UTC retrieval instant (`retrieved_at`, injectable). `grounding.resolve_result_date` classifies each label: absolute in-window (`source_absolute`, confidence `high`), relative with its whole plausible interval inside the window (`derived_relative`, confidence `med`), or dropped with a reason (`undated`, `unparsable_date`, `out_of_window`, `relative_unverifiable`). The Serper artifact records `organicCount`, `resultCount`, `requestedCount`, `retrievedAt`, `derivedRelativeCount`, and the `dropped` breakdown so a thin web lane is diagnosable.
- **The web result count is configurable and bounded.** `LAST30DAYS_WEB_MAX_RESULTS` / `--web-max-results` (default 5, bounds 1-20, precedence CLI > env > default) feeds the `count` of every keyed backend (Brave, Exa, Serper, Parallel). argparse rejects out-of-range CLI values; an invalid env value falls back to the default and an out-of-range one is clamped, each with one stderr warning.
- **YouTube dates are preserved with provenance.** `youtube_yt.resolve_video_date` reads yt-dlp `upload_date` / `timestamp` and ScrapeCreators `publishedTime` as `source_absolute`, resolves `publishedTimeText` as `derived_relative` only when no absolute value exists, and leaves the date null with provenance `unknown` otherwise. Nothing is inferred from ordering, engagement, transcripts, or the retrieval time itself.
- **The investment-topic contract lives in one pure module.** `lib/investment_topic.py` validates and formats `<Company Name> $<TICKER> <short research objective>` with no engine imports. The CLI consults it twice: `--investment-topic` makes the contract mandatory (exit 2 with violations and a suggested rewrite), and financial topics that are ticker-first or lack a cashtag get a non-blocking warning. Non-financial topics are never touched; comparison topics are left to comparison mode.

The agent JSON export is now `schema_version` `1.3`: each result carries `date_provenance` (`source_absolute`, `derived_relative`, `unknown`). Golden snapshot and `docs/reference/json-export.md` updated.

## Why

Evidence quality matters more than evidence quantity. The old behavior was wrong in both directions at once: it threw away the freshest web results (relative labels) while letting undated YouTube videos through as if they were in-window. Resolving relative labels recovers real, recent evidence; attaching provenance lets a consumer refuse to treat an unknown date as fresh; recording drop reasons makes "the web lane returned 1 result" explainable instead of silent.

The topic contract is deliberately not a global rewrite. last30days researches people, products, and events; only listed-company topics suffer the ticker-first failure, and only there does a cashtag mean anything. A pure helper plus an opt-in flag keeps the boundary clean for an external adapter and keeps domain logic out of retrieval.

## Date-provenance model

| `date_provenance` | Meaning | `date_confidence` | Freshness gate |
| --- | --- | --- | --- |
| `source_absolute` | The provider supplied the timestamp (Reddit `created_utc`, HN time, yt-dlp `upload_date`, ScrapeCreators `publishedTime`, an absolute Serper label). | `high` when inside the window | May satisfy a freshness requirement when `published_at` is inside the window. |
| `derived_relative` | A relative label (`5 days ago`) resolved against the retrieval instant. `published_at` is the latest plausible calendar date; metadata carries `date_source_text`, `date_precision` (`day`, `week`, `month`), `date_retrieved_at`. | `med` when the whole plausible interval is inside the window, otherwise `low` | May satisfy a freshness requirement only when `med`; the web lane drops `low` (`relative_unverifiable`). |
| `unknown` | No usable date. | `low` | Never satisfies a freshness requirement. `signals.freshness` scores it 0 in `strict_recent`; `schema.freshness_verifiable()` is False. |

Rounding slack per unit is part of the model: `5 days ago` may be almost 6 days back, `2 weeks ago` almost 3 weeks, `1 month ago` almost 2 months. On a 30-day window `4 weeks ago` and `1 month ago` are therefore unverifiable and dropped from the web lane rather than counted as fresh. Precision is never manufactured: month-precision labels never earn `med`.

The web (grounding) lane still requires a date (`normalize.filter_by_date_range(require_date=True)`), so undated web results are dropped and counted, not retained. YouTube keeps undated items (an unknown date is a coverage gap, not a stale item) but exports them as `unknown`.

Example exported results:

```json
{"source": "grounding", "published_at": "2026-09-02", "date_provenance": "derived_relative", "title": "FuelCell Energy Reports Third Quarter Fiscal 2026 Results"}
{"source": "grounding", "published_at": "2026-08-29", "date_provenance": "source_absolute",  "title": "Generac Reports Second Quarter 2026 Results"}
{"source": "youtube",   "date_provenance": "unknown",                                        "title": "(video whose provider record carried no date)"}
```

The derived item's `SourceItem.metadata` holds `date_source_text: "5 days ago"`, `date_precision: "day"`, `date_retrieved_at: "2026-09-07T20:02:11+00:00"`.

## Configuration behavior and limits

| Knob | Default | Bounds | Precedence | Invalid value |
| --- | --- | --- | --- | --- |
| `--web-max-results <n>` | unset | 1-20 | wins | rejected by argparse (exit 2) |
| `LAST30DAYS_WEB_MAX_RESULTS` | 5 | 1-20 | after the flag | non-integer falls back to 5; out of range clamped; one warning each |
| `--investment-topic` | off | - | - | malformed topic exits 2 with guidance |

Secrets are never logged: the warning prints the offending count value only.

## Benchmark: before and after

Same three hand-authored plans, same `--subreddits`, same `--web-backend=serper`, same 30-day window (2026-08-08 to 2026-09-07), one run per cell. Variant B is the pre-change entity-first run (the "22, EXPLORATORY" baseline). On the hardened engine: C keeps B's exact topic; C-short uses the contract topic `<Company> $<TICKER> material developments affecting the investment thesis`; C-short10 adds `--web-max-results 10`; C-concrete uses a keyword objective (`earnings guidance backlog data center outlook`) at the default count. "Verified fresh" = dated, provenance not `unknown`, and inside the window. Entity match = share of items whose title or summary names the company.

| Ticker | Variant | N | Sufficiency | By source | Provenance (absolute / derived / unknown) | Verified fresh | Entity match | Serper organic / kept / derived | YouTube dated |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GNRC | B (before) | 14 | INSUFFICIENT | youtube 8, web 6 | 6 / 0 / 8 | 6 | 100% | n/a | 0/8 |
| GNRC | C (same topic) | 8 | INSUFFICIENT | youtube 3, web 5 | 8 / 0 / 0 | 8 | 100% | 15 / 10 / 3 | 4/4 retrieved |
| GNRC | C-short | 8 | INSUFFICIENT | web 6, stocktwits 2 | 8 / 0 / 0 | 8 | 100% | 15 / 8 / 2 | 1/1 |
| GNRC | C-short10 | 9 | INSUFFICIENT | web 7, stocktwits 2 | 9 / 0 / 0 | 9 | 100% | 20 / 14 / 5 | 1/1 |
| GNRC | C-concrete | 17 | INSUFFICIENT | web 7, reddit 6, youtube 2, stocktwits 2 | 17 / 0 / 0 | 17 | **65%** | 15 / 10 / 3 | 2/2 |
| FCEL | B (before) | 15 | INSUFFICIENT | youtube 11, web 4 | 4 / 0 / 11 | 4 | 100% | n/a | 0/11 |
| FCEL | C (same topic) | 13 | INSUFFICIENT | web 9, youtube 4 | 8 / 5 / 0 | 13 | 100% | 20 / 15 / 10 | 4/4 |
| FCEL | C-short | 11 | INSUFFICIENT | web 8, stocktwits 2, youtube 1 | 7 / 4 / 0 | 11 | 100% | 20 / 13 / 9 | 1/1 |
| FCEL | C-short10 | 21 | EXPLORATORY | web 20, youtube 1 | 7 / 14 / 0 | 21 | 100% | 39 / 33 / 23 | 1/1 |
| FCEL | C-concrete | 18 | INSUFFICIENT | web 13, reddit 2, stocktwits 2, youtube 1 | 9 / 9 / 0 | 17 | **89%** | 20 / 19 / 14 | 1/1 |
| WHR | B (before) | 22 | EXPLORATORY | youtube 8, web 6, reddit 6, hn 1, stocktwits 1 | 14 / 0 / 8 | 14 | 100% | n/a | 0/8 |
| WHR | C (same topic) | 19 | INSUFFICIENT | web 7, reddit 6, youtube 5, hn 1 | 17 / 2 / 0 | 14 | 100% | 19 / 17 / 6 | 10/10 |
| WHR | C-short | 17 | INSUFFICIENT | reddit 7, web 6, stocktwits 3, hn 1 | 15 / 2 / 0 | 17 | 100% | 19 / 12 / 3 | 1/1 |
| WHR | C-short10 | 21 | EXPLORATORY | web 10, reddit 7, stocktwits 3, hn 1 | 17 / 4 / 0 | 21 | 100% | 29 / 25 / 15 | 1/1 |
| WHR | C-concrete | 18 | INSUFFICIENT | reddit 7, web 6, stocktwits 3, hn 1, youtube 1 | 15 / 3 / 0 | 17 | 100% | 20 / 15 / 9 | 1/1 |

Runtime 171-243 s per ticker in every variant. Reddit reported HTTP 429 throttling (12-22 sub-requests per subquery) in every run; StockTwits returned HTTP 403 on two runs (WHR C, FCEL C-short10) and 2-9 posts otherwise.

Reading the table honestly:

- **The "before" WHR EXPLORATORY was not real.** Its 22 items included 8 undated YouTube videos; once the same videos carry dates they are 2020 to early-August 2026 uploads. On verified-fresh evidence B was 6 / 4 / 14, not 14 / 15 / 22.
- **Same topic, hardened engine: fewer items, all dated.** C loses the undated videos and gains 3-10 derived-relative web results per ticker (FuelCell Energy's Q3 results release, call transcript, 8-K summary and slides were all relative-labeled and previously discarded).
- **`--web-max-results 10` is what crosses the threshold.** With the thesis-shaped contract topic it lifts FuelCell Energy and Whirlpool to 21 verified-fresh items each at 100% entity match; Generac stays at 9 because its coverage is thin, not because of the engine.
- **A keyword objective is rejected.** The Reddit lane scores relevance against the raw topic's tokens, so `earnings guidance backlog data center outlook` admitted Marvell, Dell and Chevron data-center earnings posts into Generac's results (entity match 65%). More items, worse evidence.
- **YouTube is now thin for these names.** With dates known, only 1-4 in-window videos exist per ticker; the 8-11 videos the old runs showed were mostly out-of-window.

## Remaining failure modes

- **The #1043 transcript rescue can still admit a dated-stale YouTube video.** When no in-window video exists, `normalize` keeps transcript-backed videos regardless of date (a tested, deliberate upstream rule). On the hardened engine those videos now carry real dates (WHR C surfaced uploads from 2020-09-07 and 2026-06-07; WHR C-concrete one from 2023-10-26), so a consumer must gate on `published_at` inside the window, not only on provenance. Not changed here; it is an upstream design decision.
- **Reddit is rate-limited on every run** (HTTP 429) because broad `--subreddits` lists multiply listing fetches. On-entity Reddit content exists only for Whirlpool (consumer repair threads, which are legitimate customer signals but weak thesis evidence).
- **StockTwits is intermittently blocked** (HTTP 403) and its surviving posts are mostly near-empty (`$WHR`, `$GNRC`); a consumer should treat them as sentiment breadcrumbs, not evidence.
- **Fusion prunes roughly half the kept Serper results** (33 kept -> 20 ranked for FCEL C-short10); the web lane's contribution is bounded by ranking as much as by retrieval.
- **Sufficiency is marginal.** 21 verified-fresh items is the bottom of `EXPLORATORY`; Generac never leaves `INSUFFICIENT`. Coverage depends on the name, and one run per ticker is supplementary evidence only.

## Cost implications

- Serper: one request per plan subquery (4 per run) in every variant; the relative-date fix recovers results from requests already paid for. `--web-max-results 10` doubles the page size, not the request count; check the provider plan for how larger pages are billed before making it a default.
- ScrapeCreators: unchanged. Dates come from the search response already fetched; no extra call is made to recover them.
- yt-dlp: unchanged; `timestamp` is read from the search dump already produced.
- No reasoning-provider or LLM call was added anywhere.

## Decision: LIMITED GO

All five gate conditions hold for exactly one configuration, and one of them only marginally:

1. Two of three tickers reach `EXPLORATORY` only with `--web-max-results 10` and the thesis-shaped contract topic (FCEL 21, WHR 21); at the default count no ticker does, and Generac never does.
2. Every freshness-qualified item carries `source_absolute` or `derived_relative` provenance with the derivation recorded.
3. Entity match is 100% in the recommended configuration; the keyword-objective variant regresses it and is rejected.
4. Cost is unchanged in request count; noise is modest (list articles, near-empty StockTwits posts); source failures (Reddit 429, StockTwits 403) are pre-existing and visible in `source_status`.
5. Unknown dates cannot satisfy a freshness requirement anywhere in the pipeline or the export.

Proceed to a separate import design only under these constraints: the contract topic with `--investment-topic`, `--web-max-results 10`, a consumer-side freshness gate of `published_at` inside the window **and** `date_provenance != unknown`, the sufficiency label attached to every run with its N, StockTwits near-empty posts filtered, and thin names (Generac-class) expected to land at `INSUFFICIENT`. Do not build the import on the "before" numbers; they counted undated videos.

**Exact next step:** write the import design as a supplementary feed keyed on `date_provenance` and the window, then validate the recommended configuration on a second set of three tickers (one thin, one mid, one heavily covered) before any adapter code is written.

## Links

- Engine: `lib/dates.py` (`parse_relative_date`, `relative_date_confidence`), `lib/grounding.py` (`resolve_result_date`, `resolve_web_max_results`, `serper_search`), `lib/youtube_yt.py` (`resolve_video_date`), `lib/schema.py` (`date_provenance`, `freshness_verifiable`, export `1.3`), `lib/investment_topic.py`.
- Tests: `tests/test_grounding_dates.py`, `tests/test_youtube_dates.py`, `tests/test_investment_topic.py`, `tests/test_cli_web_max_results.py`, golden `tests/fixtures/agent_export_v1.json`.
- Docs: `CONFIGURATION.md` (web result count, investment topics), `SKILL.md` Step 0.45 Class 6, `docs/reference/json-export.md`.
- Related: `docs/solutions/logic-errors/entity-grounding-full-phrase-false-demotion.md` (why the head token decides grounding).
