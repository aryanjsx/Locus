# ADR: Product naming — Kommy vs AURA

**Status:** Accepted  
**Date:** 2026-07-08  
**Context:** Credibility remediation pass before public visibility

## Problem

The repository presents a split identity:

| Surface | Current branding |
|---|---|
| GitHub repo name / URL | `AURA` (`github.com/aryanjsx/AURA`) |
| GitHub About (pre-fix) | "Autonomous **Utility** & Resource Assistant" |
| README footer / engineering spec | "Autonomous **Unified** Response Architecture" |
| Spoken wake phrase / persona | **Kommy** ("Hey Kommy") |

Two different acronym expansions were live simultaneously. The repo URL and package name (`aura`) use generic "AURA"-style branding, while the differentiated, search-viable product persona is **Kommy** — chosen because saturated assistant names dilute discoverability.

This is the same class of contradiction as the pre-fix dual `SafetyGate` implementations: two naming strategies deployed at once.

## Options considered

### (a) Rename GitHub repo to incorporate Kommy (e.g. `aryanjsx/kommy`)

**Pros**

- URL becomes search-distinct immediately
- External links are cheap to fix today (0 stars/forks at decision time)
- Cost rises every day rename is deferred

**Cons**

- Python package, engineering spec, and 40+ internal modules remain `aura` / `AURA`
- CI badges, clone URLs, and docs require a sweeping URL migration
- Contributors see `kommy` in the browser and `aura` in imports — persistent cognitive split

### (b) Keep repo name `AURA`; lead with Kommy in public-facing surfaces

**Pros**

- Matches existing package layout (`aura/`), spec title, and module naming
- No broken clone URLs or badge churn
- Kommy can be the **product/persona** name; AURA remains the **architecture** name

**Cons**

- GitHub URL stays generic/search-saturated regardless of README content
- Requires discipline: every new doc must say "Kommy" first, "AURA architecture" second

## Decision

**Choose (b): keep the GitHub repository named `AURA`; reposition Kommy as the primary product name in description, topics, README headline, and spoken persona.**

### Canonical naming

| Layer | Name |
|---|---|
| Product / persona (what users say and search for) | **Kommy** |
| Architecture / codebase (what engineers import) | **AURA** — **A**utonomous **U**nified **R**esponse **A**rchitecture |
| Python package | `aura` (unchanged) |

The GitHub About description, README header, and `public/index.html` meta must all use the **Unified** expansion, not "Utility."

### Execution checklist

- [x] GitHub About → Kommy-first description with AURA expansion
- [x] README headline → Kommy product name, AURA architecture subtitle
- [x] Remove mismatched "Autonomous Utility & Resource Assistant" strings
- [ ] Optional future: rename repo to `kommy` if external traction justifies URL migration (revisit at 100+ stars or first major press)

## Consequences

- Wake phrase, TTS prompts, and user docs say **Kommy**
- Code imports, spec filename, and architecture diagrams say **AURA**
- No partial rename — URL stays `AURA`, persona stays `Kommy`, one acronym expansion everywhere public
