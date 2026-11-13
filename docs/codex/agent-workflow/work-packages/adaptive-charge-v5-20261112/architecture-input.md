---
workflow_version: agent-workflow/v4-plus
project_id: adaptive-charge
workflow_unit_id: adaptive-charge-v5-20261112
input_status: IMMUTABLE
base_binding: b2ea3e0ff2cc5491722f217f3c69a6372c40fb4a
---

# Goedgekeurde architectuurinput

Autoriteit: Patrick heeft het voorgestelde plan in deze chat aanvaard met "Implement the proposed plan." De onderstaande inhoud legt dat ontwerp en de direct voorafgaande antwoorden vast. Geen nieuwe productkeuzes worden hiermee geautoriseerd.

## Productcontract

- Bestaande repository Patrick1610/AdaptiveCharge; nieuwe hoofdversie als aparte beta op een featurebranch. Geen actieve laadbesturing naast de bestaande controller tijdens ontwikkeling.
- Een hoofdinstantie per laadpaal; voertuigen als afzonderlijke HA-subconfiguraties. Een uitvoerder en maximaal een actieve opdracht per laadpaal.
- Green als basis: Enphase bestuurt zonnestroom, fasen en ampères. Green blijft toegestaan buiten netlaadblokken, na doelbereik en na expliciete opdracht-Stop.
- Geen oude eigen zonneregelaar, overschotfilters, importguard, vaste nachtvensters, dubbele bereikgrenzen of zelflerende capaciteit/verliesregeling.
- Voertuigdata via bestaande HA-integraties; geen nieuwe voertuigcloud-authenticatie.
- Tesla op Tessie-praktijkrange; ID.3 op berekende planningsrange en echte SOC-doelbevestiging. Bronmeettijd en ontvangsttijd strikt onderscheiden.
- Per auto doelbereik, vertrek, SOC-terugval 0.1-10 procentpunten (default 1), 5 minuten aanhoudende terugval; energiemarge default 15 procent per auto, geen autokalibratie.
- Optionele schrijfbare autolimietentiteit per auto. Bij Patrick Tesla wel, ID.3 niet. Zonder schrijver: begrenzen op bestaande limiet. Met schrijver: tijdelijk benodigd SOC plus 1 procentpunt, max 100 en entiteitsgrens; uitsluitend eigen ongewijzigde limiet herstellen bij passende aansluiting/eigendom.
- Gedeeld: Enphase-telemetrie en bediening, IQ-energie en onafhankelijk HomeWizard-vermogen, prijzen, huisverbruik, zonneprognose; rekenvermogen 11 kW en vertrekreserve 15 min als defaults.
- Aanvullend netladen economisch plannen. Green- en Manual-energie verminderen dezelfde resterende behoefte. Kosten uit netinkoop en misgelopen terugleveropbrengst; geen vast jaartal in rekenkern. Zonder prijzen Green tot uiterlijk veilige start; direct verzoek onmiddellijk. Iedere minuut en bij relevante updates herplannen, equivalente vensters stabiel houden.
- Los blok minimum 3 minuten. Kortere energie eerst aansluiten in goedkoopste geldige aangrenzende tariefkwartier aan bestaand blok, energie behouden. Een kort restblok binnen de ingestelde SOC-band vervalt ook voor eerste doelbereik, status klein restant binnen tolerantie, nooit onterecht doel bevestigd. Significante rest zonder geldige aansluiting mag korter met zichtbare uitzonderingsreden.
- Opdracht los van aansluiting en fysiek laadblok bewaren. Nulvermogen, anonieme OFF en fasepauze annuleren niet en geven op zichzelf geen Green-commando.
- Stop trekt hervattingsrecht eerst in, beëindigt eigen Manual, draagt gecontroleerd aan Green over. Ontkoppeling van eigen voertuig of bewezen nieuwe aansluiting beëindigt oude opdracht, ook zonder algemene Enphase-OFF. Oude meldingen/timers mogen nieuwere opdracht/andere auto niet beïnvloeden.
- Generatie, meterbasis, gebruikte energie, doel, vertrek, aansluitidentiteit en eigen limietwijziging duurzaam bewaren. Doelwijziging/pauze resetten geen energie. Herstart pas hervatten na herbeoordeling van geldige bronnen, voertuig, generatie en aansluiting.
- API: adaptive_charge.start (vehicle_id, optioneel target_range_km/departure); adaptive_charge.set_target (vehicle_id, target_range_km en/of departure); adaptive_charge.stop (vehicle_id). Inactieve doelwijziging start niet. Ontbrekend/verstreken vertrek bij Start betekent direct.
- Status onderscheidt wachten gegevens, gepland, aanvullend laden, verificatie, doelbewaking, tolerantie en geblokkeerd, plus aparte fysieke meetstatus.
- Bestaande configuratie/historie behouden; geen ongetoetste actieve opdracht importeren. Dashboard/Shortcuts naar nieuwe API en entiteiten, oude helpers geen tweede waarheid.

## Verificatie en uitrol

- Pure rekenkern en opdrachtbeheer daadwerkelijk testen, historische situaties als replays; bestaande spiegeltests gelden niet automatisch als tests van nieuwe runtime.
- HA-configuratie, subentries, opslag, acties en diagnostiek testen. Jev-baseline na eerste coherente implementatie en na relevante verbeteringen; onafhankelijke Claude-review na implementatiefasen.
- Read-only shadow ondersteund, zonder actuatorwrites. Omschakeling alleen na goedgekeurde kandidaat en bewezen rust; daarna nieuwe expliciete Start en afzonderlijk fysiek bewijs Tesla/ID.3. Geen fysieke proef claimen zonder uitvoering.
- Verplichte scenario's: doel omhoog/omlaag zonder budgetreset; Green-energie; fasepauzes; Stop; gewone/snelle autowissel; doelstop versus energieguard; hysterese; korte vensters/tolerantie/uitzondering; limit raise/restore versus read-only ID.3; bronuitval, vertraagde gegevens, teruglopende meter, HA-herstart.

## Bestaande bronnen

- Adaptive Charge develop basis: b2ea3e0ff2cc5491722f217f3c69a6372c40fb4a
- Native EV-bron read-only: /Users/patrick/Documents/Codex/2026-10-14/beschikbare-stroom-excl-autolader; src/build_config.py, native/ev_v2.jinja, docs/INSTALLATIE.md en docs/OPTIMALISATIES.md.
- Die werkboom bevat ongecommitte werk; geen terugdraaiing of overschrijving.

## Huidige uitvoeringsgrens

Geen SEALED charter of productwriter geactiveerd. Runtimepreflight vereist een werkelijk gebonden custom agent_role en gaf PLATFORM_ACTION_REQUIRED. Herstel of expliciete eigenaarsexceptie vereist voordat productcode verandert.
