# AdaptiveCharge met uitschakelbare besturing

Deze uitbreiding volgt Patricks opdracht van 13 november 2026: bouw en publiceer
een volledige regelaar, richt hem in HA in, en begin de Tesla-proef pas met een
werkende prijzenfeed. De bestaande native hoofdschakelaar is in bewezen rust
uitgezet; sessie en pending waren leeg, generatie 29 bleef behouden. Het receipt
staat in de eigen HA-map `adaptive_charge_preview_20261113/control_preparation_20261113`.
Er komt geen geplande Codextaak voor het wachten op een feed.

De eerdere previewrelease blijft historisch read-only. De uitbreiding behoudt het
domein en de bestaande entiteitsidentiteiten voor een gewone HACS-upgrade. Eén
hoofdschakelaar per laadpaal geeft uitvoeringsrecht; zonder dat recht blijft het
pakket meerekenen zonder actuatoracties. Deze nieuwe gebruikersopdracht vervangt
voor deze uitbreiding het eerdere verbod om besturing te bouwen. De huidige
controller blijft de enige productwriter, met Jev en onafhankelijke Claude-review;
er wordt geen geslaagde custom-agentpreflight of formele workflowclosure verzonnen.

## Contract

- Green is de basis; Enphase regelt zon, fasen en ampères. Alleen gepland of direct
  aanvullend laden wordt door AdaptiveCharge aangevraagd.
- Geen fysieke start uit alleen een previewkopie. Een expliciete Start bindt auto,
  aansluiting, generatie en meterboekhouding. Eén opdracht per laadpaal.
- Hoofdschakelaar uit: eigen aanvullend laden gecontroleerd naar Green overdragen;
  bewaarde opdracht blijft bestaan. Expliciete Stop trekt hervattingsrecht in.
- Nulvermogen, fasepauze of anonieme schakelaar-OFF annuleren niet en veroorzaken
  op zichzelf geen Green-commando. Een echte Stop of vastgestelde ontkoppeling wel.
- Automatisch hervatten na een HA-herstart is de standaard voor een bewaarde
  opdracht met toekomstig vertrek en dezelfde aansluiting. Een oude kabeltimestamp
  is geen fout: de stabiele aansluitidentiteit heeft geen leeftijdslimiet.
- Ontbrekende/vertraagde voertuiggegevens wissen geen opdracht. De bewaarde
  energieallocatie mag worden afgemaakt zonder een nieuwe VW-update. Ook een eerste
  schatting met geldige getallen kan begrensd worden gebruikt; doelbevestiging,
  nieuwe voortgangsallocatie en terugvalbewijs blijven afzonderlijk herkenbaar.
- Meterfout, ontbrekende actuele kWh-meter of een andere bestuurder blokkeert extra
  netladen, maar wist de opdracht niet. Geen nieuw energiebudget bij herstart,
  herhaalde Start of doelwijziging. De verliesmarge blijft 15%, geen autokalibratie.
- Bestaande native Master moet uit staan en native ACTIVE/SESSION/PENDING leeg;
  anders weigert de nieuwe uitvoerder overname. Hij schakelt de oude regeling niet
  zelf uit en overschrijft geen nieuwe externe opdracht.
- Tesla kan alleen met expliciet ingeschakelde capability tijdelijk zijn limiet
  verhogen; ID.3 blijft read-only. Herstellen uitsluitend bij eigen, ongewijzigde
  limiet en dezelfde Tesla-bedieningsidentiteit, ook na ontkoppelen. Voor de eerste proef geldt maximaal 80%.
- De laadstatussensor is aanvullend: fysieke rust wordt bewezen door schakelaar uit
  en vers onafhankelijk HomeWizard-vermogen onder 0,2 kW. Een ontbrekende binaire
  laadstatus blokkeert dat bewijs niet.
- Het totale opdrachtplafond telt alle gemeten IQ-energie mee, ook Green. Veranderde
  bedieningsbronnen worden geweigerd zolang de eigen laadactie of limietherstel
  loopt. Bij onvoorziene bronuitval probeert alleen de vastgelegde oude bediening
  na maximaal 120 seconden ontbrekende terugmelding begrensd negatief af te ronden; een nieuwe externe opdracht gaat altijd voor.
- Voor Patricks installatie is een werkende Enever-feed verplicht voor Manual.
  De bron moet beschikbaar zijn en geldige tariefintervallen voor het huidige
  moment leveren. Ook vlak vóór een positieve serviceaanroep wordt dit herlezen.
  Feeduitval draagt alleen het eigen Manual-laden terug over aan Green; zelfstandig
  Green mag doorlopen. De opdracht en gebruikte energie blijven behouden en de
  normale integratie beoordeelt hervatting zodra de feed terug is. Er komt geen
  Codextaak om de feed te volgen.
- Een slapende Tesla houdt zijn voertuiglimiet-herstelopdracht bewaard. Die
  blokkeert geen Start van de ID.3 en blijft aan de oorspronkelijke Tesla-writer
  gekoppeld. Een vervangen writer of onbevestigde terugmelding na een herstelwrite
  wordt gemeld met de oorspronkelijke en geschreven limiet; nooit stil overschreven.
- Na publicatie/configuratie wordt de feed één keer gecontroleerd. Zonder bruikbare
  feed blijft de fysieke proef ongestart tot Patricks bericht.

## Tesla-proef, nog niet gestart

T0 wordt pas aan een concrete UTC-tijd gebonden na software-GO, geïnstalleerde
bestandverificatie, werkende tarieven en dezelfde Tesla-aansluiting. De proef krijgt
vooraf een absolute eindtijd en een gezamenlijk kWh-plafond in haar receipt.
Geen wijziging van voertuigdata, klimaat, fasen of ampères om een resultaat te maken.

