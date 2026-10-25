# PwMngt – projektkontekst for Claude

Udviklere: **Kasper** (erfaren C#/ASP.NET-udvikler og løsningsarkitekt – forklar gerne Python-/HA-særheder kort) og **Claus** (ejer repoet og er manden bag de oprindelige Node-RED-automationer). Begge skriver dansk – svar på dansk. Spørg, hvem du taler med, hvis det ikke fremgår.

## Samarbejde (Kasper og Claus – hver sin Claude-session)
- Repo: **`Irming1109/PwMngt`** (https://github.com/Irming1109/PwMngt) – ejet af Claus (`Irming1109`); Kasper (`repzak`) er collaborator med push-adgang.
- **Begge koder, committer og pusher** – kode, denne fil, skills og hooks. Der er ingen tester-rolle som i Billboard.
- Arbejd altid ovenpå den andens seneste: `git pull --autostash` **før** en opgave startes og igen **før** push. Små, afgrænsede commits; push ikke halvfærdigt arbejde, der kan bryde den andens HA.
- Rør ikke den andens igangværende, ikke-committede filer – commit kun de filer, din egen opgave har ændret (aldrig `git add -A` blindt).
- Commit kun, når den du taler med, beder om det. Commit-beskeder på dansk (som de seneste), kort hvad og hvorfor, og versionen i parentes ved et versionsbump, fx `Ret seed-felter der blev ignoreret (0.3.37)`. **Alle commits har så vidt muligt et issue-nummer**: emnelinjen starter med nummeret og beskeden har `Refs #<nr>` – se **Opgaver og backlog**.
- **Hver har sin egen Home Assistant.** Kasper: `https://homeassistant.karacore.dk`. Claus har sin egen installation, som Kaspers Claude ikke kan nå. Claus's Node-RED er referencen for logikken (se **Node-RED-referencen**).
- **Denne fil er fælles viden og styres af GitHub.** Begge må rette i den – hold den kort og sand, og commit rettelsen sammen med (eller lige efter) den ændring, den beskriver. Personlige ting (egen maskine, stier, HA-adresse, browser, arbejdsvaner) lægges i `CLAUDE.local.md` i projektmappen – den læses også automatisk, men er git-ignoreret. Claudes hukommelse (memory) er personlig og bruges ikke til projektviden.
- **Automatisk opdatering:** `.claude/settings.json` kører ved hver sessionsstart `.claude/hooks/sync-shared-files.mjs` (Node – virker på Windows og Mac). Den henter `origin/main` og:
  - **Henter selv al koden** (`git merge --ff-only`), når det er sikkert: du står på `main`, har ingen upushede commits og ingen ikke-committede ændringer i sporede filer (kopier af fælles filer fra en tidligere kørsel tæller ikke). Så starter hver session på den nyeste kode.
  - Ellers kopierer den **kun** de fælles Claude-filer (`CLAUDE.md`, `.claude/skills`, `.claude/hooks`, `.claude/settings.json`, `.github/ISSUE_TEMPLATE`), som GitHub har ændret, og som ikke har lokale ændringer, og melder, hvor mange commits koden mangler – hent den med `git pull --autostash`, når dit eget arbejde tillader det.
  - Har en fælles fil egne lokale ændringer, opdateres den ikke, og hooken siger det – commit og push dine ændringer (efter pull), eller kassér dem (`git checkout HEAD -- <fil>`).
  - Claude skal så læse de opdaterede filer (CLAUDE.md først) og kort fortælle, hvad der er nyt.
  - Kør én gang `git config rebase.autoStash true` i din klon, så et almindeligt `git pull` (også fra Visual Studio) virker som `git pull --autostash`.

## Formål
PwMngt ("Power Management", domæne `pwmngt`) er en **Home Assistant custom integration** installeret via **HACS**. Den er en **native genimplementering af Claus's (og Kaspers) Node-RED-automationer** til et solcelleanlæg: PV-produktion, batteri, forbrug og spotpris – og efterhånden EV-lader, pool og PV-overskud.

**Status:** Solar PV Plant-delen er i drift på begge installationer (version i `custom_components/pwmngt/manifest.json`). EV-lader, Pool og PV Surplus er stadig scaffolding. Ændringer rammer rigtige installationer – særligt alt der skriver `battery_command` eller senere styrer inverteren.

## Krav
- **Al kode er på engelsk:** navne, kommentarer, docstrings, log-beskeder. Kun denne fil, `BACKLOG.md`, commit-beskeder og chatten er på dansk.
- **Porter fra Node-RED, opfind ikke.** Find altid Claus's funktion/node i den nyeste eksport, før logik bygges eller ændres, og skriv i modulets docstring hvilken Node-RED-funktion (og tab) det er en port af, plus en tabel `Node-RED sensor → PwMngt` over de sensorer, det erstatter (se `data/solar_data.py`, `data/battery_data.py`).
- **Ingen hårdkodede entity_ids fra en bestemt installation.** Brugerens egne entities mappes i options-wizarden (`config_flow.py`), og auto-detect matcher på `(platform, translation_key)` – aldrig på entity_id-strengen (de omdøbes ikke, når et device omdøbes).
- **Alle brugervendte tekster går gennem HA's sprog-framework** (wizard, entity-navne, fejl, Repairs): `strings.json` + `translations/*.json` – aldrig hårdkodet i koden, så flere sprog nemt kan tilføjes senere. Ét sprog er nok for nu; en ny nøgle skal mindst i `strings.json` og `translations/en.json` (HA's grundsprog – `precheck.py` tjekker, at de matcher), og gerne i `da.json`.
- **Rør aldrig en live Home Assistant ud over det, opgaven eksplicit kræver** (se **Home Assistant – sikkerhedsregler**).
- Læsevenlighed frem for få linjer; DRY. Kommentarer forklarer *hvorfor* (Claus's begrundelse, HA-særheder) – ikke historik som "flyttet hertil fra …".

## Stak og struktur
Python, Home Assistant ≥ 2025.3 (`hacs.json`), ingen eksterne pakker ud over `requests`. Afhænger (uden formel dependency – mangler de, oprettes en HA Repair, `_async_check_dependencies` i `__init__.py`) af **Solcast Solar** (`solcast_solar`) og **Strømligning** (`stromligning`). Ladere: Wallbox/Easee (valgfrit). `single_config_entry: true`.

`custom_components/pwmngt/`:
- `__init__.py` – opsætning, platforme, dependency-tjek, registrering af frontend-kortene (`/pwmngt_frontend/pwm-cards.js`).
- `const.py` – alle konstanter: `DOMAIN`, `PLATFORMS` (sensor, number, select, button, text), `SEGMENT_*`, entity-map-felter, auto-detect-navne for invertere/ladere.
- `config_flow.py` – opsætning + options-wizard. Sider: `init` (valgfrie segmenter) → `pv_inverter` (inverter-mærke) → `solar_pv_plant` (entity-map, obligatorisk) → `ev_charging` / `pool` / `pv_surplus`.
- `devices.py` – **eneste sted** der definerer HA-devices: `hub_device_info` ("PwM"), `pv_device_info` ("PwM PV"), `charger_device_info` ("PwM Charger1/2", `CHARGERS`) – alle nestet under hubben via `via_device`. Nye devices tilføjes her.
- Platforme: `sensor.py`, `number.py`, `select.py`, `button.py`, `text.py`; fælles basis i `base.py`; `services.py`.
- `data/` – **"_data"-sensorer**: hver fil har en ren `calculate_*`-funktion (ingen HA-state) + en `SensorEntity`, der kobler en trigger (timer/event) til den og skriver resultatet ud. Fx `balance_data` (hub, 10 s), `solar_data` (PV-prognose + battery nightly target), `battery_data` (min-SoC + batteriregnskab), `battery_charge_decision` ("Skal batteri lades"), `battery_charge_level` ("Bestem ladeeffekt"), `battery_sell_decision` ("Beregning ny" – blokér ladning ved salg), `consumption_data` (rullende forbrugsgennemsnit fra Recorder), `pv_shift_data`, `savings_data`.
- `properties/` – read-only accessors pr. device (`pv_properties.py`, `hub_properties.py`), der skjuler entity-registry-opslag og state-parsing bag ét sikkert kald.
- `helpers/` – statiske hjælpefunktioner, ét emne pr. fil (`state_helper`, `history_helper`, `statistics_helper`). Long-term statistics bruges frem for state history, fordi Recorder sletter history efter `purge_keep_days` (10).
- `www/pwm-cards.js` – egne Lovelace-kort (`pwm-hub-card`, `pwm-charger-card`, `pwm-pv-card`, `pwm-electricity-card`), ren JS uden build. Felterne spejler Python-beskrivelserne manuelt – ændres et config-felt i Python, skal `fields`-arrayet her rettes også.

**Device vs. segment:** et *device* er en HA-device-indgang fra `devices.py`; et *segment* er en wizard-side. De er ikke 1:1 (EV Charging → 2 devices, Pool/PV Surplus → 0 i dag). En fils device afgøres af det bogstavelige `*_device_info(`-kald i filen – intet andet.

**Entities:** diagnostiske/interne sensorer (fx Solar data, Savings data, Consumption data) ligger under *Diagnostisk* (`EntityCategory.DIAGNOSTIC`). Ikke-konfigurerede kilder viser 0 frem for at fejle (fx PV1/2/3).

## Node-RED-referencen
- Kilden til "hvordan gør den gamle automation" er **eksporterede flow-filer** – aldrig live Node-RED (Claus's kan ikke nås, og Kaspers live-editor må ikke bruges til research). Se skillen **`pwmngt-nodered-reference`**.
- Kasper: `C:\Temp\CurrentHA\NodeRed\All tabs - all flows <Person> <DD-MM-YYYY>.json` (+ `C:\Temp\CurrentHA\homeassistant\` med YAML). Claus angiver sin egen sti i `CLAUDE.local.md`.
- Brug altid **nyeste fil pr. person efter datoen i filnavnet** (parse DD-MM-YYYY – ikke leksikalsk sortering); ældre filer kun til at sammenligne. **Claus's version er referencen.** Søg i `func`-teksten med Python, ikke på node-navne, og tjek om tab/node er disabled ("Skraldespand", "backup").

## Kommandoer
- Syntakstjek: `python -m py_compile custom_components/pwmngt/*.py custom_components/pwmngt/*/*.py` (Git Bash) (der er ingen testsuite – test sker i HA). Hele tjekket før en release: `python -I .claude/skills/pwmngt-deploy/precheck.py`.
- Release: `.\release.ps1` fra repo-roden (læser version fra `manifest.json`, laver annoteret tag `vX.Y.Z` fra `RELEASE_NOTES.md`, pusher kun tagget; `.github/workflows/release.yml` opretter GitHub Release som "Latest"). Se skillen **`pwmngt-deploy`**.
- GitHub CLI: `C:\Program Files\GitHub CLI\gh.exe` (på Kaspers maskine).

## Arbejdsgang (hver opgave)
1. `git pull --autostash`. Kommer opgaven fra et issue: tildel dig selv og sæt `in-progress` (skillen `github-issue`). Læs relevante filer og den tilsvarende Node-RED-funktion; stil afklarende spørgsmål ved uklarheder – **altid med din anbefaling** til hvert spørgsmål, så der kan svares "anbefalingerne er ok".
2. Byg ændringen. Nye config-felter: `const.py` + `config_flow.py` + platformfil + `strings.json`/`translations/*.json` + evt. `www/pwm-cards.js`.
3. Bump `version` i `manifest.json` ved ændringer, der skal ud (format `0.4.03` – patch altid to cifre).
4. `py_compile` uden fejl. Opdatér denne fil, hvis arkitekturen ændres. Nye fund undervejs oprettes som issues – ikke som noter her.
5. Commit når der bedes om det, `git pull --autostash`, push. Sæt issuet til `in-test` med en kort kommentar (commits, version, hvad der skal tjekkes i HA). Deploy er et separat skridt ("Deploy").

## Version og deploy
- Versionen står i `custom_components/pwmngt/manifest.json` og tagges `v<version>`. Patch er zero-padded til to cifre (`0.4.01`, ikke `0.4.1`).
- `RELEASE_NOTES.md` er git-ignoreret kladde – indholdet bliver tag-beskeden og release-teksten. `release.ps1` tagger med `--cleanup=verbatim`, ellers forsvinder Markdown-overskrifter (`##`).
- `release.yml` henter tagget igen efter checkout (ellers mistes annotationen, og release-teksten bliver commit-beskeden) og springer over, hvis releasen allerede findes.
- HACS: releases siden v0.2.24 er "Latest" (ikke pre-release), så nye installationer får den nyeste version.

## Home Assistant – sikkerhedsregler
- Live HA er i drift. **Kun læsning**: screenshots, Developer Tools → Template, read-only JS mod `document.querySelector('home-assistant').hass` (states – aldrig service calls), almindelig navigation.
- **Aldrig** Reload, Restart, Redownload, Remove, slette entities eller ændre indstillinger. Installation af en opdatering og genstart af HA gør ejeren selv.
- Eneste stående undtagelse (Kaspers HA): HACS-repositoriets side → ⋮ → **"Update information"** under deploy (skillen `pwmngt-deploy`).
- Brug **Claude in Chrome** (ejerens egen, loggede-ind browser) til HA – den indbyggede browser er ikke logget ind. Log aldrig selv ind; er browseren ikke logget ind, så sig det.

## Skills (`.claude/skills/`)
- **`pwmngt-deploy`** – når der siges "Deploy": tjek før release (`precheck.py`), evt. versionsbump, release-noter med issues, ét samlet "ja", tag og release, issues får en kommentar og sættes til `in-test`, HACS "Update information" (kun Kasper).
- **`pwmngt-nodered-reference`** – opslag i Node-RED-eksporterne.
- **`github-issue`** – opret/opdatér issues og felterne på boardet.
- **`pwmngt-visual-docs`** – den interaktive drill-down-dokumentation (`docs/visual-documentation.html`); kun når der bedes om det, aldrig som del af deploy.

## Opgaver og backlog – GitHub Issues
Alle opgaver, fejl og ønsker ligger som **GitHub Issues** i `Irming1109/PwMngt` og på projekt-boardet **"Power Management"** (https://github.com/users/Irming1109/projects/6) – det er den eneste opgaveliste. `BACKLOG.md` blev flyttet dertil 25. okt. 2026 (#1–#7) og er kun arkiv.
- Issues oprettes og opdateres efter skillen **`github-issue`** – samme opbygning og farver som Billboards board. Formularer til github.com: `.github/ISSUE_TEMPLATE/`.
- **Ingen labels** – alt er felter på boardet: **Issue type** (`bug`/`feature`), **Område** (`solar`, `battery`, `consumption`, `savings`, `ev`, `pool`, `pvsurplus`, `heatpump`, `wizard`, `cards`, `deploy`, `docs`), **Status** (`needs-triage` → `ready-for-dev` → `in-progress` → `in-test` → `done`), **Prio** (`prio-1` vigtigst … `prio-4` lav; `prio-5` = out of scope lige nu) og **Spor** (`Udvikling` = kode, `Data` = uden kode, `Beslutning` = aftales mellem Kasper og Claus).
- **Tildeling = hvem har bolden:** et issue i `ready-for-dev` må være utildelt; den der går i gang, tildeler sig selv; `in-test` tildeles den, hvis HA det testes på; `Beslutning` tildeles begge.
- **Alle commits skal så vidt muligt høre til et issue.** Emnelinjen **starter med nummeret** – `#2 Port Automatik Styring (0.4.04)`, flere: `#2 #5 …` – og beskeden har `Refs #<nr>` (ikke `Fixes`, som lukker før test). Så kobles committet til issuet på GitHub, og deploy (`precheck.py`) finder de issues, en release indeholder.
  - Findes der intet issue til en ændring i koden, så find et passende (`gh issue list --repo Irming1109/PwMngt --search "<ord>"`) eller foreslå at oprette ét (skillen `github-issue`), før der committes – også for små rettelser, der skal ud til brugerne.
  - Uden nummer kun når et issue ikke giver mening: rene ændringer af denne fil, skills, hooks og dokumentation, versionsbump (`Bump version til 0.4.04`) og oprydning uden betydning for brugerne. Er du i tvivl, så spørg. Git sletter linjer, der starter med `#`, når beskeden skrives i en editor – commit med `-m`/`-F`, eller kør én gang `git config commit.cleanup scissors` i klonen. Issuet lukkes, når det er testet.

## Historik (kort)
- **Sep. 2026:** Projektet startet af Kasper i Claude CoWork (v0.1–0.3): hub, Solar PV Plant-wizard med auto-detect, forbrugsmåling fra Recorder (statistics frem for history), egne Lovelace-kort, visuel dokumentation, release-flow via `release.ps1` + GitHub Actions. 28.–30. sep.: forbrugsmodulerne slået sammen til `consumption_data.py`; `solar_data`/`battery_data` tilpasset Claus's Node-RED (PV-prognose 08–16 i stedet for 11–16; kumulerede afladningstællere fjernet).
- **14.–15. okt. 2026 (v0.3.24–0.3.35):** port af batteristyringen fra "Batteri styring NY": charge decision/level, battery mode gate, timevis gentjek af `battery_charge_status`, PV shift detection, battery sell decision (blokér ladning ved salg), savings tracking og konfigurerbart pristillæg.
- **16.–19. okt. 2026 (v0.3.36–0.4.03, primært Claus):** afgiftstrappe på indkøbspris uden PV, rettelser af seed-felter og headline-sensorer ved opstart, min_soc låst op til target (max 95), diagnostiske sensorer flyttet til *Diagnostisk*.
- **25. okt. 2026:** Flyttet fra Claude CoWork til Claude Code med fælles `CLAUDE.md`, skills og sync-hook (samme opsætning som Billboard). Backloggen flyttet til GitHub Issues og boardet "Power Management".
