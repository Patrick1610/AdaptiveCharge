# AdaptiveCharge 5 — read-only preview

Deze beta bundelt planning rond een gedeelde laadpaal met afzonderlijke
voertuigprofielen voor Tesla en Volkswagen ID.3. Enphase Green blijft de basis;
de preview berekent aanvullend netladen naar een bereikdoel en vertrektijd.

**De integratie bestuurt niets.** Er is geen laad-, fase-, ampère- of limietactie
en geen schakelaar om besturing aan te zetten. Bestaande werkende scripts,
automations, helpers en Shortcuts blijven de echte laadregeling uitvoeren.

## Installeren via HACS

Voeg `Patrick1610/AdaptiveCharge` zo nodig toe als custom repository van het type
**Integration**. Zet bij die repository **Show beta versions** aan, open opnieuw
downloaden en kies expliciet `v5.0.0b1`. Met beta's ingeschakeld biedt HACS ook
latere beta's aan; gebruik geen onbedoelde automatische upgrade.
Home Assistant 2026.11.1 of nieuwer is vereist. Voeg vervolgens
**AdaptiveCharge Preview** toe onder Apparaten & diensten, met één hoofdinstantie
voor de laadpaal en Tesla/ID.3 als autosubconfiguraties.

Deze beta gebruikt het eigen domein `adaptive_charge_preview`, zodat hij geen
bestaande native laadregeling kan overnemen. Een oude AdaptiveCharge v4-entry
wordt niet automatisch gemigreerd of gebruikt. Deze release verwijdert geen reeds
geïnstalleerde v4-code of entries. HACS ruimt de oude domeinmap bij deze naamswijziging
niet betrouwbaar op. Controleer daarom vóór een eventuele herstart:

1. Deactiveer of verwijder **alleen** de oude `adaptive_charge`-integratie-entries.
   Laat de werkende native scripts, automations en helpers staan.
2. Controleer of `/config/custom_components/adaptive_charge` is achtergebleven.
   Verplaats die oude map buiten `custom_components` naar een eigen archief of
   verwijder haar pas na archivering. Het gedeactiveerd houden van oude entries
   blijft nodig; bestanden verplaatsen is geen entrymigratie.
3. Controleer na HACS-download alle previewbestandhashes met het bij de release
   gevoegde JSON-bewijs, zoals uitgelegd in [PREVIEW.md](docs/PREVIEW.md#installeren-naast-de-bestaande-regeling).

HACS kan bij vervanging om een herstart vragen. Plan die wanneer de huidige
laadopdracht mag eindigen: de bestaande native herstartveiligheid kan een
actieve opdracht annuleren. Een herstart is geen onderdeel van de previewbesturing.

Zie [werking, bronbindingen en bewijsgrenzen](docs/PREVIEW.md) voor de volledige
uitleg. De beta is softwarematig getest; dit is geen fysiek functioneel akkoord
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
