# Kronos – „Jarvis“ su keliais agentais

Asmeninis asistentas terminale, pastatytas ant **Claude Managed Agents**: Anthropic laiko agentų ciklą ir
sandbox konteinerį, o šiame kompiuteryje veikia tik plonas Python klientas. Vienas koordinatorius (**Jarvis**)
kalbasi su jumis ir dalija darbus penkiems specialistams, kurie dirba paraleliai atskirose gijose.

| Agentas | Modelis | Ką daro |
|---|---|---|
| **Jarvis** | Claude Opus 5.5 | Kalbasi su jumis, planuoja, deleguoja, tikrina, atsako. Gali pasileisti savo kopiją dideliam poprojekčiui. |
| Researcher | Claude Sonnet 5.5 | Interneto tyrimai su šaltiniais. |
| Coder | Claude Sonnet 5.5 | Kodas, skriptai, failai sandbox'e, vykdymas ir testavimas. |
| Secretary | Claude Sonnet 5.5 | Kalendorius, el. paštas (per MCP, kai prijungta), priminimai, užduočių sąrašas atmintyje. |
| Home | Claude Sonnet 5.5 | Namų ir kompiuterio valdymas per **jūsų** kompiuteryje vykdomus įrankius: Home Assistant, lokalus shell, pranešimai. |
| Chef | Claude Sonnet 5.5 | Maisto planavimas, receptai, pirkinių sąrašai pagal išsaugotus pomėgius. |

Visi agentai dalijasi **ilgalaike atmintimi** (memory store, prijungtas kaip `/mnt/memory/jarvis-memory/`),
tad Jarvis prisimena jūsų profilį, pomėgius, užduotis, namų įrenginius ir praeities meniu tarp sesijų.
Įjungti ir **dinaminiai workflow'ai**: kai darbas skyla į daug vienodų dalių (dešimtys šaltinių ar failų),
Jarvis paleidžia foninį vykdymą, kuriame daug agentų dirba fazėmis.

## Kaip tai veikia

```
 jūs ──► kronos CLI (šis kompiuteris) ──► Managed Agents sesija (Anthropic)
            ▲      │                          │
            │      │ custom tools:            ├─ Jarvis (koordinatorius, Opus 5.5)
            │      │  home_assistant          ├─ Researcher / Coder / Secretary / Home / Chef
            │      │  local_shell             └─ sandbox: bash, failai, web, /mnt/memory
            │      │  notify_user
            └──────┘
```

- **Valdymo plokštuma** (agentai, aplinka, atmintis, vault) aprašyta failuose `agents/`, `environments/`,
  `memory_stores/`, `vaults/` ir sinchronizuojama su `ant apply`. ID atsiduria `claude-lock.json`.
- **Duomenų plokštuma** (sesijos, žinutės, įvykių srautas) – Python klientas `src/kronos/`, naudojantis
  oficialų `anthropic` SDK.
- Įrankiai, kuriems reikia jūsų kompiuterio (Home Assistant, shell, pranešimai), aprašyti kaip *custom tools*:
  agentas juos kviečia, sesija sustoja, CLI įvykdo juos lokaliai ir grąžina rezultatą. Jūsų raktai niekada
  nepatenka į sandbox'ą.

## Paleidimas

### 1. Įrankiai

```sh
# Anthropic CLI (reikia 1.40.0 ar naujesnės)
brew install anthropics/tap/ant          # macOS
# Linux: https://github.com/anthropics/anthropic-cli/releases
ant auth login                           # arba export ANTHROPIC_API_KEY=sk-ant-...

# Python 3.13 ir uv
uv sync
```

### 2. Sukurti agentus, aplinką, atmintį ir vault

```sh
ant apply --dry-run -v agents/jarvis.md environments/kronos.yaml memory_stores/jarvis.yaml vaults/jarvis.yaml
ant apply agents/jarvis.md environments/kronos.yaml memory_stores/jarvis.yaml vaults/jarvis.yaml
```

`agents/jarvis.md` nurodo kitus penkis agentus pagal kelią, tad `ant apply` sukuria ir juos.
Komanda įrašo `claude-lock.json` – **commit'inkite jį**: kitas `ant apply` atnaujins tuos pačius
resursus, o ne sukurs dublikatus. Pakeitę bet kurį agento failą, paleiskite tą pačią komandą dar kartą.

### 3. Konfigūracija

