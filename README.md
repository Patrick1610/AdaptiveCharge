# AdaptiveCharge 5.1 — uitschakelbare laadbesturing

Deze beta bundelt planning rond een gedeelde laadpaal met afzonderlijke
voertuigprofielen voor Tesla en Volkswagen ID.3. Enphase Green blijft de basis;
de integratie plant aanvullend netladen naar een bereikdoel en vertrektijd.

Zonder bruikbare prijzen plant hij één aaneengesloten blok dat eindigt bij vertrek.
Drie uur laden voor 06:15 begint dan om 03:15, zonder extra prijsplanreserve.
Terugkerende prijzen herplannen dezelfde opdracht; de energieboekhouding blijft staan.

Elke auto heeft een **Laadplanning**-sensor: de staat is de resterende geplande
laadduur in minuten; `quarters` bevat per kwartier de geplande minuten en kWh,
inclusief nulkwartieren tussen blokken. Zie [het kaartvoorbeeld](examples/planning-card.yaml).
Vervang daarin de voorbeeldentiteit door de werkelijke planningssensor van je auto.
De kWh zijn de totale geplande AC-laadenergie tijdens Manual, inclusief eventuele
zon in die vensters. Autonome Green-opbrengst buiten de vensters wordt niet voorspeld.

**De hoofdschakelaar staat standaard uit.** Uit betekent alleen meerekenen.
Met de hoofdschakelaar aan en een expliciete Start bestuurt de integratie de
geselecteerde auto via de bestaande Enphase-/voertuigintegraties. De oude
controller moet uit staan; actieve native opdrachten worden niet overgenomen.
Fasen en ampères blijven bij Enphase. Zie [bediening en testgrenzen](docs/CONTROL.md).

## Installeren via HACS

Voeg `Patrick1610/AdaptiveCharge` zo nodig toe als custom repository van het type
**Integration**. Zet bij die repository **Show beta versions** aan, open opnieuw
downloaden en kies expliciet `v5.1.0b7`. Met beta's ingeschakeld biedt HACS ook
latere beta's aan; gebruik geen onbedoelde automatische upgrade.
Home Assistant 2026.11.1 of nieuwer is vereist. Voeg vervolgens
**AdaptiveCharge** toe onder Apparaten & diensten, met één hoofdinstantie
voor de laadpaal en Tesla/ID.3 als autosubconfiguraties.

Deze beta behoudt het domein `adaptive_charge_preview` voor een gewone update van
5.0.0b1. Bestaande entries, voertuigsubentries en entiteitsidentiteiten blijven staan.
Een oude AdaptiveCharge v4-entry
wordt niet automatisch gemigreerd of gebruikt. Deze release verwijdert geen reeds
geïnstalleerde v4-code of entries. HACS ruimt de oude domeinmap bij deze naamswijziging
niet betrouwbaar op. Controleer daarom vóór een eventuele herstart:

1. Noteer of archiveer de oude configuratie en verwijder **alleen** de oude
   `adaptive_charge`-integratie-entries. Alleen deactiveren is voor deze upgrade
   niet voldoende: HACS weigert een domeinwijziging zolang een oude entry bestaat.
   Verwijder vervolgens de oude HACS-installatie en installeer de geselecteerde
   v5-beta opnieuw. Laat de werkende native scripts, automations en helpers staan.
2. Controleer of `/config/custom_components/adaptive_charge` is achtergebleven.
   Verplaats die oude map buiten `custom_components` naar een eigen archief of
   verwijder haar pas na archivering. Het gedeactiveerd houden van oude entries
   blijft nodig; bestanden verplaatsen is geen entrymigratie.
3. Controleer na HACS-download alle previewbestandhashes met het bij de release
   gevoegde JSON-bewijs, zoals uitgelegd in [PREVIEW.md](docs/PREVIEW.md#installeren-naast-de-bestaande-regeling).

HACS kan bij vervanging om een herstart vragen. Plan die wanneer de huidige
laadopdracht mag eindigen: de bestaande native herstartveiligheid kan een
actieve opdracht annuleren. Een herstart is geen onderdeel van de previewbesturing.

Zie [de volledige besturing](docs/CONTROL.md) en de
[historische 5.0-previewberekeningen](docs/PREVIEW.md). Softwaretests zijn geen fysiek functioneel akkoord
voor doelstop of natuurlijk bijladen bij beide auto's.

## Ontwikkelen

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-preview-test.txt
.venv/bin/python -m pytest tests -q
```

`custom_components/` bevat uitsluitend de nieuwe preview, conform de
HACS-repository-indeling. De ongewijzigde v4-bron staat als archief onder
`legacy/v4/`, met de oude uitleg in [LEGACY_V4.md](docs/LEGACY_V4.md).
Die archiefcode wordt niet door HACS geïnstalleerd. Historische tests blijven
meedraaien naast tests die de echte preview en HA-platformsetup uitvoeren.
