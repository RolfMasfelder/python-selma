# SOCIAL_MEDIA_TOOLS_TODO.md

**Kontext:** Social-Media-Experiment (geplantes getrenntes Workspace, Stand 2026-09-27: Verzeichnis **noch nicht existiert**; Rolf legt es an, z. B. `/home/rolf/workspace/socialmedia`).
Angela: 2026-09-27. Rolf entscheidet Reihenfolge; nichts ungefragt.

**Umgewandelt (2026-09-30):** Diese Liste ist jetzt ein **Werkzeugkasten** (capability-first, **nicht** Media-first).
Rolf hat noch nicht entschieden, welche Medien oder welchen Zweck die Tools bedienen; diese Liste zeigt erstmal die **technischen Möglichkeiten** pro Tool. Die **konkrete Umsetzung** (`src/selma/my_tools.py`) ist eine **spätere Runde**; **Skills** (Agenten-Leitfaden) sind **noch späterer** Runde. Alles Unten = **Möglichkeiten**, keine Verpflichtung.

**Kriterien für die Auswahl:**
- **Local-first** vor SaaS, aber SaaS-Optionen sind erlaubt.
- Für jedes Tool: **Zweck** (was es techn. kann) + **Schnittstellen/Frameworks** (Konkrete API/SDK/Bibliothek + ggf. Link, die wir tatsächlich gegen sprechen könnten).
- **Offene Entscheidungen** werden am Ende zusammengefast, nicht zerstreut.

> ⚠️ "Offen für Rolf" an der einzelnen Tool-Position = **wenn** du das Tool wirklich willst, was du noch entscheiden musst, nicht was du **jetzt** entscheiden musst.

---

## WERKZEUGKASTEN (12 Tools, capability-first)

