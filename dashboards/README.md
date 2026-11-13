# AdaptiveCharge-preview in een bestaand dashboard

Deze configuratie vervangt uitsluitend het bestaande dashboard `ev-v2-proef`
(oude zijbalktitel `EV laadplan · V2`) en geeft het de titel `EV · AdaptiveCharge`.
De drie bestaande viewpaden `proef`, `tesla` en `id3` blijven behouden.
`dashboard-stormbreaker` / `EV laden` blijft de bestaande echte bediening en wordt
niet gewijzigd. Maak geen nieuw dashboard, helpers of sensoren aan.

De bron is `tools/preview_dashboard.py`; genereer `dashboards/preview.json` hiermee
opnieuw na een wijziging. De bronbindingen zijn de tien preview-entiteiten van
Patricks bestaande HA-instantie. Bij hernoemen van die entiteiten moeten de
bindingen hier worden aangepast. De generator schrijft alleen een lokaal bestand,
maakt geen verbinding met HA en doet geen installatie.

```sh
.venv/bin/python tools/preview_dashboard.py
```

Er zijn uitsluitend ingebouwde Markdown- en entiteitskaarten en navigatielinks.
De entiteitskaarten wijzigen alleen de eigen previewwaarden voor bereik, vertrek
en terugval. Er zijn geen laadknoppen of service-acties voor native scripts,
helpers, auto, laadpaal of limieten. De besturingsstatus wordt uit de integratie
gelezen; een ontbrekende status wordt niet als bevestigd uit weergegeven.

Bewaar vóór installatie de twee huidige dashboardconfigs en metadata via de
publieke Lovelace-API. Valideer de templates en aanwezige bronentiteiten. Schrijf
alleen de configuratie van `ev-v2-proef` en wijzig alleen diens zijbalktitel;
behoud url, id, opslagmodus en toegangsinstellingen. Controleer de teruggelezen
config, het ongewijzigde `EV laden`, de ongewijzigde dashboard-ID-set en native
regelconfiguraties. Backup en readback horen in de eigen HA-bewijsmap, niet in
publieke bronbestanden.

## Uitgevoerd op 13 november 2026

Het bestaande `ev-v2-proef` is vervangen en heet nu `EV · AdaptiveCharge`.
Overzicht, Stormbreaker en Éowyn zijn in de HA-frontend bekeken; alle tien unieke
Markdown-templates renderden via HA en alle tien entiteitsbindingen bestaan.
De previewbesturing bleef uit. Ontbrekende tarieven en onbekende/verouderde
voertuigmeettijden blijven zichtbaar als beperking van het advies.

De teruggelezen configuratie komt exact overeen met `preview.json` (SHA256
`f6f0bda9b71c383574e9ea617531c7c80de2b13b1a5e6fb4d0fd5fc16c6fd520`).
`EV laden` bleef exact gelijk, evenals alle andere dashboardmetadata, het aantal
dashboards (tien totaal, twee EV-dashboards), vier native regelconfiguraties en
de identiteit/generatie van de lopende native opdracht. Er zijn geen helpers,
sensoren of dashboards aangemaakt en geen laadacties uitgevoerd.

Externe dashboardreview: `21fa85da-1aa9-448c-969f-634c20bae9f5`, sequence 2, GO.
Backup, templatevalidatie, API-readback en afgerond installatiebewijs staan alleen
in de eigen HA-map
`/config/ev_v2_control_20261106/adaptive_charge_preview_20261113/dashboard_reuse_20261113/`.
