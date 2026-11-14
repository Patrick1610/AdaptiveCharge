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
- Prijsuitval blokkeert laden niet. Bij ontbrekende, ongeldige of onvolledige
  prijzen plant de integratie één aaneengesloten blok tot vertrek, zonder de
  prijsplanreserve: drie uur nodig voor 06:15 betekent starten om 03:15. Tot dat
  blok blijft Green beschikbaar. Een begonnen fallbackblok blijft aaneengesloten
  als metercredits de benodigde tijd verkorten. Nieuwe prijzen herplannen dezelfde
  opdracht automatisch; doel, generatie, gebruikte energie en Stop blijven gelden.
  Kosten zijn onbekend, ook per venster; er worden geen prijzen verzonnen.
  Vlak vóór een positieve serviceaanroep wordt bij feeduitval opnieuw getoetst of
  het fallbackblok al mag starten. Een oude goedkope timer start dus niet te vroeg.
  De vroegere instelling `require_prices_for_control` wordt genegeerd en wordt
  niet meer aangeboden; een opgeslagen oude sleutel kan tot herconfiguratie blijven
  staan. De bestaande energie-, voertuig- en eigendomsgrenzen blijven gelden.
- Een reeds begonnen prijsblok blijft vanaf nu doorlopen zolang het huidige
  kwartier nog geselecteerd is. Metercredits schuiven zijn start niet opnieuw naar
  voren. Het resterende korte stuk van dat lopende blok is geen nieuwe losse start.
  Een niet meer geselecteerd kwartier wordt wel overgeslagen; Stop, ontkoppeling,
  doel-/energiegrens en een buitenlandse opdracht gaan ongewijzigd voor.
  Een pauze korter dan de ingestelde minimumblokduur naar het aangrenzende
  geselecteerde kwartier wordt tijdens laden gesloten. Dezelfde energie wordt
  uit de duurste toekomstige geselecteerde kwartieren gehaald; het totaalbudget
  stijgt niet. Dat kan iets meer kosten dan een theoretische planning met een
  pauze van enkele seconden. Valt de prijsfeed tijdens een prijsblok uit, dan
  herplant dezelfde opdracht naar één blok dat bij vertrek eindigt, met Green in
  de wachttijd. Een reeds begonnen fallbackblok blijft wel aaneengesloten; ook
  een materiële vertrekwijziging wordt opnieuw ingepland zonder nieuw budget.
- Een slapende Tesla houdt zijn voertuiglimiet-herstelopdracht bewaard. Die
  blokkeert geen Start van de ID.3 en blijft aan de oorspronkelijke Tesla-writer
  gekoppeld. Een vervangen writer of onbevestigde terugmelding na een herstelwrite
  wordt gemeld met de oorspronkelijke en geschreven limiet; nooit stil overschreven.
- De native bronlisteners en timers herbeoordelen feedherstel zelfstandig; een
  aanvullende Codextaak voor prijsbewaking is niet nodig.

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

## Oplevering 13–14 november 2026

### Doorladen bij prijsuitval en korte vensters, 5.1.0b6

De eigenaar verving op 14 november het eerdere verbod op Manual zonder Enever
door het bovenstaande vertrekgerichte fallbackbeleid. Dit wijzigt de actuele
besturing, niet de historische uitkomsten van eerdere proeven met de oude regel.
De gewone economische planning blijft de goedkoopste bekende kwartieren kiezen.
Losse restvensters onder de ingestelde drie minuten worden aan het goedkoopste
geschikte aangrenzende kwartier gekoppeld, inclusief vrije ruimte binnen een
gedeeltelijk gebruikt kwartier. Totale energie en de bijbehorende kosten blijven
behouden. Zonder bestaand blok of voldoende aansluitcapaciteit blijft de bestaande
tolerantie of zichtbare uitzondering voor een noodzakelijk kort blok gelden.

### Normale bediening en planningssensoren

De zichtbare regeling heet Laadregeling; de oude preview-identiteit blijft intern
behouden om bestaande entiteiten en opgeslagen opdrachten zonder duplicaten bij
te werken. Het installatiedomein `adaptive_charge_preview` is daarom een technische
compatibiliteitsnaam, niet een uitgeschakelde of beperkte appvariant.

