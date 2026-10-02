# SOCIAL_MEDIA_TOOLS_TODO.md

**Kontext:** Social-Media-Experiment (geplanter getrennter Workspace, Stand 2026-09-27: Verzeichnis **noch nicht existiert**; Rolf legt es an, z. B. `/home/rolf/workspace/socialmedia`).
Rolf setzt die hier definierten Tools selbst als Selma-Tools um — "die stabilste Version".
Standpunkte: Email-API + TOTP. Erwartet werden weitere Tools (Abschnitt 4).
Angelegt: 2026-09-27. Rolf entscheidet Reihenfolge; nichts wird ungefragt angefasst.

## 1. email_api (E-Mail als Identität für Social-Media-Accounts)

Status: **TODO — Design offen** (Details stammen aus Rofs Vorschlagsrunde; hier nur der Rahmen bis Rolf sie bestätigt/ergänzt)

- [ ] Zweck: Adressen für Social-Media-Registrierungen erzeugen/empfangen/lesen
- [ ] Backend: API des Providers endgültig festlegen (Endpoint, Auth-Schema, Rate-Limits)
- [ ] Tool-Operationen (Vorschlag):
  - `email_address_create(alias?)` → neue Adresse aus Provider-Pool
  - `email_inbox_list(address, limit=20, since=?)` → neue Nachrichten
  - `email_read(address, message_id)` → Body (für Bestätigungslinks/Codes)
  - `email_send(address, to, subject, body)` — falls Sende-Fähigkeit im Experiment sinnvoll ist
- [ ] Credentials: Token per Env-Var (z. B. `EMAIL_API_TOKEN`), NICHT in selma.json/workspace-Dateien
- [ ] Rate-Limits: few-shot Writes, 429/Retry-After respektieren (Selma-Skill-Konvention)
- [ ] Test: gegen Fake-Provider im getrennten Workspace, KEIN Live-Versand im Test
- [ ] Offene Fragen an Rolf: Provider? Wie viele Adressen/Day? Sollen Adressen wiederverwendbar sein?

## 2. totp (2FA-Codes für Account-Registrierung/Verwaltung)

Status: **TODO — Design offen**

- [ ] Zweck: TOTP-Codes generieren, wenn Social-Media-Plattformen 2FA erzwingen
- [ ] Kern: `totp_now(secret)` → 6-stelliger Code (RFC 6238, SHA1, 30s) + `totp_uri(account, secret)` für Einmal-Setup
- [ ] Secret-Verwaltung: `~/.selma/totp-secrets.json` im getrennten Workspace (chmod 600), NICHT im Repository/Workspace-MD-Dateien
- [ ] Abhängigkeit: `pyotp` oder reine stdlib-Implementierung (Rolf entscheidet)
- [ ] Test: bekannte RFC-Testvektoren (RFC 4226/6238 Vektoren) als Unit-Tests im getrennten Workspace
- [ ] Offene Fragen an Rolf: Welche Plattformen? Einmal-Secrets oder wiederkehrende Konten?

## 3. Gemeinsame Konventionen

- [ ] Beide Tools registrieren im **getrennten Workspace** (`.selma/workspace/skills/` oder Tool-Registry dort) — NICHT im Selma-Repo
- `toolsAllow` in der neuen `selma.json` gezielt setzen (Liste statt "all"), sobald der Tool-Setz feststeht
- [ ] Credentials-Matrix: welche Env-Vars welche Tools brauchen → in `TOOLS.md` des neuen Workspaces dokumentieren
- [ ] Vor Produktiv-Test: `find ~/.selma -type f` + `ls <neuer_root>/.selma` zur Verifikation der Trennung

## 4. Erweiterungen (erwartet, aber NICHT geplant)

- [ ] Social-Media-Posting-Tool (Plattform-übergreifend)
- [ ] Session/Cookie-Handling für Web-Logins
- [ ] Screenshot-/Evidenz-Archiv (über `browser`-Tool?)
- [ ] Rate-Limit-/Cooldown-Tracker pro Plattform

## 5. Reihenfolge / nächste Schritte (offen für Rolf)

- [ ] Rolf: Provider/Scope der Email-API finalisieren (Abschnitt 1)
- [ ] Rolf: TOTP-Kontext finalisieren (Abschnitt 2)
- [ ] Separater Workspace aufsetzen (siehe Chat-Protokoll 2026-09-27)
- [ ] Danach: Tool 1 → testen → Tool 2

## 6. weitere Tools für Zugriffe & Accounts

1. Bildsuche: **Pexels- oder Unsplash-API** (kostenlos, Free-Tier reicht)
2. Postings/Queue: **Buffer oder Later API** (1 Konto reicht für Start)
3. Plattform-Metriken: **einer** der Graph APIs – Instagram, TikTok, LinkedIn, X (v2 = Free-Tier limitiert)
4. Design: **Canva API** (falls Rolf bereits dort Vorlagen hat)
5. Content-Pipeline (später): Bild-Generierung (Local/Stable Diffusion / GPTImage), **ElevenLabs** für Audio
