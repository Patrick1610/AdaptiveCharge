# AdaptiveCharge in het bestaande EV-dashboard

`EV laden` / `dashboard-stormbreaker` blijft de oude native bediening.
`EV · AdaptiveCharge` / `ev-v2-proef` is de bestaande tweede plek voor de nieuwe
integratie. Maak geen derde dashboard. De viewpaden `proef`, `tesla`, `id3`, URL,
ID, opslagmodus en toegangsinstellingen blijven behouden.

De bron is `tools/preview_dashboard.py`; genereer `dashboards/preview.json` na een
wijziging opnieuw. De installatiegebonden entity-ID's blijven bewust zichtbaar;
controleer ze tegen de publieke registry na een HACS-upgrade. De generator schrijft
uitsluitend een lokaal bestand en maakt geen verbinding met HA.

```sh
.venv/bin/python tools/preview_dashboard.py
```

Vanaf 5.1 toont Overzicht de echte gedeelde hoofdschakelaar. Beide autoviews tonen
het eigen doel, vertrek, terugval en de eigen Start/Stop-knoppen. Met de hoofdschakelaar
uit blijft Start een lokale simulatie. Met de hoofdschakelaar aan geeft Start echte
uitvoeringsbevoegdheid, na de interlocks. Een doelwijziging zonder actieve opdracht
start niets. De oude regeling moet uit staan; zij wordt niet door dit dashboard
omgeschakeld. Er zijn uitsluitend ingebouwde Markdown- en entiteitskaarten.

Bewaar vóór iedere dashboardupdate de twee configs en alle dashboardmetadata via
Lovelace. Valideer templates en de bestaande entiteiten; schrijf alleen de config
van `ev-v2-proef`. Verifieer readback, ongewijzigd `EV laden`, alle andere metadata,
dezelfde dashboard-ID-set en native regelconfigs. Runtime-/installatiebewijs blijft
in de eigen HA-map, buiten publieke bronbestanden.

De 5.1-config is pas live na afzonderlijke installatie/readback; onderstaande
historische verificatie bewijst uitsluitend de eerdere read-only dashboardvariant.

## Historische verificatie van de 5.0-variant, 13 november 2026

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