Per auto komt één Laadplanning-sensor bij. Zijn numerieke staat is de resterende
geplande Manual-laadduur in minuten. `quarters` bevat tijdzonebewuste UTC-waarden
`start`/`end`, `charging`, `minutes` en `energy_kwh`, ook voor nulkwartieren tussen
blokken. Verstreken stukken worden afgeknipt; de payload is begrensd op de bestaande
48-uursplanningshorizon. De sommen sluiten aan op `scheduled_energy_kwh` en de
sensorstaat. De sensor maakt geen opdracht en verzint geen toekomstige Green-energie.
Zonder actieve opdracht is de staat nul en de kwartierenlijst leeg. Bij ontbrekende
berekeningsgegevens is de duur onbekend, met de bestaande reden zichtbaar.
De kwartierprojectie wijzigt de laadbesturing en energieboekhouding niet.

`energy_kwh` en `scheduled_energy_kwh` zijn totale AC-laadenergie tijdens de
Manual-vensters, inclusief de eventuele zonnebijdrage binnen zo'n venster. Zij zijn
dus niet hetzelfde als uitsluitend extra netenergie (`additional_kwh`) of accutoename.
Autonoom Green buiten de vensters is niet in deze projectie opgenomen.

Onbekende vensterkosten blijven `None`. Sensorattributen en diagnostiek geven
deze waarde ongewijzigd door. Het dashboard formatteert de totale kosten alleen
als die numeriek zijn; de kwartiergrafiek gebruikt geplande kWh, geen verzonnen
kwartierkosten. Een [native kaartvoorbeeld](../examples/planning-card.yaml) toont resterende tijd
en een kwartiertabel. De standaardgrafiek toont historische waarden. Het optionele
[ApexCharts-voorbeeld](../examples/planning-chart-apex.yaml) tekent toekomstige
kwartieren rechtstreeks uit de attributen, zonder recorderhistorie.

### Bewerkbaar vermogensveld, 5.1.0b5

Het vermogensveld gebruikt een native numerieke HA-selector. De eerdere
union-validator werd door HA als vaste nul weergegeven, hoewel de API andere
geldige waarden accepteerde. De selector behoudt dezelfde validatie: nul of
1–22 kW, met afwijzing van niet-eindige waarden. Een regressie toetst het echte
geserialiseerde HA-formulier, naast de bestaande berekenings- en validatietests.

### Planningsvermogen per voertuig, 5.1.0b4

Een auto kan met een lagere bestaande stroomlimiet laden dan de laadpaal aankan.
Daarom kan ieder voertuig een eigen planningsvermogen krijgen. `0` behoudt het
gedeelde laadpaalvermogen; een positieve waarde begrenst alleen de berekening
voor die auto en kan nooit boven het gedeelde vermogen uitkomen. Vensters,
urgentie, minimale blokduur en de berekening bij vertraagde data gebruiken
dezelfde waarde, zichtbaar als `planning_power_kw` op de voertuigsensor.

Dit wijzigt geen ampères of fasen en leert geen laadvermogen uit wisselende
zonneopbrengst. Kies een conservatief haalbaar vermogen bij de gebruikte
voertuiginstelling en controleer het opnieuw wanneer die instelling verandert.
Andere voertuigen blijven het gedeelde laadpaalvermogen gebruiken.

### Ontbrekende volgende prijsdag, 5.1.0b3

Na de datumwisseling publiceert Enever geldige kwartieren voor vandaag terwijl
`prices_tomorrow` nog `None` is. Dat betekent dat die volgende dag nog niet is
gepubliceerd. 5.1.0b3 slaat zo'n lege publicatie over. De bestaande voorwaarden
blijven gelden: een beschikbare bron, geldige intervallen en dekking van het
huidige tijdstip zijn vereist voor aanvullend laden. Verlopen, onbeschikbare of
ongeldige prijzen geven geen toestemming voor Manual. De regressies toetsen de
datumwisseling met native datums en ISO-tekst, plus ontbrekende en misvormde data.

### Tariefformaatcorrectie 5.1.0b2

Enever 1.8 publiceert de tijden in `prices_today` en `prices_tomorrow` intern als
Python-datumobjecten. De HA-API toont dezelfde waarden als ISO-tekst. Versie
5.1.0b1 accepteerde alleen die tekstvorm en wees de interne tariefregels af.
5.1.0b2 verwerkt beide vormen met dezelfde tijdzone- en zomertijdcontrole.
De eis van actuele prijzen vóór Manual blijft behouden. De regressietest gebruikt
192 native Enever-kwartieren en toetst actuele dekking op beide dagen en afwijzing
buiten de beschikbare periode. Dit verandert geen stop-, laad- of limietbeleid.

### Eerdere oplevering 5.1.0b1

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
