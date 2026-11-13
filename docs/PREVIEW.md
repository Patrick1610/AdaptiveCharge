# AdaptiveCharge Preview 5.0.0b1

Deze versie rekent alleen mee naast de bestaande laadregeling. Domein:
`adaptive_charge_preview`. De oude AdaptiveCharge v4-bron is gearchiveerd buiten het HACS-installatiepad.
De native EV-scripts/automations worden niet vervangen, uitgeschakeld of aangeroepen.
Getest op Home Assistant 2026.11.1; oudere HA-versies worden in deze beta niet ondersteund.

## Harde grens

- Er is geen actuator, fase-/ampèreregelaar, voertuigrefresh, inschakeloptie of
  netwerkclient in deze preview. `Besturing actief` blijft uit.
- De preview leest uitsluitend geselecteerde HA-entiteiten en publiceert eigen
  entiteiten. Instellingen en opdrachten staan alleen in eigen HA-Store-opslag.
- Preview Start, doelwijziging en Stop veranderen alleen de simulatie. Voor echt
  laden blijft de bestaande bediening gelden. Pas bestaande Shortcuts nu niet aan.
- Laden, herladen en verwijderen van de preview herstellen geen autolimiet en
  stoppen geen auto. Er wordt geen oude actieve opdracht overgenomen voor besturing.

## Indeling

Eén hoofdconfiguratie per laadpaal: IQ-kWh, onafhankelijk vermogen, aansluiting,
aansluitidentiteit, prijsbron, zonneprognose en huisverbruik. Voeg daaronder via
**Auto toevoegen** Tesla en ID.3 als afzonderlijke subconfiguraties toe.
Toevoegen, verwijderen en herconfigureren herlaadt uitsluitend de preview-entry;
de bestaande laadcontroller wordt daarbij niet herladen.

Per auto: SOC, beschikbare accu-kWh (geen totale capaciteit!), range, eigen
kabelmelding, limiet en eventueel echte capture-tijd, SOH en thuisstatus. Bij Tesla
betekent range het resterende Tessie-praktijkbereik; bij VW de volle referentierange
vóór SOH-correctie. Verkeerde of ontbrekende eenheden worden geweigerd.

De optionele limietbediening is alleen een capability voor het toekomstige ontwerp.
In de preview wordt nooit een limiet verhoogd. Het effectieve doel blijft begrensd
op de actuele autolimiet; `suggested_limit` toont afzonderlijk welke verhoging een
latere bestuurder zou moeten uitvoeren en bevestigen. Voor Patrick: Tesla wel
een capability, ID.3 geen capability.

Doel en vertrek mogen aan bestaande helpers gekoppeld worden: die worden alleen
gelezen. Eigen preview-nummers en vertrekentiteit maken lokale overrides. Een
overzichtssensor geeft per auto `vehicle_id`, status, reden, bronnen, waarschuwingen,
energierekening en laadvensters. Het ID is de stabiele HA-subentry-ID.
De previewactie `adaptive_charge_preview.follow_helpers` wist alleen lokale doel-
en vertrekoverrides en volgt weer de gekoppelde helpers, zonder een opdracht te starten.
`value_sources` laat zien welke waarden uit een override of gekoppelde helper komen.
Zonder geldige gekoppelde helpers weigert deze actie en blijven overrides staan.
Een bron die van een losse entiteit naar een HA-device verhuist kan in dezelfde
configuratie worden hergebonden. Een werkelijk vervangen registry-/device-identiteit
wordt conservatief geweigerd, ook als de naam gelijk bleef. Bewaar dan zo nodig de
previewdiagnostiek en voeg de laadpaal of alleen die auto als nieuwe previewconfiguratie
toe na verwijderen van de oude previewconfiguratie. Dit raakt geen bestaande
laadregeling; een oude previewopdracht wordt niet aan het nieuwe apparaat doorgegeven.

## Berekening

- Enphase Green is de basis. Alleen aanvullend netladen wordt gepland. Er is geen
  oude Adaptive Charge-zonnestroomregeling, importguard of geleerd capaciteitsmodel.
