# SOCIAL_MEDIA_TODO.md

**Status: Pause (Rolf, 2026-10-03).** Die Social-Media-Experimente liegen ausgesetzt — Rolf möchte sich dem Thema "in den sozialen Medien gibt es kaum noch Fakten … das ist nicht meine Welt" nicht verschreiben. Die Tools hier sind **eingelagert für spätere Verwendung**, nicht verworfen.
Herkunft: Aus **`SOCIAL_MEDIA_TOOLS_TODO.md`** getrennt (2026-10-03). Die 9 Plattform-unabhängigen Tools stehen jetzt in **`TOOLS_TODO.md`** (E-Mail, TOTP, Session-Persistenz, Stock-Bilder, Bild-Gen, TTS/STT, Video, Screenshots, Link-Prüfung) — die können auch **ohne** Social-Media genutzt werden (E-Mail-Verwaltung, Doku-Assets, Audio-Experimente, …).
Diese 3 Tools hier sind die **eigentlich Social-Media-spezifischen** Teile (Veröffentlichung + Planung + Messung) und warten hier.

**Konvention (übernommen aus der Ausgangsdatei):**
- **Local-first** vor SaaS, aber SaaS-Optionen sind erlaubt.
- Für jedes Tool: **Zweck** + **Schnittstellen/Frameworks** (konkrete API/SDK/Bibliotheken mit Links).
- Konkret **umsetzen** (`src/selma/my_tools.py`) und **Skills** wären spätere Runden — aktuell gilt: **reine Recherche**, keine Verpflichtung.

---

## Social-Media-Tools (3)

### S1. Content-Publikation (Social-Plattformen)
**Zweck:** Text/Media auf eine oder mehrere Plattformen posten (API-basiert oder via Browser).

**Schnittstellen / Frameworks:**

