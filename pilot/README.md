# Bounty-pilot

Första researchkörningen: 17 oktober 2026. Två agenter sökte efter koduppdrag respektive research och dokumentation. En tredje agent granskade underlaget. Ingen kandidat valdes för genomförande: de hittade uppdragen hade hög konkurrens, oklar återstående tillgänglighet eller omfattande hårdvaru- och testkrav.

Inga uppdrag har reserverats, inga meddelanden eller lösningar har skickats och ingen ersättning har tjänats in. Detta är ett sparat urval och ett arbetsflöde för piloten. En tjänst som kör agenter kontinuerligt är ännu inte byggd.

## Arbetsflöde

1. Sökagenten hittar konkreta uppdrag och sparar originalkälla, belopp och tidpunkt.
2. Urvalsagenten kontrollerar återstående arbete, betalningsvillkor, konkurrerande lösningar och vad som krävs för att testa leveransen.
3. Upp till två arbetsagenter tar var sitt valt uppdrag. Varje uppdrag får en ansvarig så att vi inte konkurrerar med oss själva.
4. Granskningsagenten kontrollerar resultatet mot uppdragets leveranskrav och de tester som faktiskt körts.
5. Utfallet följs genom inlämning, godkännande och faktisk utbetalning. Annonserade belöningar räknas inte som intäkt.

Piloten kräver ingen separat förhandsförfrågan om AI-användning. Beskrivningar av arbetsmetod, källor och tester ska vara sanningsenliga.

Målet är små uppdrag kring 10–500 kr. Beloppen nedan behåller källans valuta; ingen aktuell valutakurs har hämtats. Innan ett arbete startar behöver det ha en tidsgräns och en budget. Någon budget för externa API:er eller hyrd hårdvara har inte satts.

## Första urvalet

| Uppdrag | Annonserad ersättning | Observation vid kontrollen | Beslut |
| --- | --- | --- | --- |
| [Chronicle: minst 10 PromQL-exempel](https://github.com/josedab/chronicle/blob/main/docs/BOUNTY_PROGRAM.md#-documentation-25-each) | $25; dollarvaluta ej specificerad | Programtabellen säger Open, men relevanta PR:er [#82](https://github.com/josedab/chronicle/pull/82) och [#31](https://github.com/josedab/chronicle/pull/31) är redan öppna. | Bevaka; ledig plats är obekräftad. |
| [Chronicle: minst 10 FAQ-svar](https://github.com/josedab/chronicle/blob/main/docs/BOUNTY_PROGRAM.md#-documentation-25-each) | $25; dollarvaluta ej specificerad | Programtabellen säger Open; relevant [PR #63](https://github.com/josedab/chronicle/pull/63) väntar redan. | Bevaka; ledig plats är obekräftad. |
| [Sybil: RX 7900 XTX-recept](https://github.com/sybil-solutions/local-ai-registry/issues/219) | $50; dollarvaluta ej bekräftad | Reserverat till annan deltagare till 20 oktober kl. 08.01 svensk tid. Kräver specifik GPU och verkliga mätningar. | Starta inte nu. |
| [TentOfTrials: testfixturer](https://github.com/weilixiong/TentOfTrials/issues/5) | $25 (LT); LT och betalningssätt oklara | Minst 10 länkade öppna PR:er och 44 kommentarer. Återstående betalt arbete obekräftat. | Välj bort i första omgången. |
| [Aparapi: inline-deklarationer](https://github.com/Syncleus/aparapi/issues/97) | $20 i rubriken; dollarvaluta ej bekräftad | Öppet sedan 2018, minst 7 länkade öppna PR:er och inget verifierat aktuellt betalningsbesked. | Välj bort i första omgången. |
| [SpaceApp: starta hela utvecklingsstacken](https://github.com/oll4com/spaceapp/issues/65) | 450 USD | Uttryckligt betalningslöfte efter första godkända mergade lösningen. Minst 10 länkade öppna PR:er; kräver Linux samt macOS eller WSL2-verifiering. | Bevaka som större uppdrag. |
| [Cognitive-OS: jämför 8 modellfamiljer](https://github.com/aLexzzz430/Cognitive-OS/issues/5) | 3 000 USD | Minst 10 länkade öppna PR:er och 95 kommentarer. Omfattande research; kvarvarande budget obekräftad. | Välj bort från småpiloten. |

En öppen issue eller programtabell visar inte att uppdraget är ledigt. Länkade öppna PR:er visar konkurrens eller tidigare arbete, men vi har inte granskat kvaliteten på varje lösning. Inga faktiska utbetalningar har verifierats.

## Betalningsbelägg och begränsningar

- SpaceApps ägare [bekräftade den 16 oktober](https://github.com/oll4com/spaceapp/issues/65#issuecomment-6222727310) att ingen lösning då hade mergats. Annonserade villkor anger betalning inom sju dagar via PayPal, Wise eller banköverföring efter godkänd merge. Detta är ett betalningslöfte, inte verifierad finansiering.
- Sybils maintainer [bekräftade reservationen den 17 oktober](https://github.com/sybil-solutions/local-ai-registry/issues/219#issuecomment-6234543063). [Registret](https://github.com/sybil-solutions/local-ai-registry/issues/262) redovisar accepterade belopp; det bevisar inte genomförda utbetalningar.
- Chronicle anger PayPal eller GitHub Sponsors inom sju dagar efter godkänd merge. Uppdrag tilldelas första deltagaren och PR ska lämnas inom sju dagar. En Open-rad i programtabellen fastställer inte att en ny deltagare kan få uppdraget nu.
- GitHubs webbsidor och råfiler gick att läsa. Bland annat Algora, Superteam Earn och GitHub API blockerades av miljöns nätverksproxy med HTTP 403. Bred GitHub-sökning stoppades när HTTP 429 uppstod.
- Statusen kan ändras efter denna kontroll. Kontrollera originalkällan igen före ett genomförande.

## Mätning inför nästa omgång

Spara arbetstid, verifierade driftkostnader, inlämningsdatum, godkännande, mottagen betalning och avgifter per uppdrag. Beräkna nettot från faktisk utbetalning. Nästa sökomgång bör prioritera färska, avgränsade uppdrag med tydligt betalningslöfte och få befintliga lösningar.

Fullständiga observationer och källor finns i [research.json](research.json).