De eerste commissioning gebruikt maximaal 6 kWh totaal vanaf de nieuwe IQ-basis en
een absolute eindtijd T0 + 120 minuten. R0 is de bij aanvang gemelde Tesla-range.
Doelen worden begrensd op de actuele autolimiet van maximaal 80%; een onvoldoende
bereikbare stap wordt als zodanig vastgelegd. De concrete UTC-tijden, R0 en doelen
moeten vooraf in het commissioningreceipt staan.

1. Start op R0 + 10 km en vertrek T0 + 90 minuten:
   vergelijk de gekozen kwartieren met de werkelijke feed en controleer de timer.
2. Verhoog naar R0 + 20 km en verlaag daarna naar R0 + 15 km zonder nieuwe Start; identiteit en gebruikte energie
   moeten behouden blijven. Wacht minstens vijf gemeten fysieke laad minuten vóór
   de verlaging tijdens laden.
3. Stel R0 + 25 km in met vertrek over 10 minuten om urgentie te toetsen; beoordeel tekort en haalbaarheid,
   zonder een onhaalbaar doel als succes aan te merken.
4. Observeer doelstop of energieguard afzonderlijk. Alleen een passende nieuwe
   voertuigmeting bewijst doelbereik; Green op energiegrens is verificatie.
5. Stop expliciet, wacht op rust en verifieer dat oude timers niet hervatten. Een
   nieuwe Start is een afzonderlijke opdracht binnen hetzelfde proefenergieplafond.
6. Fasepauzes, nulvermogen, hoofdschakelaar uit/aan en herstart worden softwarematig
   gereplayed. Geen onnodige fysieke HA-herstart of geforceerde fasewissel.
7. Natuurlijk bijladen alleen beoordelen als een echte terugval optreedt. Geen
   kunstmatige SOC-daling; anders: niet opgetreden. Werkelijke ontkoppeling sluit af.

Softwaretests, service/readback en fysiek gedrag worden afzonderlijk gerapporteerd.

De laadpaalsensor behoudt zijn entiteits-ID. Zijn toestanden zijn nu `read_only`,
`control_enabled` en `stopping`; de voertuigtoestanden blijven afzonderlijk zichtbaar.

Als de prijsbron onbeschikbaar is, vervallen ook de voorgestelde adviesvensters en
kosten. Oude tariefattributen worden dan niet als een live feed behandeld.
De besturingsstatus en foutcodes in attributen blijven technisch herkenbaar voor
diagnostiek; dashboard en normale sensortoestanden tonen Nederlandstalige uitleg.

## Oplevering 13 november 2026

Versie [5.1.0b1](https://github.com/Patrick1610/AdaptiveCharge/releases/tag/v5.1.0b1)
is gepubliceerd op commit `c27cee992f0ddb565e11b9ddd58a2392a0ad1a26`.
Alle 1.043 softwaretests zijn lokaal geslaagd. De
[GitHub-tests](https://github.com/Patrick1610/AdaptiveCharge/actions/runs/43415681777)
en [hassfest/HACS-validatie](https://github.com/Patrick1610/AdaptiveCharge/actions/runs/43415681834)
zijn voor dezelfde releasecommit geslaagd.
De geconsolideerde onafhankelijke critical-review gaf GO zonder open critical,
high of medium bevindingen; de herstelbare bestandsupgrade kreeg afzonderlijk GO.

De bestaande HA-instantie is bijgewerkt en na een toegestane HA-herstart geladen.
De 24 componentbestanden komen overeen met het gepubliceerde releasearchief.
Beide voertuigsubentries, de tien bestaande entiteiten en de vijf nieuwe
bedieningsentiteiten zijn geverifieerd. Alle drie configuraties zijn via de gewone
configuratiestromen opgeslagen en teruggelezen. HACS was op de host afwezig;
daarom is de bestaande component vanuit de gepubliceerde GitHub-release bijgewerkt
met een exacte controle van de oude bestanden en een bewaarde herstelkopie.

De oude native Master is uit; diens vier automationconfiguraties zijn ongewijzigd.
De nieuwe Master is aan, zonder Start, actieve auto of wachtende actuatoractie.
De eerste proef is ingesteld op maximaal 6 kWh en 80% SOC. De bestaande
AdaptiveCharge-dashboardroute heeft dezelfde drie tabbladen behouden; alle
15 entiteitsverwijzingen en 13 Markdown-templates zijn in HA gecontroleerd.
Het oude EV-dashboard en de dashboardinventaris zijn ongewijzigd.

De afsluitende feedcontrole gaf `unknown`, zonder tarieven voor vandaag of morgen.
Daarom is de fysieke proef niet gestart en wacht Manual op Enever. Zelfstandig
Green bleef toegestaan en fysiek laden werd waargenomen. Er is geen Codextaak
voor feedbewaking gemaakt. Dit is installatiebewijs; fysieke doelstop, herstart
tijdens eigen laden en natuurlijk bijladen met deze release blijven onbewezen.
De oorspronkelijke proefuitslagen zijn hierdoor niet gewijzigd.

De eigen HA-bewijzen staan onder
`adaptive_charge_preview_20261113/control_preparation_20261113/upgrade_5_1_0b1`:
het oorspronkelijke upgrade-receipt bewaart de bestandsfase, terwijl
`runtime_outcome.json` de geverifieerde herstart, inrichting en wachttoestand vastlegt.