**A) Offizielle Plattform-API (direkt):**
| Plattform | API | Link | Kosten |
|---|---|---|---|
| Instagram | **Meta Graph API** (Instagram Graph) | [developers.facebook.com/docs/instagram-platform](https://developers.facebook.com/docs/instagram-platform) | Kostenlos (Business Account, Meta-Dev-Zugang) |
| X / Twitter | **X API v2** (Posts, Media) | [docs.x.com/x-api/posts](https://docs.x.com/x-api/posts) | Free tier sehr limitiert (~100 Tweets/Monat), Basic $100/Monat |
| TikTok | **TikTok Content Posting API** (Beta) | [developer.tiktok.com](https://developer.tiktok.com) | Beta, Antrags-Prozess |
| LinkedIn | **LinkedIn Marketing API** (Posts) | [learn.microsoft.com/linkedin/marketing](https://learn.microsoft.com/en-us/linkedin/marketing/community/post/sharing) | Kostenlos (Free tier) |
| Mastodon | **Mastodon REST API** (ActivityPub) | [docs.joinmastodon.org/api](https://docs.joinmastodon.org/api) | Kostenlos (Self-host oder Instance) |
| Pixelfed | **Pixelfed REST API** | [docs.pixelfed.org](https://docs.pixelfed.org) | Kostenlos (Self-host) |

**B) SaaS-Zwischenschicht (mehrere Plattformen, einheitliche API):**
| Dienst | Typ | Anmerkung | Link |
|---|---|---|---|
| **Buffer API** | SaaS | Community/Pro, Instagram/FB/Twitter/LinkedIn/Threads, [developer.buffer.com](https://developer.buffer.com) |
| **Later API** | SaaS | Instagram/TikTok/LinkedIn/FB, [later.cream/developers](https://later.cream/developers) |
| **Postiz** | **Self-host** | Open-Source Social-Scheduler, API + Web UI, [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) |

**C) Unofficial/Wrapper (wo es offiziell keine API gibt):**
| Bibliothek | Typ | Anmerkung |
|---|---|---|
| `instagrapi` | **lib** | Robustes Instagram (unofficial), [instagrapi.readtheadocs.io](https://instagrapi.readthedocs.io) |
| `tweepy` | **lib** | X/Twitter (offizieller Wrapper), [tweepy.readthedocs.io](https://tweepy.readthedocs.io) |
| `mastodon.py` | **lib** | Mastodon-Python-Wrapper, [mastodon.py (github)](https://github.com/hhalim/mastodon.py) |
| `pixelfed-python-api` | **lib** | Pixelfed-Wrapper, `pip install pixelfed-python-api` — [dcappellin/pixelfed-python-api](https://github.com/dcappellin/pixelfed-python-api) |
| `snscrape` | **lib** | VORSICHT: weitgehend tot (TikTok/Instagram), [rumble-007/snscrape](https://github.com/JustAnotherArchivist/snscrape) |

**Wichtig zu wissen:**
- **Offizielle API** = stabil, aber Antrags-/Verifizierungs-Prozess (v. a. X, TikTok).
- **SaaS (Buffer/Later)** = einfachster Einstieg, aber Subscription + "black box".
- **Self-host (Postiz)** = volle Kontrolle, aber Betrieb (Docker, PostgreSQL, Reverse Proxy).
- **`instagrapi`** = pragmatische Alternative zur offiziellen IG-API (einfacher Zugang, aber Fragilität).
- **Browser-Automation (Selma `browser`-Tool)** = letzte Alternative, wenn keine API — aber langsam und fragil.

**Offene Entscheidungen (Rolf, falls je):**
- Welche Plattformen **zuerst**? (Vorschlag: Mastodon/Pixelfed = offen & selbst-hostbar, Instagram/X offiziell = Antrags-Puffer)
- Offizielle API vs. SaaS (Buffer) vs. `instagrapi` (unofficial)?
- Selbst-hosten (Postiz/Mastodon/Pixelfed) oder SaaS nutzen?

---

### S2. Content-Queue / Scheduling
**Zweck:** Posts in eine Warteschlange legen, mit Zeitstempeln planen, in Intervallen abspielen.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **Postiz** | **Self-host** | Built-in Scheduler (Tag/Stunde/Intervall), API + UI — [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) |
| **Buffer API** | SaaS | Scheduling via `scheduled_at`-Param, [developer.buffer.com](https://developer.buffer.com) |
| **`APScheduler`** | **lib** | Python, Cron/Interval/Once, robust, [apscheduler.readthedocs.io](https://apscheduler.readthedocs.io) |
| **`schedule`** | **lib** | Python, einfach, `pip install schedule` |
| **Celery + Redis** | **Stack** | Verteilte Async-Task-Queue, [docs.celeryq.dev](https://docs.celeryq.dev) |
| **`taskspooler`** | **CLI** | Leichte Task-Queue, `yum install taskspooler` |
| **Selma `task_manager`** | **Integriert** | Interner Scheduler, nutzt Selma-Ökosystem |

**Wichtig zu wissen:**
- **`APScheduler`** ist die **beste lokale** Python-Option, wenn kein SaaS.
- **Celery + Redis** = überkill für 1 Server, aber robust.
- **Postiz** = "All-in-One" für Social-Scheduling (Post + Schedule + UI).
- **Selma `task_manager`** = falls der Scheduler Teil des Agenten-Flows sein soll.

**Offene Entscheidungen (Rolf, falls je):**
- SaaS (Buffer) vs. Self-host (Postiz) vs. lokaler Scheduler (`APScheduler`)?
- Sollen mehrere Plattformen gleichzeitig gequeued werden?

---

### S3. Engagement / Metriken (Graph-Analytics)
**Zweck:** Follower, Likes, Kommentare, Reichweite, CTR für einen/mehrere Account(s) einlesen.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **Meta Graph Insights API** | **Plattform-API** | Instagram/FB Insights, [developers.facebook.com/docs/instagram-platform/insights](https://developers.facebook.com/docs/instagram-platform/insights) |
| **X API v2 — Tweet Metrics** | **Plattform-API** | `GET /2/tweets/metrics/...`, [docs.x.com/x-api/metrics](https://docs.x.com/x-api/metrics) |
| **LinkedIn Page/Post Insights** | **Plattform-API** | Company-Page-Insights, [learn.microsoft.com/linkedin/marketing](https://learn.microsoft.com/en-us/linkedin/marketing/community/community-management/company-pages/insights) |
| **TikTok Analytics API** | **Plattform-API** | (Beta) [developer.tiktok.com](https://developer.tiktok.com) |
| **`instagrapi`** (unofficial) | **lib** | `insta.user_insights()`, [instagrapi.readthedocs.io](https://instagrapi.readthedocs.io) |
| **SocialBlade** (YouTube/Twitch) | **SaaS** | [socialblade.com](https://socialblade.com) |
| **Ahrefs / SimilarWeb** | **SaaS** | Web-Metriken, [ahrefs.com](https://ahrefs.com) |
| **`feedparser`** + RSS | **lib** | Für Plattformen ohne echte API, [feedparser.readthedocs.io](https://feedparser.readthedocs.io) |

**Wichtig zu wissen:**
- **Offizielle API** = sauber, aber Antrags-/Free-Tier-Limits (X-API Free = 1000 Reads/Monat).
- **`instagrapi`** = pragmatisch für Instagram, wenn offizielle API zu langsam/limitiert.
- **RSS/`feedparser`** = Fallback für Plattformen ohne echte Metrics-API (Mastodon hat `/timelines/public.json`).
- **Aggregatoren** (SocialBlade, Ahrefs) = gut für YouTube/Twitch/Web, aber nicht für Social-Plattformen per se.

**Offene Entscheidungen (Rolf, falls je):**
- Welche Plattformen brauchen Metriken? (Mastodon hat sie, Instagram/X/LinkedIn offiziell, TikTok Beta)
- Offizielle API vs. `instagrapi` (unofficial) für IG?
- SaaS (SocialBlade) für YouTube/Twitch, oder offizielle YouTube/Twitch API?

---

## Offene Entscheidungen (Rolf, zusammengefasst — nur falls je wieder aktiv)

1. **Plattformen zuerst:** Welche Social-Plattformen sollen zuerst bedient werden? (Mastodon/Pixelfed = offen & selbst-hostbar; Instagram/X/LinkedIn/TikTok = Antrags-Puffer)
2. **Publikation:** Offizielle API vs. SaaS (Buffer) vs. `instagrapi` (unofficial)?
3. **Scheduling:** SaaS (Buffer) vs. Self-host (Postiz) vs. lokal (`APScheduler`)?
4. **Metriken:** Welche Plattformen, offizielle API vs. unofficial (`instagrapi`) vs. SaaS-Aggregatoren?
5. **Generell:** Reihenfolge, wenn das Thema je wieder auf den Tisch kommt.

> Abhängigkeiten auf Tools aus `TOOLS_TODO.md` (falls je aktiviert): Tool S1–S3 konsumieren Outputs von Bild-Gen (Tool 5), TTS (Tool 6), Video (Tool 7), Stock-Bilder (Tool 4).
