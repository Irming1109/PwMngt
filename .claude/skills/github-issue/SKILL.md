---
name: github-issue
description: Opret, opdatér, flyt eller luk GitHub issues for PwMngt (Irming1109/PwMngt) efter den faste standard – fejl og ønsker med Issue type, Område, Status, Prio og Spor som felter på projekt-boardet "Power Management" (ingen labels). Brug den, når Kasper eller Claus vil melde en fejl, foreslå en funktion, oprette opgaver fra en liste, ændre et issues status/prioritet, eller når et issue er løst.
---

# GitHub issues for PwMngt

Fælles retningslinje for Kasper og Claus (og deres Claude-sessioner) – samme opbygning og farver som Billboards board, så de to projekter ser ens ud.

GitHub Issues er **den eneste opgaveliste** for PwMngt. `BACKLOG.md` bruges ikke længere til nye punkter (punkterne blev flyttet til #1–#7 den 25. okt. 2026).

## Forudsætninger

- Repo: `Irming1109/PwMngt`. Board: GitHub Project **"Power Management"** (nr. 6 under `Irming1109`): https://github.com/users/Irming1109/projects/6
- GitHub CLI logget ind med din egen konto (`gh auth status`) med scope `project` (`gh auth refresh -s project`).
- Windows: findes `gh` ikke på PATH, så kald `"C:\Program Files\GitHub CLI\gh.exe"`.
- Login udføres af personen selv i en terminal – Claude logger aldrig ind på brugerens vegne.

## Regler

1. **Sprog:** titel og tekst på dansk. Feltværdierne er som nedenfor. Kode, filnavne, entity_ids og Node-RED-navne citeres som de er.
2. **Fem ting tages der altid stilling til:** **Issue type**, **Område**, **Status**, **Prio** og **Spor** – præcis én værdi af hver, alle som felter på boardet. Repoet bruger **ingen labels**; brug aldrig `--label`.
3. **Tildeling = hvem har bolden nu.** Begge udvikler, så et issue i `ready-for-dev` må være utildelt (frit at tage). Den, der går i gang, tildeler sig selv og sætter `in-progress`. `in-test` tildeles den, hvis Home Assistant det skal testes på. `Beslutning` tildeles begge. Logins: Kasper `repzak`, Claus `Irming1109`.
4. Kasper, Claus og Claude må alle prioritere og sætte status ud fra, hvor vigtig opgaven er: `prio-1`–`prio-4` = in scope, `prio-5` = out of scope lige nu. En afhængig opgave har altid lavere prio end den, den venter på. Er prioriteten et gæt, så sig det.
5. **Ingen private data:** ingen adresser, HA-adresser/tokens, adgangskoder, serienumre eller skærmbilleder, der viser dem. Entity_ids må gerne citeres.
6. **Ingen dubletter:** søg først (`gh issue list --repo Irming1109/PwMngt --state all --search "<ord>"`). Findes et lignende issue, så foreslå at kommentere på det i stedet.
7. **Vis udkastet før oprettelse** (titel, de fem felter, evt. tildeling, brødtekst) og opret først efter et ja – medmindre brugeren allerede har bedt om at få dem oprettet. Ved mange issues: vis en tabel og opret samlet efter ét ja.
8. Brug kun værdierne nedenfor. Mangler et Område, så foreslå et nyt (små bogstaver, ét ord) og tilføj det først efter et ja – som ny værdi i feltet på boardet, her og i formularernes Område-dropdown (`.github/ISSUE_TEMPLATE/*.yml`).

## Felter på boardet

| Felt | Værdier |
|---|---|
| **Issue type** | `bug` (noget virker forkert – rød) / `feature` (ny funktion **eller** forbedring – blå) |
| **Område** | `solar` (Solar PV Plant: PV-produktion, prognose, inverter), `battery` (batteristyring: min_soc, ladning, salg, `battery_command`), `consumption` (forbrug og gennemsnit fra Recorder), `savings` (besparelser og priser), `ev` (EV-ladere, Wallbox/Easee), `pool`, `pvsurplus`, `heatpump` (jordvarmepumpe), `wizard` (opsætning og options-wizard, `config_flow.py`), `cards` (Lovelace-kort og dashboards), `deploy` (release, HACS, installation), `docs` (dokumentation, CLAUDE.md, visuel dokumentation). Ét område – vælg det vigtigste |
| **Status** | `needs-triage` (nyt – ikke vurderet, gul) → `ready-for-dev` (klar, grøn) → `in-progress` (nogen arbejder på det, blå) → `in-test` (lavet – skal testes i HA, lilla) → `done` (testet og færdig – eller droppet; issuet lukkes, droppede som "not planned" med en kort kommentar, grå) |
| **Prio** | `prio-1` (vigtigst, fx fejl der rammer en kørende installation eller styrer batteriet forkert – pink), `prio-2` (høj – orange), `prio-3` (normal – gul), `prio-4` (lav – grøn), `prio-5` (out of scope lige nu – grå) |
| **Spor** | `Udvikling` (kræver kode – også kun en del) / `Data` (uden kode: opsætning i HA, Node-RED-eksporter, test på en installation) / `Beslutning` (skal aftales mellem Kasper og Claus, før noget bygges) |

**Kode vinder:** kræver en opgave kode, ligger hele opgaven i `Udvikling`. **Ingen sub-issues:** dele står som tjekliste (`- [ ]`) i issuet; afhængigheder skrives som "Afhænger af #N" i den afhængige opgave.

**Automatik på boardet** (Workflows – som på Billboard): alle nye åbne issues i repoet lægges på boardet (*Auto-add to project*, `is:issue is:open`) og får Status `needs-triage` (*Item added to project*); et lukket issue får `done` (*Item closed*), og et issue sat til `done` lukkes (*Auto-close issue*). *Auto-add sub-issues* er slået fra (vi bruger ikke sub-issues). Issue type, Område, Prio og Spor sættes altid manuelt. Er det klart, at opgaven skal laves nu, sættes Status til `ready-for-dev`.

**Visninger på boardet** (som på Billboard – alle boards med Column by Status og Prio + Issue type vist på kortene): "Alle Issues" (`is:open`), "Kasper" (`assignee:repzak is:open`), "Claus" (`assignee:Irming1109 is:open`) og "Beslutninger" (`spor:Beslutning is:open`) – med Swimlanes by Spor og Sort by Prio – samt "Nylige ændringer" (`updated:>@today-2d`, uden swimlanes, nyeste først efter Updated). Et utildelt issue i `ready-for-dev` ses kun i "Alle Issues". GitHub har intet API til workflows og visninger – de ændres på github.com (ændringer i en visning gemmes først med "Save").

## Skabeloner for brødteksten

Samme felter som formularerne på github.com (`.github/ISSUE_TEMPLATE/`). Udelad tomme valgfrie afsnit.

**Fejl (`bug`):**

```markdown
**Hvor:** Kaspers HA · **Version:** 0.4.03 · **Inverter/lader:** Growatt / Wallbox

### Trin til at genskabe
1. …

### Forventet
…

### Faktisk
…  (gerne entity_id, state/attributter og log-linjer)

### Hvordan gør Node-RED?
…  (valgfri – funktion, tab og eksportens dato)

### Teknisk note
…  (valgfri – fil:linje, årsag, forslag)
```

**Ønske/forbedring (`feature`):**

```markdown
### Behov
Hvad mangler, og hvorfor?

### Forslag
…

### Acceptkriterier
- [ ] …

### Ikke med
…  (valgfri)

### Node-RED-modstykke
…  (valgfri – funktion og tab hos Claus)
```

Titel: kort og konkret om *hvad* (ikke "Fejl:"/"Ønske:" – det står i Issue type).

### Skærmbilleder
Gerne – fx af en entity, et dashboard eller wizarden (uden private data, regel 5). Gem dem som `issue-screenshots/<issue-nr>-<kort-navn>.jpg` i repoets rodmappe (git-ignoreret) og upload efter et ja (det er et push til grenen `issue-screenshots`, som ikke deler historik med `main`, så HACS aldrig får dem med):

```bash
node .claude/skills/github-issue/upload-screenshots.mjs issue-screenshots/12-wizard-felt.jpg [flere ...]
```

Scriptet udskriver Markdown-linjerne til afsnittet **Skærmbilleder**. Et navn, der allerede findes, afvises – brug et nyt.

## Kommandoer

```bash
# Opret (brødtekst fra fil – undgår problemer med anførselstegn og æøå)
gh issue create --repo Irming1109/PwMngt --title "…" --body-file body.md [--assignee repzak]

# Læg på boardet (returnerer kortets id med --format json)
gh project item-add 6 --owner Irming1109 --url <issue-url> --format json

# Kortets id for et eksisterende issue
gh project item-list 6 --owner Irming1109 --format json --limit 500 --jq '.items[] | select(.content.number == <nr>) | .id'

# Sæt et felt (id'erne står i tabellen herunder)
gh project item-edit --id <kortets-id> --project-id PVT_kwHOBJveEs4BooKR --field-id <felt-id> --single-select-option-id <værdi-id>
```

| Felt | Felt-id | Værdier (id) |
|---|---|---|
| Issue type | `PVTSSF_lAHOBJveEs4BooKRzhnHdPw` | bug `22d6f912`, feature `643b93bf` |
| Område | `PVTSSF_lAHOBJveEs4BooKRzhnHdQE` | solar `dbb6722f`, battery `e229d075`, consumption `383cc229`, savings `cd9dd2da`, ev `2c1b7c2b`, pool `f54effea`, pvsurplus `e6e9015e`, heatpump `6bb5fc3d`, wizard `ca77c245`, cards `b56eb2e8`, deploy `015fbd7d`, docs `a8661194` |
| Status | `PVTSSF_lAHOBJveEs4BooKRzhnHXSQ` | needs-triage `841440c0`, ready-for-dev `1f8b5583`, in-progress `e5c1b28e`, in-test `d39d229c`, done `ee89b4d8` |
| Prio | `PVTSSF_lAHOBJveEs4BooKRzhnHdP0` | prio-1 `32359de5`, prio-2 `947a1a2c`, prio-3 `4df581a8`, prio-4 `0fc2ea5a`, prio-5 `6eb53e2f` |
| Spor | `PVTSSF_lAHOBJveEs4BooKRzhnHdP4` | Udvikling `0a530f62`, Data `b2454e03`, Beslutning `df8d2d52` |

Projektets id er `PVT_kwHOBJveEs4BooKR`. Ændres felterne på boardet, ændres id'erne – find dem igen med `gh project field-list 6 --owner Irming1109 --format json`.

## Arbejdsgang

**Oprette:** forstå behovet → spørg ind til det manglende → søg efter dubletter → foreslå de fem felter → vis udkast → opret efter ja → læg på boardet og sæt felterne → giv linket.

**Alle commits skal så vidt muligt have et issue-nummer** (se CLAUDE.md, **Opgaver og backlog**) – mangler der et issue til en kodeændring, så find eller foreslå ét, før der committes.

**Når Claude laver et issue:** tildel personen, sæt `in-progress` → (arbejde) → commit, når der bedes om det, med **nummeret først i emnelinjen** (`#2 Port Automatik Styring (0.4.04)`) og `Refs #<nr>` i beskeden – ikke `Fixes`/`Closes`, som lukker ved push før test. Giv beskeden med `-m`/`-F` (git sletter ellers linjer, der starter med `#` – eller sæt `git config commit.cleanup scissors` i klonen) → sæt `in-test`, tildel den, hvis HA det testes på, og skriv en kort kommentar: hvilke commits og hvilken version, hvad er lavet, og hvad der skal tjekkes i HA.

**Når det er testet:** luk issuet (`gh issue close <nr> --repo Irming1109/PwMngt`). Fejler testen: tilbage til `ready-for-dev`/`in-progress` med en kommentar om, hvad der ikke virkede.