### 1. E-Mail-Inbox (ephemeral / wieder verwendbar)
**Zweck:** E-Mail-Adressen erzeugen, empfangen, lesen, optional senden — für Account-Registrierung, Verifications, oder als "wiederverwendbare Identität".

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| `imap-tools` (Python) | **lib** | Generische IMAP-Lesen — passt auf **jeden** IMAP-Provider |
| `aiosmtplib` / `smtplib` | **lib** | Sende von wo auch immer |
| **Resend API** | Saas | Modern, `pip install resend`, 10 000 Mails/Tag free tier — [resend.com/api](https://resend.com/docs/api-reference/emails) |
| **Mailgun API** | Saas | Etabliert, Events/Callbacks, `pip install mailgun` — [documentation.mailgun.com](https://documentation.mailgun.com/en/developer-reference/api_overview.html) |
| **SendGrid API** | Saas | Grosse Community, `pip install sendgrid` — [developers.sendgrid.com](https://developers.sendgrid.com) |
| **Postmark API** | Saas | Schnell, sauber, `pip install postmarkpy` — [postmarkapp.com/developer/api](https://postmarkapp.com/developer/api/overview) |
| **Mailtrap Inboxes** | Saas (inkl. free) | Inbox-API für Test/Produktions-Verbindungen, `pip install mailtrap` — [mailtrap.io/inboxes-documentation](https://mailtrap.io/inboxes-documentation) |
| **Fastmail** / **mailbox.org** | Saas | "richtige" E-Mail-Boxen (IMAP via `imap-tools`) |
| **Mailcow / Mail-in-a-Box** | **Self-host** | Docker/VM, volle IMAP/SMTP-Box — [mailcow.email](https://mailcow.email) |
| **Temp-mail / mail.tm / Guerrilamail API** | Saas | Einweg-Adressen (Registrierung), `temp-mail`, `guerrillamail` API |
| **AnonAddy / Addy.io** | Saas | E-Mail-Verschlüsselung (1 Adress → viele) |

**Wichtig zu wissen:**
- **`imap-tools`** + **eigener IMAP-Provider** ist der günstigste Pfad für "echte" Adresse.
- **Self-host (Mailcow)** gibt volle Kontrolle, aber Betrieb (Mails, DKIM, DNS).
- **Temp-mail-Provider** = für **einmalige** Registrierung nur, nicht für "echte Identität".
- **Rate-Limits:** Alle APIs haben 429/Retry-After — Selma-Konvention ist **IMMER** zu respektieren.

**Offene Entscheidungen (Rolf):**
- Welche Strategie: eigene Box (Mailcow/Fastmail), SaaS (Resend/Mailgun), oder Disposable (Temp-mail)?
- Wie viele Adressen / Tag? Wiederverwendbar oder einmalig?
- Sende oder nur Empfang?

---

### 2. TOTP (2FA-Code-Generierung)
**Zweck:** 6-stellige TOTP-Codes generieren (RFC 6238, SHA1, 30s), optional Setup-URL/QR erzeugen.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| `pyotp` | **lib** | Standard, klein, `pip install pyotp`, RFC 4226 & 6238 — [pyotp.readthedocs.io](https://pyotp.readthedocs.io) |
| `otpauth` | **lib** | `pyotp` + QR-Setup-URL, `pip install otpauth`, `pip install qrcode` für QR — [pangea-data/otpauth](https://github.com/pangea-data/otpauth) |
| `speakeasy` | **Binary/CLI** | Rust, schnell, server + CLI, [akj/speakeasy](https://github.com/akj/speakeasy) |
| `oathtool` | **Binary/CLI** | Liboath, alt, robust, `yum install oathtool` |
| **RFC 4226** (HOTP) / **RFC 6238** (TOTP) | **RFC** | Die Spezifikationen — [rfc-editor.org](https://www.rfc-editor.org) |
| `pyqrcode` / `qrcode` | **lib** | QR-Code für Secret-Setup, `pip install qrcode` |

**Wichtig zu wissen:**
- **`pyotp`** ist die **sauberste** Python-Option, klein und standard.
- **Secret-Verwaltung:** `~/.selma/totp-secrets.json` (chmod 600) im **neuen Workspace**, NICHT im Repo.
- **Keine externe API nötig** — TOTP ist mathematisch, alles lokal.
- **Testvektoren:** RFC 4226/6238 haben offizielle Testvektoren (z. B. `ZHVROBQO2F2J` = "353" für "12345678901234567890") → **IMMER** als Unit-Test nutzen.

**Offene Entscheidungen (Rolf):**
- Welche Plattformen sollen 2FA erzwingen? (Instagram, TikTok, X, LinkedIn?)
- Einmalige Secrets (Registrierung) oder dauerhafte Konten (Verwaltung)?
- `pyotp` (Python) oder `pyotp` + CLI (`oathtool`)?

---

### 3. Session / Cookie-Persistenz
**Zweck:** Web-Login über Läufe hinweg erhalten (Session-Cookies, CSRF-Tokens, `access_token`).

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **Playwright `storage_state`** | **Framework** | `context.storage_state(path=...)` → export/import JSON, Standard für Login-Persistenz — [playwright.dev/python/docs/api/class-browsercontext](https://playwright.dev/python/docs/api/class-browsercontext#browsercontextstoragstate) |
| **Playwright `user_data_dir`** | **Framework** | Persistentes Profil, Auto-Save — [playwright.dev](https://playwright.dev/python/docs/api/class-browser) |
| `pycookiecheat` | **lib** | Liest Cookies direkt aus laufendem Browser (Chrome/Firefox), `pip install pycookiecheat` |
| `requests` + `CookieJar` | **lib** | Manuell, `requests.cookies.RequestsCookieJar` |
| **Selma `browser`-Tool** | **Integriert** | Bestehendes Playwright-Tool, kann `storage_state` schon nutzen (bzw. erweitern) |

**Wichtig zu wissen:**
- **Playwright `user_data_dir`** ist der **niedrigste Aufwand**: `--user-data-dir=/path/to/profile` und Playwright merkt sich alles.
- **`storage_state`** ist portabler (eine Datei, die man kopieren kann).
- **`pycookiecheat`** ist praktisch für "meine echten Browser-Login übernehmen" — aber erfordert laufenden Browser.
- **CSRF-Tokens / Session-Cookies** sind oft **IP-Tied** — falls die Plattform die IP prüft, muss die Quelle derselbe sein.

**Offene Entscheidungen (Rolf):**
- Welche Plattformen sollen Login-basiert sein? (Social mit Login nötig: Instagram, TikTok, X, LinkedIn)
- Playwright `user_data_dir` (einfach) oder `storage_state` (portabel)?

---

### 4. Content-Publishing (Social-Platformen)
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

**B) Saas-Zwischen-Schicht (mehrere Plattformen, einheitliche API):**
| Dienst | Typ | Anmerkung | Link |
|---|---|---|---|
| **Buffer API** | Saas | Community/Pro, Instagram/FB/Twitter/LinkedIn/Threads, [developer.buffer.com](https://developer.buffer.com) |
| **Later API** | Saas | Instagram/TikTok/LinkedIn/FB, [later.cream/developers](https://later.cream/developers) |
| **Postiz** | **Self-host** | Open-Source Social-Scheduler, API + Web UI, [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) |

**C) Unofficial/Wrapper (wo es offiziell keine API gibt):**
| Bibliothek | Typ | Anmerkung |
|---|---|---|
| `instagrapi` | **lib** | Robustes Instagram (unofficial), [instagrapi.readthedocs.io](https://instagrapi.readthedocs.io) |
| `tweepy` | **lib** | X/Twitter (offizieller Wrapper), [tweepy.readthedocs.io](https://tweepy.readthedocs.io) |
| `mastodon.py` | **lib** | Mastodon-Python-Wrapper, [mastodon.py (github)](https://github.com/hhalim/mastodon.py) |
| `pixelfed-python-api` | **lib** | Pixelfed-Wrapper, `pip install pixelfed-python-api` — [dcappellin/pixelfed-python-api](https://github.com/dcappellin/pixelfed-python-api) |
| `snscrape` | **lib** | VORSICHT: weitgehend tot (TikTok/Instagram), [rumble-007/snscrape](https://github.com/JustAnotherArchivist/snscrape) |

**Wichtig zu wissen:**
- **Offizielle API** = stabil, aber Antrags-/Verifizierungs-Prozess (v. a. X, TikTok).
- **SaaS (Buffer/Later)** = einfachster Einstieg, aber Subscription + "black box".
- **Self-host (Postiz)** = volle Kontrolle, aber Betrieb (Docker, PostgreSQL, Reverse Proxy).
- **`instagrapi`** = pragmatische Alternative zu offizieller IG-API (einfacher Zugang, aber Fragilität).
- **Browser-Automation (Selma `browser`-Tool)** = letzte Alternative, wenn keine API — aber langsam und fragil.

**Offene Entscheidungen (Rolf):**
- Welche Plattformen **zuerst**? (Vorschlag: Mastodon/Pixelfed = offen & selbst-hostbar, Instagram/X offiziell = Antrags-Puffer)
- Offizielle API vs. SaaS (Buffer) vs. `instagrapi` (unofficial)?
- Selbst-hosten (Postiz/Mastodon/Pixelfed) oder SaaS nutzen?

---

### 5. Content-Queue / Scheduling
**Zweck:** Posts in eine Warteschlange legen, mit Zeitstempeln planen, in Intervallen abspielen.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **Postiz** | **Self-host** | Built-in Scheduler (Tag/Stunde/Intervall), API + UI — [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) |
| **Buffer API** | Saas | Scheduling via `scheduled_at`-Param, [developer.buffer.com](https://developer.buffer.com) |
| **`APScheduler`** | **lib** | Python, Cron/Interval/Once, robust, [apscheduler.readthedocs.io](https://apscheduler.readthedocs.io) |
| **`schedule`** | **lib** | Python, einfach, `pip install schedule` |
| **Celery + Redis** | **Stack** | Verteilte Async-Task-Queue, [docs.celeryq.dev](https://docs.celeryq.dev) |
| **`taskspooler`** | **CLI** | Leichte Task-Queue, `yum install taskspooler` |
| **Selma `task_manager`** | **Integriert** | Interner Scheduler, nutzt Selma-Ökosysstern |

**Wichtig zu wissen:**
- **`APScheduler`** ist der **beste lokale** Python-Option wenn kein SaaS.
- **Celery + Redis** = überkill für 1 Server, aber robust.
- **Postiz** = "All-in-One" für Social-Scheduling (Post + Schedule + UI).
- **Selma `task_manager`** = falls der Scheduler Teil des Agenten-Flows sein soll.

**Offene Entscheidungen (Rolf):**
- SaaS (Buffer) vs. Self-host (Postiz) vs. lokaler Scheduler (`APScheduler`)?
- Sollen mehrere Plattformen gleichzeitig gequeued werden?

---

### 6. Engagement / Metriken (Graph-Analytics)
**Zweck:** Follower, Likes, Kommentare, Reichweite, CTR für eine/n mehrere(n) Account(s) einlesen.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **Meta Graph Insights API** | **Plattform-API** | Instagram/FB Insights, [developers.facebook.com/docs/instagram-platform/insights](https://developers.facebook.com/docs/instagram-platform/insights) |
| **X API v2 — Tweet Metrics** | **Plattform-API** | `GET /2/tweets/metrics/...`, [docs.x.com/x-api/metrics](https://docs.x.com/x-api/metrics) |
| **LinkedIn Page/Post Insights** | **Plattform-API** | Company-Page-Insights, [learn.microsoft.com/linkedin/marketing](https://learn.microsoft.com/en-us/linkedin/marketing/community/community-management/company-pages/insights) |
| **TikTok Analytics API** | **Plattform-API** | (Beta) [developer.tiktok.com](https://developer.tiktok.com) |
| **`instagrapi`** (unofficial) | **lib** | `insta.user_insights()`, [instagrapi.readthedocs.io](https://instagrapi.readthedocs.io) |
| **SocialBlade** (YouTube/Twitch) | **Saas** | [socialblade.com](https://socialblade.com) |
| **Ahrefs / SimilarWeb** | **Saas** | Web-Metriken, [ahrefs.com](https://ahrefs.com) |
| **`feedparser`** + RSS | **lib** | Für Plattformen ohne echte API, [feedparser.readthedocs.io](https://feedparser.readthedocs.io) |

**Wichtig zu wissen:**
- **Offizielle API** = sauber, aber Antrags-/Frei-Tier-Limits (X-API Free = 1000 Reads/Monat).
- **`instagrapi`** = pragmatisch für Instagram, wenn offizielle API zu langsam/limitiert.
- **RSS/`feedparser`** = Fallback für Plattformen ohne echte Metrics-API (Mastodon hat `/timelines/public.json`).
- **Aggregatoren** (SocialBlade, Ahrefs) = gut für YouTube/Twitch/Web, aber nicht für Social-Platformen per se.

**Offene Entscheidungen (Rolf):**
- Welche Plattformen brauchen Metriken? (Mastodon hat sie, Instagram/X/LinkedIn offiziell, TikTok Beta)
- Offizielle API vs. `instagrapi` (unofficial) für IG?
- SaaS (SocialBlade) für YouTube/Twitch, oder offizielle YouTube/Twitch API?

---

### 7. Stock-Bildsuche (royalty-free)
**Zweck:** CC0 / royalty-free Bild-URLs per Query (Begriff, Stil, Größe) suchen und zurückgeben.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung | Link |
|---|---|---|---|
| **Pexels API** | **lib/SaaS** | Free tier 200 req/h, 50 000 req/Monat (Paid), [pexels.com/api/documentation](https://www.pexels.com/api/documentation/) |
| **Unsplash API** | **lib/SaaS** | Free 50 req/h, 5000 req/Monat (Free), [unsplash.com/documentation](https://unsplash.com/documentation) |
| **Pixabay Content API** | **lib/SaaS** | Free, 100 req/h, [pixabay.com/api/docs](https://pixabay.com/api/docs/) |
| **Openverse API** | **lib (aggregator)** | Aggregiert CC-Images (Flickr/Wikimedia/etc.), [api.openverse.org/v1](https://api.openverse.org/v1/) |
| **Wikimedia Commons API** | **lib** | PD/CC, [commons.wikimedia.org/w/api.php](https://commons.wikimedia.org/w/api.php) |
| `python-pixabay` | **lib** | Python-Wrapper für Pixabay, `pip install python-pixabay` |
| `unsplash-client` | **lib** | Python-Wrapper für Unsplash, `pip install unsplash-client` |

**Wichtig zu wissen:**
- **Pexels** und **Unsplash** sind die **besten** für stock-freie Bilder (hohequalität, saubere API).
- **Pixabay** = ähnlich, aber mit etwas mehr "Stock-Foto"-Vibe.
- **Openverse** = Aggregator über **alle** CC-/FD-Quellen (Flickr, Wikimedia etc.) — gut für seltene Motive.
- **Lizenz-Pflicht:** Die APIs liefern immer die **Lizenz** im Response — **IMMER** bei Nutzung prüfen/ausgeben (Attribution für CC-BY).

**Offene Entscheidungen (Rolf):**
- Welche Quelle(n) zuerst? (Pexels + Unsplash sind der Standard-Start)
- Brauchst du **CC0 nur** (keine Attribution) oder auch **CC-BY** (Attribution erlaubt)?
- Wie viele Bilder pro Tag?

---

### 8. Bild-Generierung (lokal) — Qwen-Image-2.1
**Zweck:** Text→Bild, Bild-Editing (bis 10 Referenz-Bilder), native 2K, RGBA-Transparenz.

**⚠️ Recherche-Ergebnis (2026-09-30): Ollama + Qwen-Image-2.1 = nicht kombinierbar**
- **Ollama Image-Gen** (seit 20.01.2026) ist **macOS-only** ("Windows/Linux coming soon") und schleppt nur `x/z-image-turbo` + `x/flux2-klein` (4B/9B) mit — **Qwen-Image-2.1 ist NICHT im Ollama-Repo** (alle Kandidaten-URLs 404).
- Unser Ollama (v0.32.15, OpenSUSE) ist **Linux** → Feature ist hier nicht verfügbar.
- → **Kurzfazit:** "Qwen-Image-2.1 lokal über Ollama ohne ComfyUI" ist aktuell nicht möglich.

**Empfohlene lokale Pfade (ohne COMFY, CLI-freundlich):**
| Option | Typ | Anmerkung |
|---|---|---|
| **`stable-diffusion.cpp` (sd.cpp)** | **CLI/lib** | C++, Qwen-Image/FLUX/SDXL/SD3.5, **GGUF-Unterstützung**, CPU/CUDA, [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) |
| **Unsloth Desktop/CLI/Python** | **CLI/lib** | FP8/GGUF, Qwen-Image-2.1 explizit, [unsloth.ai/docs/models/qwen-image-2.1](https://unsloth.ai/docs/models/qwen-image-2.1) |
| **`diffusers` (Hugging Face)** | **lib** | Python-Pipeline, volle Kontrolle, [huggingface.co/docs/diffusers](https://huggingface.co/docs/diffusers) |
| **`transformers`** | **lib** | `pipeline("text-to-image")`, [huggingface.co/docs/transformers](https://huggingface.co/docs/transformers) |
| **ComfyUI** | **Full App** | Full node-graph, LoRA/ControlNet/Inpaint, **aber "komplexes Frontend"** |

**Cloud/API (Fallback):**
| Option | Typ | Anmerkung |
|---|---|---|
| **`Replicate`** | **API** | Hosted Qwen-Image-2.1/FLUX/SD3, [replicate.com/docs/topics/image-generation](https://replicate.com/docs/topics/image-generation) |
| **`fal.ai`** | **API** | Image-Gen API, [docs.fal.ai](https://docs.fal.ai/) |
| **Stability AI API** | **API** | [platform.stability.ai/docs/api-reference](https://platform.stability.ai/docs/api-reference) |
| **OpenAI `dall·e` API** | **API** | [platform.openai.com/docs/guides/images](https://platform.openai.com/docs/guides/images) |

**VRAM / Hardware-Fitness (Quelle: Unsloth-Doku, Stand 2026-09-26):**
| Konfiguration | VRAM | Auflösung |
|---|---|---|
| GGUF Q4_K_M | **11 GB VRAM** | 1024×1024 (Empfehlung) |
| INT8 / FP8 | **24 GB VRAM** | 1024×1024 |
| FP8 + Offload | **6 GB VRAM** | 512×512 (<2x langsamer) |
| CPU-only | **12-16 GB RAM** | 512×512 oder 1024×1024 (Q4_K_M) |
| Apple Silicon | **12-16 GB+ unified** | 1024×1024 |

> ⚠️ **Lizenz:** Qwen-Image-2.1 = **research-only** (kommerzielle Nutzung erfordert separate Qwen-Lizenz). **Wichtig für Social-Media:** Wenn die Bilder öffentlich auf einer Plattform erscheinen und du damit (auch indirekt) Geld verdienst, brauchst du die kommerzielle Qwen-Lizenz — im Zweifel bei Qwen/Tongyi klären, bevor wir loslegen.

**Offene Entscheidungen (Rolf):**
- Lokal (sd.cpp / Unsloth / diffusers) oder Cloud-API (Replicate / fal.ai)? → hängt von der Hardware ab: **Welche CUDA-Karte/VRAM läuft auf diesem Host?**
- Auflösung: 1024×1024 (Standard) oder reicht 512×512 (mehr Speicher/Zeit)?
- Nur Text→Bild oder auch Bild-Editing (Inpaint, Referenz-Bilder)?
- Welche Style-Vorlagen/LoRAs wären nützlich (Foto, Illustration, Infografik…)?

---

### 9. Audio (TTS/STT)
**Zweck:** Text→Audio (Voice-Overs für Video/Posts, z. B. TikTok/Reels), Audio→Text (Transkription, z. B. Kommentare analysieren, eigene Sprachnotizen).

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **`piper-tts`** | **Self-host/CLI** | Lokales, schnelles, mehrsprachig (DE!), ONNX — [rhasspy/piper](https://github.com/rhasspy/piper) |
| **Kokoro TTS** (`kokoro-onnx` / `kokoro-fastapi`) | **Self-host** | Kleine, hochwertige Stimmen, DE-Unterstützung, [hexgrad/Kokoro-82M](https://github.com/hexgrad/Kokoro-82M) |
| **`whisper.cpp`** | **CLI/lib** | Lokale STT, schnell, CPU-fähig, `ggml-base`-Modelle, [ggerganov/whisper.cpp](https://github.com/ggml-org/whisper.cpp) |
| **`faster-whisper`** (Python) | **lib** | CTranslate2, 4× schneller als OpenAI Whisper, DE-tark, [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| **`Vosk`** | **lib** | Offline, geringe RAM, `pip install vosk`, [alphacep/vosk-api](https://alphacep.github.io/vosk-api/) |
| **OpenAI `/audio/speech` + `/audio/transcriptions` API** | **SaaS** | `pip install openai`, sehr gut, aber Kosten/Cloud — [platform.openai.com/docs/guides/speech](https://platform.openai.com/docs/guides/speech) |
| **Edge-TTS** (Microsoft) | **lib** | `pip install edge-tts`, kostenlos, aber Cloud + ToS-Grauzone — [shakkarth/edge-tts](https://github.com/shakkarth/edge-tts) |
| **`ffmpeg`** | **CLI** | Umschnitt, Muxen, Codec, `yum install ffmpeg` — [ffmpeg.org](https://ffmpeg.org) |

**Wichtig zu wissen:**
- **Lokal TTS:** `piper` oder `Kokoro` = schnell, deutsch, keine Cloud.
- **Lokal STT:** `whisper.cpp` (CLI) oder `faster-whisper` (Python, in `my_tools.py` ideal).
- **`ffmpeg`** ist die Basis für alles Audio- und Video-Verarbeitung (Schnitt, Tonspur-Mux, Transcoding).
- **Edge-TTS** = praktisch, aber Microsoft-Cloud (keine echte Self-Hosting-Garantie).

**Offene Entscheidungen (Rolf):**
- Deutsch-Voice bevorzugt? (piper/Kokoro beide ja)
- STT für was genau: eigene Sprachnotizen, Kommentare, oder beides?
- `whisper.cpp` (CLI) vs. `faster-whisper` (Python-lib)?

---

### 10. Video (Erstellung / Export)
**Zweck:** Kurze Clips (Reels/TikTok/Shorts) aus Bildern, Audio, Text-Einblendungen zusammensetzen — optional aus generierten Bildern + TTS.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **`MoviePy`** (v2.x) | **lib** | Python, `ImageClip` + `AudioFileClip` + `TextClip`, [zulko/moviepy](https://github.com/Zulko/moviepy) |
| **`ffmpeg`** | **CLI** | Robust, `filter_complex`, `concat`, `xfade`, [ffmpeg.org](https://ffmpeg.org) |
| **`Remotion`** | **Full Framework** | React-basiert, programmatische Videos, Node.js — [remotion.dev/docs](https://www.remotion.dev/docs) |
| **`Kdenlive`** (GUI) | **App** | Manuell, mächtiger NLE, Projekt-Dateien (`.mlt`) sind JSON/XML → skriptbar; OpenShot-Äquivalent `.json` — [kdenlive.org](https://kdenlive.org) · [openshot.org](https://www.openshot.org) |
| **`stable-diffusion.cpp` (SVD)** | **lib** | Stable Video Diffusion — kurze **Video-Gen** (lokal), [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) |
| **`diffusers` (CogVideoX / LTX-Video)** | **lib** | Text→Video (lokal, VRAM-hungrig), [huggingface.co/docs/diffusers](https://huggingface.co/docs/diffusers) |
| **Pexels Videos API** | **API** | Free stock video (200 req/h), [pexels.com/api/documentation/videos](https://www.pexels.com/api/documentation/#videos) |
| **Coverr / Mixkit** | **Saas/Stock** | Free stock video, [coverr.co](https://coverr.co), [mixkit.co](https://mixkit.co) |

**Wichtig zu wissen:**
- **`MoviePy`** = schnellster Pfad für "Bilder + Ton → 15s-Clip" (ideal für den automatisierten Flow).
- **`Remotion`** = mächtiger (React/JS), aber Node-Stack (unsere Umgebung ist Python-first).
- **Text→Video (SVD/CogVideoX)** = experimentell, VRAM-hungrig, Qualität variiert — **erst wenn Bild-Gen läuft**.
- **`ffmpeg`** macht das Muxen/Schneiden; `MoviePy` ist ein dünner Wrapper drum.

**Offene Entscheidungen (Rolf):**
- Video-Ziel: Kurz-Clips (15-60s) für Reels/Shorts? Oder längere Formate?
- Bild-sequenz (Slide-Show) oder echte Video-Gen?
- `MoviePy` (Python, einfach) oder `ffmpeg` (CLI, mächtig)?

---

### 11. Screenshots / Mockups (Webseiten, UI, Device-Preview)
**Zweck:** Websites/Apps als Bild (Browser-View, Mobile, Tablet) rendern — für Portfolio, Social-Media-Preview, "schau meine neue Seite" Content.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **Selma `browser`-Tool** (Playwright) | **Integriert** | `action=screenshot`, `viewport` setzen, `wait_for`, [playwright.dev/python](https://playwright.dev/python) |
| **`Playwright` (Python, direkt)** | **lib** | `page.screenshot()`, `devices`, `full_page=True` — [playwright.dev/python](https://playwright.dev/python) |
| **`puppeteer` (Node)** | **lib** | JS/Node, `screenshot`, [pptr.dev](https://pptr.dev) |
| **`ShotStack`** (SaaS) | **API** | URL→Screenshot (Cloud), [shotstack.io/docs](https://shotstack.io/docs/) |
| **`Pillow` (PIL)** | **lib** | Bild-Compositing (Text, Logo, Frame), `pip install pillow` — [python-pillow.readthedocs.io](https://pillow.readthedocs.io) |
| **`ImageMagick`** | **CLI** | `convert`, `montage` (Grid/Mockup), [imagemagick.org](https://www.imagemagick.org) |
| **`Pexels`/`Unsplash` Background** | **lib** | Vollbild-Hintergrund + Screenshot-Overlay (Mockup-Style), vgl. Tool 7 |

**Wichtig zu wissen:**
- **Selma `browser`-Tool** kann das **schon** (Screenshot-Action) — Erweiterung: `viewport`/`device` + `full_page` Parameter, dann ist Tool #11 fast "gratis".
- **`Pillow`** = für Text-Einblendungen, Wasserzeichen, Compositing (z. B. Screenshot + "Schau mal!"-Caption).
- **`ImageMagick`** = wenn mehrere Bilder zu einem Grid/Mockup.
- **Keine API nötig** — alles lokal.

**Offene Entscheidungen (Rolf):**
- Soll das `browser`-Tool um `device`/`full_page`/`scale`-Params erweitert werden (kleiner Aufwand)?
- Soll das Tool ein "Mockup-Modus" haben (Hintergrund + Screenshot + Text)?

---

### 12. Link-Prüfung / TLD-Tools
**Zweck:** URLs prüfen (Erreichbarkeit, Redirects, Status), Domain-Verfügbarkeit (TLD-Freigegeben), Link-Kurz-Links erzeugen, TLD/Domain-Metadaten lesen.

**Schnittstellen / Frameworks:**
| Option | Typ | Anmerkung |
|---|---|---|
| **`httpx` (Python)** | **lib** | HTTP-Client, `HEAD`/`GET`, Timeout, Headers, [encode/httpx](https://www.python-httpx.org) |
| **`tldextract`** | **lib** | `tldextract` für TLD/Registrierungs-Domain-Extraktion, `pip install tldextract` — [tldextract.readthedocs.io](https://tldextract.readthedocs.io) |
| **`rdappy`** (Python) | **lib** | RDAP (Registry Data Access Protocol) für Domain-Lookups, `pip install rdappy` — [rdappy (pypi)](https://pypi.org/project/rdappy/) |
| **`python-whois`** | **lib** | WHOIS (legacy), `pip install python-whois`, pure Python — [joepie91/python-whois](https://github.com/joepie91/python-whois) |
| **`whois` (CLI)** | **CLI** | `yum install whois`, direkt, `whois domain.com` |
| **Domainr API** | **API** | Free Tier (1-Check/Monat? 25/Monat), Domain-Verfügbarkeit, [domainr.com/api](https://domainr.com/api) |
| **InstantDomainSearch API** | **API** | Free, `?part_of=...`, [instantdomainsearch.com/developer](https://instantdomainsearch.com/developer) |
| **`is.gd` / `t.ly`** | **API** | Link-Kurz-Links (frei), [is.gd/create.php](https://is.gd/create.php) · [t.ly](https://t.ly) |
| **`urlwatch`** | **CLI** | URL-Monitoring (Inhalts-/Status-Änderungen), `pip install urlwatch` — [thp/urlwatch](https://github.com/thp/urlwatch) |

**Wichtig zu wissen:**
- **`httpx`** + **`tldextract`** = Core: URL prüfen, TLD identifizieren.
- **RDAP/WHOIS** = Domain-Daten (Status, Registrar, Expiry) lesen.
- **Link-Kurzer** = nur wenn man den Link teilen will (Social/QR).
- **Keine Cloud-Abhängigkeit nötig** — alle Tools sind lokal oder Free-API.

**Offene Entscheidungen (Rolf):**
- Link-Prüfung: nur Status-Code (erreichbar?) oder auch Redirect/Title/Meta (tiefer)?
- Kurze-Link-API: welche (is.gd, t.ly) oder gar keine?
- TLD-Such-Tool (Domain-Verfügbarkeit pro TLD)?

---

## Offene Entscheidungen (Rolf, zusammengefasst)

1. **Plattformen zuerst:** Welche Social-Plattformen soll der Werkzeugkasten zuerst bedienen? (Mastodon/Pixelfed = offen & selbst-hostbar; Instagram/X/LinkedIn/TikTok = Antrags-Puffer)
2. **E-Mail:** Selbst (Mailcow/Fastmail) vs. SaaS (Resend/Mailgun) vs. Disposable (Temp-mail)?
3. **Bild-Gen:** Lokal (sd.cpp / Unsloth / diffusers) oder Cloud-API (Replicate / fal.ai)? → hängt von der **Hardware (VRAM)** ab.
4. **Audio:** `piper`/Kokoro (TTS) + `whisper.cpp`/`faster-whisper` (STT)? Deutsch?
5. **Video:** `MoviePy` (Python, Bild+Aud→Clip) oder `ffmpeg` (CLI)? Echte Text→Video-Gen (SVD/CogVideoX) erst später?
6. **Screenshot/Mockup:** `browser`-Tool erweitern (device/full_page) oder `Pillow`-Compositing als separaten Schritt?
7. **Link-Prüfung:** `httpx` + RDAP (Status) reicht, oder auch Kurze-Link-Anbieter + TLD-Suche?
8. **Generell:** Welche dieser 12 Tools soll **erst umgesetzt** werden (in `src/selma/my_tools.py`)? (Spätere Runde, Rolf bestimmt Reihenfolge.)
