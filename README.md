# BountyOS

En lokal webbapp som samlar bounties, fördelar dokumentationsarbete mellan agenter och följer leveranser och betalningar. Dashboarden är på svenska. SQLite sparar arbetskön, inställningar och historik mellan omstarter.

**Budgeten är 0 kr i betalda AI-anrop och köpta tjänster.** Den här versionen använder en lokal Ollama-modell och innehåller ingen anslutning till betalda modell-API:er. Datorn och modellen måste vara igång för att agenterna ska arbeta. Molnmiljön för denna utvecklingssession är inte en garanterad server som kör dygnet runt.

## Starta

### Windows med 16 GB RAM

1. Installera [Python för Windows](https://www.python.org/downloads/windows/) (64-bitars, version 3.11 eller senare), med Python-launchern och **Add Python to PATH** om installationsprogrammet visar det valet. Installera även [Ollama för Windows](https://ollama.com/download/windows).
2. Packa upp hela ZIP-filen till en kort lokal sökväg, till exempel `C:\BountyOS`. Kör filerna från den uppackade mappen. Undvik OneDrive- och nätverksmappar för programmets databas.
3. Starta Ollama. Öppna ett nytt Terminal- eller PowerShell-fönster och kör `ollama pull qwen2.5-coder:3b`. Vänta tills nedladdningen är klar.
4. Dubbelklicka på `install-windows.cmd`. Installationen skapar en lokal Python-miljö och sätter **en arbetsagent** vid första installationen. Befintlig databas och inställningar bevaras om du kör installationen igen.
5. Dubbelklicka på `start-windows.cmd`. Dashboarden öppnas automatiskt när servern är redo. Windows-starten väljer modellen `qwen2.5-coder:3b` och tillåter upp till 30 minuter för ett modellsvar på en långsam processor. Dina befintliga miljövariabler har företräde.
6. Välj **Inställningar → Kontrollera** och kontrollera att Ollama är ansluten. Behåll en arbetsagent till att börja med. Klicka sedan **Sök uppdrag** för att kontrollera sökningen; GitHub-inlämning kopplas separat.

Låt Ollama och BountyOS terminalfönster vara igång. Datorn behöver vara vaken för att arbetet ska fortsätta; dashboardfliken kan stängas. En 3B-modell tar mindre minne men kan behöva avvisa fler svåra uppdrag. Hastighet och faktisk leveranskvalitet måste prövas på din dator. Windows-kommandofilerna har granskats men inte körts i en riktig Windows-miljö här.

#### Om Scout väntar och uppdragen står som Hittat

Scout hittar och granskar uppdrag med det sökintervall du valt. **Hittat** betyder att ett uppdrag är registrerat men ännu inte utvalt för arbete. Arbetsagenten börjar först när granskningen ger statusen **Utvalt** och den lokala modellen är ansluten. Klicka på uppdraget och läs **Senaste bedömning** för orsaken; Scouts kort visar resultatet av senaste sökningen och eventuella fel.

Redan hittade uppdrag granskas före sökningen efter nya. Tillfälliga läsfel visas på uppdraget och prövas igen vid nästa sökning. GitHubs väntetider respekteras. Källor som bara är programsidor behöver en direktlänk till ett enskilt GitHub-ärende för automatisk granskning. Klicka **Sök uppdrag** för att starta en kontroll nu.

Uppdatera från den första Windows-versionen: stoppa BountyOS med Ctrl+C i terminalfönstret, packa upp den senaste ZIP-filen separat och kopiera dess mapp `bountyos` till din befintliga programmapp. Välj att ersätta filerna. Dina mappar `data` och `.venv` behålls. Starta sedan med den befintliga `start-windows.cmd` och uppdatera webbläsaren med Ctrl+F5. Denna rättning kräver inga nya paket eller modellnedladdningar.

#### Om GitHub ger CERTIFICATE_VERIFY_FAILED

BountyOS kompletterar Pythons betrodda systemcertifikat med certifikatpaketet `certifi`, som redan ingår i installationen. Både certifikatkedjan och servernamnet kontrolleras. Om certifikatkontrollen ändå misslyckas visas orsaken i appen och följer med även i meddelandet om väntetid. Kontrollera då datorns datum och tid samt om en proxy eller ett säkerhetsprogram använder en egen certifikatutfärdare; en sådan utfärdare måste kunna verifieras genom datorns betrodda certifikat.

För en äldre installation kan certifikatpaketet först prövas från ett kommandofönster i programmappen:

```cmd
.\.venv\Scripts\python.exe -c "import ssl,certifi; from urllib.request import urlopen; c=ssl.create_default_context(); c.load_verify_locations(certifi.where()); print(urlopen('https://github.com',context=c,timeout=25).status)"
```

Om testet visar `200` kan du stoppa den gamla BountyOS-processen och starta den från samma kommandofönster med följande inställning. Den gäller bara det kommandofönstret och program som startas därifrån. Den aktuella versionens certifikaträttning finns i mappen `bountyos` och kan installeras enligt uppdateringsstegen ovan.

```cmd
set "SSL_CERT_FILE=%CD%\.venv\Lib\site-packages\certifi\cacert.pem"
start-windows.cmd
```

### Linux, macOS eller WSL

Kräver Python 3.11 eller senare och Bash (Linux, macOS eller WSL). Kör från projektets rot:

```sh
./scripts/install.sh
./scripts/start.sh
```

Webbservern lyssnar lokalt på port 8787. Den startar pausad första gången. Öppna dashboarden i en webbläsare på samma dator. Inställningar och uppdrag lagras i `data/`, som inte ska checkas in i Git.

Installationsskriptet använder de låsta versionerna i `requirements.lock`. Det använder `uv` om det finns och annars Python venv/pip. En omstart återanvänder befintlig databas.

## Anslut lokal AI

Installera [Ollama](https://ollama.com/) på datorn där programmet körs och hämta modellen:

```sh
ollama pull qwen2.5-coder:7b
ollama serve
```

Ollamas skrivbordsapp kan redan ha startat servern; då behövs inte en andra `ollama serve`. Modellnedladdningen kräver flera GB ledigt utrymme. Kapaciteten och hastigheten beror på datorns minne och processor/GPU. Betalda AI-tjänster aktiveras inte om den lokala modellen saknas: arbetskön väntar i stället.

Miljövariabler läses från processen. `.env.example` är dokumentation och laddas inte automatiskt:

| Variabel | Standard | Användning |
| --- | --- | --- |
| `BOUNTYOS_PORT` | `8787` | Dashboardens lokala port |
| `BOUNTYOS_DATA_DIR` | `./data` | Beständig databas och leveransfiler |
| `BOUNTYOS_OLLAMA_URL` | `http://127.0.0.1:11434` | Lokal Ollama-server |
| `BOUNTYOS_MODEL` | `qwen2.5-coder:7b` | Installerad lokal modell |
| `BOUNTYOS_MODEL_TIMEOUT` | `240` | Max sekunder per lokalt modellanrop, 60–3600. Windows-starten använder 1800. |
| `GH_TOKEN` eller `GITHUB_TOKEN` | Ingen | GitHub-åtkomst för automatiska pull requests |

Modelladressen måste peka på loopback. Programmet skickar inte modellförfrågningar till en betald tjänst. Modellnamnet behöver stämma med en installerad modell i Ollama.

## Automatiskt arbetsflöde

1. Sökning läser offentliga GitHub-annonser och sparar dem utan dubbletter.
2. Urvalet söker avgränsade dokumentationsuppdrag med konkreta leveranskrav och annonserad belöning från en projektansvarig. Gamla annonser, förslag på belopp, reservationer och konkurrerande lösningar kan stoppa ett automatiskt genomförande.
3. En arbetsagent läser uppdraget och relevanta filer, förbättrar redan hämtade dokumentationsfiler och sparar leveransfiler lokalt. Den första versionen skapar inte nya filer i målprojektet.
4. Ett separat modellanrop granskar leveransen mot uppdraget. Filnamn och filtyper kontrolleras även av programmet.
5. Med GitHub-anslutning och **Automatisk inlämning** aktiverat kan en godkänd leverans lämnas som en pull request. Inställningen gäller kommande leveranser och kräver ingen ny knapptryckning per uppdrag.

Första versionen skriver dokumentation. Den kör inte godtycklig kod från främmande projekt och utför inte säkerhetstester, enkäter eller uppdrag som behöver fysisk utrustning. Den sparade pilotundersökningen är historiska researchuppgifter, inte nya lediga arbeten.

GitHub-åtkomst för inlämning behöver kunna läsa källprojektet, skapa en fork/gren och öppna en pull request. Använd en säker tokenbindning i körmiljön eller datorns tjänstekonfiguration. Skriv aldrig token i Git, loggar eller chatten. Befintliga `GH_TOKEN`/`GITHUB_TOKEN` återanvänds. Sökning av publika GitHub-sidor behöver ingen token.

## Intäkter och skalning

- **Mottaget:** faktiska betalningar som du registrerat med SEK-belopp och unik transaktionsreferens. Inbyggd anslutning till bank eller PayPal saknas i denna version; beloppet är manuellt registrerat, inte bankverifierat av programmet.
- **Väntande:** annonserad ersättning för inskickade uppdrag. Belopp i andra valutor växlas inte till SEK utan en verifierad kurs. En merge är inte bevis på betalning.
- **API-kostnad:** 0 kr eftersom endast lokal modell används. El, internet och befintlig hårdvara mäts inte av programmet.
- **Autoskalning:** kan öka antalet lokala arbetare inom ditt valda tak när kön och registrerade intäkter motiverar det. Den hyr aldrig servrar eller köper API-krediter. Fler arbetare ger inte alltid högre fart om de delar på samma modell.

Dashboarden börjar på 0 kr och saknar simulerade intäkter. Ett annonserat belopp innebär inte en garanterad utbetalning.

## Körning under dagen

Låt `start-windows.cmd` eller `scripts/start.sh` vara igång på datorn. Dashboardfliken kan stängas medan bakgrundstjänsten fortsätter arbeta. Paus stoppar tilldelningen av nya uppdrag. Vid datorvila, avstängning eller avslutad process arbetar inga agenter. För automatisk omstart kan kommandot läggas i datorns tjänstehanterare; konfigurera samma arbetskatalog, miljövariabler och beständiga `data/`-katalog.

Webbservern binder till loopback och är avsedd för en användare på samma dator. Exponera den inte publikt som en fleranvändartjänst; inloggning och kontoisolering ingår inte.

## Testa

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tester använder kontrollerade nätverks- och modellsvar och skickar inga riktiga pull requests. De verifierar beständig kö, dubbelregistreringar, budget, betalningsbokföring, HTTP-kontroller och agentflöde. Riktig lokal modell och GitHub-skrivåtkomst behöver kontrolleras separat i den miljö där programmet ska köras.

## Struktur

- `bountyos/app.py`: HTTP-API och statisk dashboard.
- `bountyos/store.py`: SQLite, inställningar, agentstatus och betalningar.
- `bountyos/engine.py`: schemaläggning, arbetsfördelning och leveransflöde.
- `bountyos/connectors.py`: GitHub och lokal modell.
- `bountyos/static/`: dashboardens HTML, CSS och JavaScript.
- `pilot/`: tidigare research med källor och begränsningar.

Nätverksåtkomst till GitHubs webbsidor behövs för sökning och till `api.github.com` för inlämning. Ollama använder modellregistret vid installation; när modellen finns lokalt behövs inget modell-API på internet. Projektet är en första fungerande programversion, inte verifierat lönsam verksamhet.