- Prijsregels bevatten begin/eindtijd, all-in inkoop en netto terugleverprijs in
  EUR/kWh. Configureer de attribuut- en kolomnamen expliciet. Voor Enever kunnen
  bijvoorbeeld `prices_today,prices_tomorrow` als attributenlijst en `time`/`price`
  als kolommen worden gekoppeld; het werkelijke
  15- of 60-minuteninterval is verplicht wanneer eindtijden ontbreken.
- Gaten of tegenstrijdige prijzen worden niet opgevuld. Een gat binnen de bekende
  reeks geeft Green en een waarschuwing. Bij een onbekende staart wordt energie die
  nog in die staart past uitgesteld; alleen het niet-uitstelbare deel krijgt bekende
  kwartieren. De gehele kostenschatting blijft dan onbekend. Uiterlijk starten wordt
  afzonderlijk bewaakt op basis van benodigd vermogen en reserve.
- Prognose: bestaand `wh_period`-attribuut (Wh per uur), timestamp in de sensorstate,
  maximaal twee uur oud. Huisvermogen wordt eraf getrokken; W en kW worden genormaliseerd.
  De preview ververst geen bron. Zonder netto terugleverprijs en met verwachte zon
  blijft de totale kostenschatting onbekend; nul is geen verzonnen teruglevertarief.
- Green-energie is een baseline. `Window.kwh` is totale verwachte laadenergie binnen
  het Manual-venster; `additional_kwh` is extra netenergie boven Green.
  `Window.cost` betreft alleen die extra netenergie; `Plan.cost` is de som hiervan
  plus afzonderlijke `green_cost` voor misgelopen teruglevering over de horizon.
  Green-kosten bij zon boven de totale doelbehoefte worden uniform naar behoefte
  geprorateerd: dit is een schatting, geen reconstructie van werkelijk verbruik.
- Defaults: 11 kW, 15 minuten vertrekreserve, 15% energiemarge per auto, 1 procentpunt
  SOC-terugval (instelbaar 0,1–10), vijf minuten aanhoudende terugval en drie minuten
  minimumduur voor losse blokken. Planning maximaal 48 uur vooruit.
- Korte restjes sluiten aan op het goedkoopste geschikte kwartier naast een bestaand
  blok. Energie blijft behouden; geen afronding naar een heel kwartier. De totale
  weggelaten energie is maximaal één ingestelde SOC-band, niet één band per fragment.
  De band wordt naar AC-kWh omgerekend met dezelfde verliesmarge als de behoefte.
- Een klein restant binnen de band mag ook vóór eerste doelbevestiging vervallen.
  Status: `within_tolerance`, geen bevestigde doelstop. Een significant kort restant
  zonder aansluitmogelijkheid blijft toegestaan met een zichtbare uitzonderingsreden.
  De tolerantie bewaart een afzonderlijke SOC-referentie: na een echte terugval van
  de ingestelde band gedurende vijf minuten kan opnieuw behoefte worden berekend.

## Opdracht en meetkwaliteit

Een gekoppelde native sessiehelper kan alleen als bron voor een previewkopie dienen.
Generatie en bronopdracht blijven apart herkenbaar. Er is maximaal één actieve
previewopdracht per laadpaal. Nogmaals Preview Start op dezelfde aansluiting reset
geen geleverde energie; Preview Stop onderdrukt het opnieuw kopiëren van dezelfde
native opdracht. Een echt nieuwe native opdracht kan daarna wel worden gevolgd.
De huidige native adapter herkent `v=tesla` en `v=id3`: automatisch meekijken vereist
precies één profiel van dat type. Bij meerdere gelijksoortige profielen wordt geen
auto geraden; `native_vehicle_identity_ambiguous` is dan zichtbaar. Expliciete lokale
previewopdrachten blijven per `vehicle_id` mogelijk.

