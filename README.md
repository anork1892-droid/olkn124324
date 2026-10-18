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
6. Välj **Inställningar → Kontrollera** och kontrollera att Ollama är ansluten. Behåll en arbetsagent till att börja med. Sökningen startar automatiskt. Välj **Starta arbete** när Ollama är ansluten för att även utföra utvalda uppdrag; GitHub-inlämning kopplas separat.

Låt Ollama och BountyOS terminalfönster vara igång. Datorn behöver vara vaken för att arbetet ska fortsätta; dashboardfliken kan stängas. En 3B-modell tar mindre minne men kan behöva avvisa fler svåra uppdrag. Hastighet och faktisk leveranskvalitet måste prövas på din dator. Windows-kommandofilerna har granskats men inte körts i en riktig Windows-miljö här.

#### Om Scout väntar och uppdragen står som Hittat

Scout börjar söka när programmet startar och söker igen en minut efter varje avslutad omgång som standard. Sökningen fungerar även när arbetsagenterna är pausade eller Ollama inte är ansluten. GitHubs väntetider kan förlänga pausen. Nästa automatiska sökning visas på Scouts kort. Du kan välja Pausa sökning, Starta sökning eller Sök nu; en paus sparas mellan omstarter. Sökintervallet kan ändras i Inställningar. Äldre installationers standardintervall på 30 minuter uppdateras en gång till en minut; andra valda intervall bevaras. **Hittat** betyder att ett uppdrag är registrerat men ännu inte utvalt för arbete. Arbetsagenten börjar först när granskningen ger statusen **Utvalt** och den lokala modellen är ansluten. Klicka på uppdraget och läs **Senaste bedömning** för orsaken; Scouts kort visar resultatet av senaste sökningen och eventuella fel.

Redan hittade uppdrag granskas före sökningen efter nya. Tillfälliga läsfel visas på uppdraget och prövas igen vid nästa sökning. GitHubs väntetider respekteras. Källor som bara är programsidor behöver en direktlänk till ett enskilt GitHub-ärende för automatisk granskning. Klicka **Sök nu** för att starta en kontroll nu.

Sökningen växlar mellan riktade sökningar efter dokumentation i ärendets titel med en bounty i titeln eller en bounty-etikett, och ärenden med etiketten `docs`/`documentation` och en bounty i beskrivningen. FAQ och stavning ingår. Varje sökfråga följer GitHubs sidantal upp till 100 resultatsidor. Vid sista eller tom sida börjar just den sökfrågan om med de senaste träffarna, så samma annonser kan återkomma. Framstegen sparas lokalt och fortsätter efter omstart. När en sida bara innehåller kända träffar eller är tom provar Scout upp till tre söksidor per omgång med vanliga väntetider. Alla nya träffar sparas före granskning, även om gränsen på 20 ärendeläsningar nås eller nätverket tillfälligt slutar fungera. Loggen skiljer mellan unika träffar, nya träffar, redan kända träffar och granskade uppdrag. Om inget är redo för arbete visas de vanligaste hindren bland sparade uppdrag. Uppdrag utan verifierbart belopp eller med föreslagen belöning blir **Bortvalt**. Oklara betalningsvillkor, reservationer eller arbete utanför agentens stöd blir **Blockerat**. Dessa uppdrag ligger kvar med en förklaring men granskas inte om vid varje sökning. Dokumentation om exempelvis GPU-stöd eller befintliga driftsättningsinstruktioner avvisas inte enbart på grund av ämnet; krav på faktisk kodändring, testkörning eller driftsättning ligger fortfarande utanför stödet. Tidigare avslag på dokumentationsomfattning utan påbörjat arbete köas en gång för ny granskning efter uppdateringen. Om uppdragsgivaren ändrar villkoren kan du öppna uppdraget och välja **Försök igen**. Sökresultatet och Scouts kort visar hur många uppdrag som valdes ut, valdes bort eller blockerades.

En annons med exempelvis `$25` kan väljas ut om resten av granskningen godkänns. Appen visar beloppet som `$` med ospecificerad dollarvaluta och antar ingen växelkurs eller USD. Uppdragsgivarens projektroll kontrolleras även i de senaste kommentarerna som GitHub visar för samma ärende. Saknad rollinformation skiljs från en uttrycklig roll som inte bekräftar projektansvar. Ett botnamn eller en bounty-etikett bevisar inte vem som ansvarar för betalningen.

Vid uppdatering återköas tidigare obearbetade uppdrag som stoppades av de gamla valuta- eller maintainerreglerna för en ny kontroll. En färsk granskning avgör om de går att utföra; de blir inte direkt utvalda. Sparade leveranser och uppdrag som redan har påbörjats eller skickats in återköas inte av denna rättning.

Återpublicerade annonser med en särskild `Source URL`/`Original URL`-rubrik eller motsvarande källrad i en tabell (även `原 URL`) granskas via originalärendet. Kopian väljs bort; originalets status, projektansvarig och ersättning bedöms separat. Originalet sparas i kön även om läsningen misslyckas. Kontrollen delar sökningens vanliga gräns på 20 ärenden och öppnar endast direkta GitHub-ärenden. Andra länkar i beskrivningen följs inte som uppdragskällor.

Nätfel vid dokumenthämtning visas med orsak och filnamn. Endast ett faktiskt HTTP 404 behandlas som en saknad fil. Agenten får en uttrycklig lista med inlästa filnamn. Ollamas strukturerade svar begränsar filvalet till dessa namn och programmet kontrollerar även svaret. Om modellen ändå föreslår en annan, i övrigt tillåten dokumentationssökväg görs högst ett korrigeringsförsök med samma källunderlag. Filer döps aldrig om automatiskt för att kringgå kontrollen. Nya dokument stöds ännu inte. Uppdrag som stoppats av det tidigare exempelnamnet `docs/name.md` återköas en gång för färsk källgranskning om ingen leverans eller inlämning finns sparad. Om uppdraget tillåter ett README-avsnitt som alternativ kan agenten använda den befintliga filen. Uppdrag som stoppades av de två äldre generiska dokumentfelen återköas en gång, förutsatt att ingen leverans eller inlämning finns sparad.

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

Webbservern lyssnar lokalt på port 8787. Sökningen startar automatiskt; arbetsagenterna aktiveras separat med Starta arbete. Öppna dashboarden i en webbläsare på samma dator. Inställningar och uppdrag lagras i `data/`, som inte ska checkas in i Git.

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

Låt `start-windows.cmd` eller `scripts/start.sh` vara igång på datorn. Dashboardfliken kan stängas medan bakgrundstjänsten fortsätter arbeta. Pausa arbete stoppar tilldelningen av nya uppdrag. Pausa sökning stoppar nya sökomgångar när en pågående omgång är klar. Vid datorvila, avstängning eller avslutad process arbetar inga agenter. För automatisk omstart kan kommandot läggas i datorns tjänstehanterare; konfigurera samma arbetskatalog, miljövariabler och beständiga `data/`-katalog.

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