```sh
cp .env.example .env     # ir užpildykite ką reikia
uv run kronos --check    # parodo, ką klientas išsprendė
```

### 4. Pokalbis

```sh
uv run kronos                      # interaktyvus režimas, tęsia paskutinę sesiją
uv run kronos --new                # nauja sesija
uv run kronos -v                   # rodyti visus agentų įrankių kvietimus
uv run kronos "Kas šiandien darbotvarkėje?"   # viena žinutė ir išeiti
```

Komandos pokalbyje: `/new`, `/status` (kaina, biudžetas), `/threads` (kurie agentai dirbo), `/url`
(nuoroda į Console sesijos peržiūrą), `/help`, `/quit`. `Ctrl+C` užduoties metu nutraukia agentą.

## Namų valdymas (Home Assistant)

`.env`:

```
HA_URL=http://homeassistant.local:8123
HA_TOKEN=<ilgalaikis prieigos tokenas iš HA profilio>
```

Home agentas pats susiranda įrenginius (`list_entities`), tikrina būseną prieš ir po veiksmo ir įsimena
įrenginių pavadinimus atmintyje (`home/devices.md`). Durų atrakinimas, signalizacijos išjungimas ir panašūs
veiksmai vykdomi tik po jūsų aiškaus patvirtinimo pokalbyje.

`local_shell` komandos jūsų kompiuteryje pagal nutylėjimą prašo patvirtinimo terminale (`KRONOS_SHELL_POLICY=ask`);
`deny` išjungia įrankį, `allow` vykdo be klausimo (nerekomenduojama).

## Kalendorius ir el. paštas

Secretary agentas užduočių sąrašą tvarko atmintyje iš karto. Google Calendar / Gmail (ar kitą servisą su
nuotoliniu MCP serveriu) prijungiate taip:

1. `agents/secretary.md` priekyje atkomentuokite `mcp_servers` bloką ir `mcp_toolset` įrankį, įrašykite
   serverio URL.
2. Įdėkite OAuth prieigos raktą į vault (vault ID yra `claude-lock.json`):
   ```sh
   ant beta:vaults:credentials create --vault-id vlt_... <<'YAML'
   display_name: Google (asmeninis)
   auth:
     type: mcp_oauth
     mcp_server_url: https://<serveris>/mcp
     access_token: "<access token>"
     expires_at: "2026-12-31T00:00:00Z"
     refresh: {refresh_token: "<refresh token>", client_id: "<client id>", token_endpoint: "https://oauth2.googleapis.com/token", token_endpoint_auth: {type: none}}
   YAML
   ```
3. `ant apply agents/jarvis.md` (atnaujina ir Secretary).

Iki tol Secretary sąžiningai pasako, kad kalendorius neprijungtas, ir dirba su užduočių sąrašu.

## Kaina ir saugikliai

- Kiekviena sesija turi **kietą biudžetą** (`KRONOS_SESSION_BUDGET_USD`, pagal nutylėjimą 10 USD, skaičiuojant
  oficialiomis kainomis, įskaitant visus specialistus ir workflow'us). Pasiekus ribą sesija sustoja; `/new`
  pradeda naują. `off` išjungia ribą.
- Managed Agents yra **beta**; be žetonų apmokestinamas ir sesijos vykdymo laikas. Sesijų istorija lieka
  Console, kol jos neištrinamos.
- Sandbox tinklas aplinkoje `environments/kronos.yaml` yra `unrestricted`, nes Researcher ir Chef skaito
  bet kokias svetaines. Kaip jį susiaurinti, parašyta tame faile.
- Workflow'us galima išjungti `agents/jarvis.md`: `workflows: {type: disabled}`.

## Kūrimas

```sh
uv run pytest            # vienetiniai testai (konfigūracija, įrankiai, įvykių apdorojimas)
uv run kronos --check
```

Struktūra:

```
agents/            šeši agentų aprašai (YAML frontmatter + sisteminis promptas)
environments/      sandbox aplinka
memory_stores/     ilgalaikė atmintis
vaults/            kredencialų konteineris (be paslapčių)
src/kronos/
  cli.py           komanda `kronos`, interaktyvus ciklas
  session.py       sesijos kūrimas/tęsimas, įvykių srautas, workflow'ų ir gijų sekimas
  tools/           lokaliai vykdomi įrankiai: home_assistant, local_shell, notify
  config.py        ID iš claude-lock.json / aplinkos kintamųjų
  ui.py            terminalo išvestis (rich)
tests/
```
