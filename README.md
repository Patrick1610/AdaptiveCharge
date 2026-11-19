# AdaptiveCharge 5.2 — laadplanning en economische Green-keuze

Deze beta bundelt planning rond een gedeelde laadpaal met afzonderlijke
voertuigprofielen met twee manieren om bereik te berekenen. Enphase Green blijft de basis;
de integratie plant aanvullend netladen naar een bereikdoel en vertrektijd.
De optionele economische regeling vergelijkt Green met later inkopen. Zonder
opdracht kan een afzonderlijke filter Green pauzeren wanneer netto terugleveren
meer oplevert dan het tijdgewogen gemiddelde van de complete bekende dagen.
Bij een vertrek buiten de bekende tarieven kiest vroege inkoop alleen kwartieren
onder dat gemiddelde minus een instelbare centaftrek. Noodzakelijk deadline-laden
houdt voorrang. Zie [economische planning](docs/ECONOMISCHE_PLANNING.md).

Zonder bruikbare prijzen plant hij één aaneengesloten blok dat eindigt bij vertrek.
Drie uur laden voor 06:15 begint dan om 03:15, zonder extra prijsplanreserve.
Terugkerende prijzen herplannen dezelfde opdracht; de energieboekhouding blijft staan.

Elke auto heeft een **Laadplanning**-sensor: de staat is de resterende geplande
laadduur in minuten; `quarters` bevat per kwartier de geplande minuten en kWh,
inclusief nulkwartieren tussen blokken. Zie [het kaartvoorbeeld](examples/planning-card.yaml).
Vervang daarin de voorbeeldentiteit door de werkelijke planningssensor van je auto.
De kWh zijn de totale geplande AC-laadenergie tijdens Manual, inclusief eventuele
zon in die vensters. De economische planner toont daarnaast apart toegewezen
Green-energie en pauzes. Deze toewijzing is geen voorspelling van alle autonome
Green-opbrengst: Enphase kan meer overschot benutten dan het nog benodigde budget.

**De hoofdschakelaar staat standaard uit.** Uit betekent alleen meerekenen.
Met de hoofdschakelaar aan en een expliciete Start bestuurt de integratie de
geselecteerde auto via de bestaande Enphase-/voertuigintegraties. De oude
controller moet uit staan; actieve native opdrachten worden niet overgenomen.
Fasen en ampères blijven bij Enphase. Zie [bediening en testgrenzen](docs/CONTROL.md).

## Installeren via HA Marketplace

Voeg `Patrick1610/AdaptiveCharge` zo nodig toe als custom repository van het type
**Integration**. Zet bij die repository **Show beta versions** aan, open opnieuw
downloaden en kies expliciet `v5.2.0b7`. Met beta's ingeschakeld biedt HA Marketplace ook
latere beta's aan; gebruik geen onbedoelde automatische upgrade.
Home Assistant 2026.11.1 of nieuwer is vereist. Voeg vervolgens
**AdaptiveCharge** toe onder Apparaten & diensten, met één hoofdinstantie
voor de laadpaal en afzonderlijke voertuigen als subconfiguraties.


Deze beta behoudt het domein `adaptive_charge_preview` voor een gewone update van
5.0.0b1. Bestaande entries, voertuigsubentries en entiteitsidentiteiten blijven staan.
Een oude AdaptiveCharge v4-entry
wordt niet automatisch gemigreerd of gebruikt. Deze release verwijdert geen reeds
geïnstalleerde v4-code of entries. HA Marketplace ruimt de oude domeinmap bij deze naamswijziging
niet betrouwbaar op. Controleer daarom vóór een eventuele herstart:

1. Noteer of archiveer de oude configuratie en verwijder **alleen** de oude
   `adaptive_charge`-integratie-entries. Alleen deactiveren is voor deze upgrade
   niet voldoende: HA Marketplace weigert een domeinwijziging zolang een oude entry bestaat.
   Verwijder vervolgens de oude HA Marketplace-installatie en installeer de geselecteerde
   v5-beta opnieuw. Laat de werkende native scripts, automations en helpers staan.
2. Controleer of `/config/custom_components/adaptive_charge` is achtergebleven.
   Verplaats die oude map buiten `custom_components` naar een eigen archief of
   verwijder haar pas na archivering. Het gedeactiveerd houden van oude entries
   blijft nodig; bestanden verplaatsen is geen entrymigratie.
3. Controleer na HA Marketplace-download alle integratiebestandhashes met het bij de release
   gevoegde JSON-bewijs, zoals uitgelegd in [PREVIEW.md](docs/PREVIEW.md#installeren-naast-de-bestaande-regeling).

HA Marketplace kan na vervanging om een HA-herstart vragen. Een eigen bewaarde opdracht met
toekomstig vertrek en dezelfde aansluiting hervat automatisch, met behoud van
energieboekhouding. Reeds fysiek doorlopend Manual-laden vereist geen extra
moduswrite of nieuw cloudrapport. Dit geldt voor deze integratie; de oude native
regeling heeft haar eigen herstartgedrag en moet bij overname uit blijven.

Zie [de volledige besturing](docs/CONTROL.md) en de
[historische 5.0-previewberekeningen](docs/PREVIEW.md). Softwaretests zijn geen fysiek functioneel akkoord
voor doelstop of natuurlijk bijladen bij beide auto's.

### Bereikberekening kiezen

Kies op basis van de betekenis van je sensoren, ongeacht het automerk:

| Berekening | Bereikbron | Werking |
| --- | --- | --- |
| Actueel resterend bereik | De resterende kilometers bij de huidige acculading | Rekent het gewenste bereik om met de huidige verhouding tussen range en SOC; SOH wordt niet nogmaals toegepast. |
| Volbereik met SOH-correctie | Een referentie bij 100% accu, vóór SOH-correctie | Corrigeert het volbereik voor SOH en leidt daaruit het benodigde percentage af. Zonder SOH-bron is de correctie 100%. |

Een helper die zelf resterend bereik berekent hoort bij de eerste keuze.
Het [ID.3-helpervoorbeeld](examples/id3-remaining-range.yaml) gebruikt de gemelde
resterende kilometers, behalve bij ontbrekende waarden of de bekende 312-sentinel.
Dan rekent het `312 × SOH/100 × SOC/100`; ontbrekende SOH valt terug op de laatst
geldige waarde of 90%. De bijbehorende SOH-referentie is een tweede native
templatehelper die zijn laatst bekende waarde herstelt. Dit YAML-bestand bevat
formulierwaarden voor helpers en wordt niet rechtstreeks als HA-package geladen.
Een berekende waarde krijgt geen verzonnen voertuigmeettijd.

De keuze verandert geen bestaande voertuigconfiguratie automatisch. Optionele
voertuigbediening vereist passende HA-entiteiten en expliciete configuratie;
tijdelijke limietverhoging en laden via de auto blijven alleen beschikbaar bij
de berekening met actueel resterend bereik. De laadpaalbediening blijft Enphase.


## Ontwikkelen

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-preview-test.txt
.venv/bin/python -m pytest tests -q
```

`custom_components/` bevat uitsluitend de nieuwe integratie, conform de
HA Marketplace-repository-indeling. De ongewijzigde v4-bron staat als archief onder
`legacy/v4/`, met de oude uitleg in [LEGACY_V4.md](docs/LEGACY_V4.md).
Die archiefcode wordt niet door HA Marketplace geïnstalleerd. Historische tests blijven
meedraaien naast tests die de huidige integratie en HA-platformsetup uitvoeren.
