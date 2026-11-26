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

Jarvis ir Secretary dar turi telefono įrankius: `phone_call` (skambutis balsu), `send_sms`, `schedule_task`
(suplanuoti skambutį, SMS, pranešimą ar užduotį sau, vienkartinę ar kartotinę), `list_scheduled`, `cancel_scheduled`.
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
            │      │  notify_user, phone_call, send_sms
            │      │  schedule_task …
            └──────┘
 kronos serve (foninis): rutinos + suplanuoti skambučiai → Twilio → jūsų telefonas
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

## Kasdienis režimas: `kronos serve`, skambučiai ir priminimai

Jarvis veikia ir be jūsų prie terminalo. Foninis procesas `kronos serve` kas 15 s tikrina, kas pribrendo:

- **Rutinos** iš `routines.yaml`: pvz. 07:30 darbo dienomis Jarvis parengia rytinį brifingą (užduotys,
  orai, viena naujiena, vakarienė) ir **paskambina** jums bei perskaito jį balsu. 21:00 vakaro apžvalga
  ateina kaip pranešimas. Rutinas redaguojate YAML'e: laikas, dienos, pristatymo kanalas
  (`call`, `sms`, `notify`, `none`) ir užduoties tekstas.
- **Agento suplanuoti dalykai**: pokalbyje pasakius „paskambink man rytoj 7:00 ir primink apie vizitą“
  arba „kiekvieną rytą 8:00 patikrink orus ir paskambink“, Jarvis įrašo tai per `schedule_task`, o
  daemonas nurodytu laiku paskambina, išsiunčia SMS, parodo pranešimą ar pats atlieka užduotį ir
  pristato rezultatą. `kronos schedule` arba `/schedule` parodo sąrašą, Jarvis gali atšaukti pagal id.

Skambučiai ir SMS eina per **Twilio** (vienpusis balso skambutis su teksto įgarsinimu, lietuviškas
Google balsas). `.env`:

```
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+370...      # jūsų Twilio numeris
USER_PHONE_NUMBER=+3706...      # kur skambinti
KRONOS_QUIET_HOURS=22:30-07:00  # ne skubūs skambučiai šiuo metu atmetami, Jarvis perkelia juos rytui
```

Paleidimas:

```sh
uv run kronos serve                       # terminale
# arba nuolat: Linux - deploy/kronos.service (systemd --user), macOS - deploy/com.kronos.daemon.plist
```

Daemono sesijose niekas nestebi, todėl įrankiai, kuriems reikėtų patvirtinimo, atmetami, o `local_shell`
išjungtas, nebent `KRONOS_SHELL_POLICY=allow`. Žurnalai: `~/.kronos/logs/`. Rutina, praleista daugiau nei
3 val. (kompiuteris buvo išjungtas), tą dieną nebevykdoma.

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
routines.yaml      kasdienės rutinos daemonui
deploy/            systemd / launchd pavyzdžiai daemonui
environments/      sandbox aplinka
memory_stores/     ilgalaikė atmintis
vaults/            kredencialų konteineris (be paslapčių)
src/kronos/
  cli.py           komanda `kronos`, interaktyvus ciklas
  session.py       sesijos kūrimas/tęsimas, įvykių srautas, workflow'ų ir gijų sekimas
  daemon.py        `kronos serve`: rutinos ir suplanuoti skambučiai/priminimai
  schedule.py      planuoklio saugykla (~/.kronos/schedule.json), rutinų skaitymas
  tools/           lokaliai vykdomi įrankiai: home_assistant, local_shell, notify, phone (Twilio), scheduling
  config.py        ID iš claude-lock.json / aplinkos kintamųjų
  ui.py            terminalo išvestis (rich)
tests/
```