Nulvermogen en anonieme laadschakelaar-OFF annuleren niet. Bewezen ontkoppeling of
een nieuwe aansluiting beëindigt de oude previewopdracht. Zonder aansluitidentiteit
wordt continuïteit niet aangenomen: `connection_identity_unknown`. De meterteller is
aan de geconfigureerde meter en de eigen aansluitidentiteit gebonden.
Een aansluitidentiteit kan uit de state of een expliciet gekozen attribuut komen,
bijvoorbeeld `sensor.iq_ev_charger_last_session` met `plugged_in_at`. Controleer dat
die bron de huidige aansluiting vertegenwoordigt: de identiteit moet stabiel én uniek
per fysieke aansluiting zijn, nooit een laadpaalserienummer of vaste kabelnaam.
Tijdelijk ontbrekende identiteit geeft wachten; dezelfde unieke identiteit herstelt
de oorspronkelijke meterboekhouding zonder nieuw budget. Een daadwerkelijk waargenomen
andere identiteit met te oude bevestiging blokkeert daarentegen de oude boekhouding,
ook als later dezelfde oude waarde terugkomt. Zo wordt onbekende energie niet alsnog
aan de verkeerde auto toegerekend.

Bronmeettijd is niet HA-ontvangsttijd. Herhaalde ontvangst of feed health bewijst geen
nieuwe voertuigmeting. Gebruik een expliciete capture-tijdentiteit alleen als deze
werkelijk bij de geselecteerde voertuigmetingen hoort. Afzonderlijke capture-attributen
gaan voor; meer dan twee minuten verschil tussen dynamische metingen maakt bronnen
incoherent: Tesla SOC/accu-energie/praktijkrange, VW SOC/accu-energie. Autolimiet,
SOH en VW-volle referentierange zijn instellingen/referenties en krijgen geen
verzonnen nieuwe capture-tijd. Voor nieuw budget en doelbevestiging is een geldige,
maximaal dertig minuten oude dynamische meetset nodig. Oude data kan wel een zichtbaar als
onbevestigd gemarkeerd rekenadvies opleveren; dit is geen toestemming om te laden.

Budgetboekhouding start bij de eerste betrouwbare previewwaarneming. Zij reconstrueert
geen eerder geladen energie uit een alleen als tekst opgeslagen oude native opdracht.
Dat begrenst vergelijkingen met een al lopende oorspronkelijke sessie.

Tijdelijk ontbrekende meter: wachten, daarna hervatten zonder nieuw budget.
Teruglopende of vervangen meter: gelatchte blokkade; herstel vereist expliciet
Preview Stop en een nieuwe Preview Start. Geen automatische herankering die eerder
verbruik kan weggeven. Een doelwijziging zonder geldige bronnen wordt uitgesteld,
met behoud van de oude boekhouding.

Gewone waarnemingen worden gebundeld opgeslagen (60 seconden); expliciete previewacties
en unload slaan onmiddellijk op. Grote bron-/planattributen blijven in HA zichtbaar en
in diagnostiek beschikbaar, maar worden niet meegekopieerd naar recorderattributen.

## Installeren naast de bestaande regeling

De beta wordt gepubliceerd vanuit `Patrick1610/AdaptiveCharge` als `v5.0.0b1`.
HACS installeert het enige top-level integratiedomein `adaptive_charge_preview`;
`legacy/v4/` wordt niet geïnstalleerd bij een nieuwe download. Een reeds aanwezige
`/config/custom_components/adaptive_charge`-map kan bij een domeinwijziging door HACS
blijven staan. Deze release verwijdert die map of bestaande v4-entries niet.
Archiveer of noteer de oude configuratie en verwijder vóór de HACS-domeinupgrade
alleen de oude `adaptive_charge`-entries. Ook gedeactiveerde entries blokkeren deze
upgrade met `domain_changed` / `repository_in_use`. Verwijder daarna de oude
HACS-installatie en download expliciet de v5-beta opnieuw. Archiveer de oude map
buiten `custom_components`. Laat de werkende native regeling
volledig staan. Er is geen automatische migratie of
activering van oude AdaptiveCharge-entries. De huidige native regeling blijft leidend.

Voor HACS: voeg de repository toe als Integration, schakel Show beta versions in,
en kies expliciet `v5.0.0b1` bij opnieuw downloaden. De beta-instelling blijft staan
en kan ook latere betaversies aanbieden. HACS vervangt bestanden in de previewmap;
de no-overwrite-garantie van het losse hulpmiddel geldt niet voor HACS. HACS haalt
zonder `zip_release` de bronboom op de gekozen tag op, niet het losse ZIP-asset.

Controleer daarom na HACS-installatie de geïnstalleerde bestanden tegen het
`adaptive_charge_preview-5.0.0b1.json`-bewijs uit de gecontroleerde release. Voer
onderstaande controle uit met de JSON op een eigen gekozen pad; dit leest alleen:

```python
import hashlib, json
from pathlib import Path
proof = json.loads(Path("/pad/naar/adaptive_charge_preview-5.0.0b1.json").read_text())
config = Path("/config")  # Vervang dit door de echte HA-configmap indien anders.
assert proof["domain"] == "adaptive_charge_preview"
for relative, expected in proof["files"].items():
    path = Path(relative)
    assert path.parts[:2] == ("custom_components", "adaptive_charge_preview")
    assert ".." not in path.parts
    assert hashlib.sha256((config / path).read_bytes()).hexdigest() == expected
actual = {p.relative_to(config).as_posix()
          for p in (config / "custom_components/adaptive_charge_preview").rglob("*")
          if p.is_file() and "__pycache__" not in p.parts}
assert actual == set(proof["files"]), "Onverwachte of ontbrekende previewbestanden"
print("Previewbestandhashes komen overeen")
```

Het afzonderlijke distributiearchief bevat uitsluitend `custom_components/adaptive_charge_preview`.
Kopieer die nieuwe map; installeer niet de hele repository over de bestaande
`adaptive_charge`-map. Alleen het losse Python-installatiehulpmiddel weigert een bestaande previewmap
te overschrijven en controleert paden en hashes vóór schrijven.

Het gecontroleerde hulpmiddel ondersteunt uitsluitend een bestaande HA-configmap
met `configuration.yaml`, bijvoorbeeld na afzonderlijke bron-/hashcontrole:

```sh
python3 tools/preview_bundle.py --install preview.zip --config /config --sha256 <gecontroleerde-SHA256>
```

Dit kopieert alleen de previewbestanden; het start of herstart HA niet.

Voeg **AdaptiveCharge Preview** toe onder Apparaten & diensten en vervolgens de auto’s.
Laat alle bestaande regelingen, helpers en Shortcuts staan. Als HA de nieuwe custom
integratie pas na een herstart ontdekt, is dat een installatieblokkade: deze opdracht
autoriseert geen herstart die de bestaande werking kan onderbreken. Geen cachepatches
of ongedocumenteerde runtime-injectie om die grens te omzeilen.

## Verifiëren

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-preview-test.txt
.venv/bin/python -m pytest tests -q
```

Nieuwe tests importeren de echte previewcode. Zij controleren energie- en
kostenbehoud, kwartieren, tolerantie, meterfouten, herstartdata, snelle autowissel en
echte HA-platformsetup/unload. Tijdens HA-observeracties blokkeren tripwires iedere
uitgaande service en elke bron-/helperwijziging. Een AST-controle verbiedt actuator-,
netwerk-, subprocess- en dynamische executiecalls in de preview.

Dit zijn softwaretests en read-only observaties, geen fysiek functioneel akkoord
voor doelstop of natuurlijk bijladen bij Tesla of ID.3. Live-installatie, bronnen en
readback worden afzonderlijk vastgelegd in het uitvoeringsdossier.

## Opleverstatus 13 november 2026, 00:58 CET

De 20 previewbestanden staan op HA en alle hashes zijn teruggelezen. Vier relevante
bestaande controller-/guardconfiguraties behielden exact dezelfde hash; de bestaande
ID.3-opdracht bleef actief met circa 10,64 kW bij de laatste waarneming. De volledige
softwaretestreeks slaagde: 934 tests, waarvan 133 nieuwe previewtests. Core- en
HA-review: GO, zonder open critical/high/medium.

**De preview loopt live nog niet mee.** Het nieuwe domein ontbreekt in de lopende
HA-loadercache. Er is geen herstart/cachepatch uitgevoerd en nog geen previewconfiguratie
aangemaakt. De besturing blijft onmogelijk in dit pakket. Na een afzonderlijk gekozen
reguliere HA-herstart kan uitsluitend **AdaptiveCharge Preview** worden toegevoegd,
met de auto’s eronder; bestaande bediening en Shortcuts blijven gelden.

Bewijs staat op HA in
`/config/ev_v2_control_20261106/adaptive_charge_preview_20261113/`:
`install_receipt.json`, `runtime_outcome.json` en `observations.jsonl`.
