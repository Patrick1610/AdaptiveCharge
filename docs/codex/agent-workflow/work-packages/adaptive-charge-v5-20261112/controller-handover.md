# Uitvoeringshandover

Status: IMPLEMENTED_READ_ONLY_FILES_INSTALLED_ACTIVATION_PENDING via OWNER_DIRECT_EXCEPTION.
De eerdere custom-agentpreflight blijft historisch mislukt; er is geen geslaagde v4-plus-rolbinding of formele workflowclosure.

Productcode: afzonderlijk `adaptive_charge_preview` 5.0.0b1; 934 tests geslaagd (133 preview, 801 legacy). Onafhankelijke core-review GO seq7 en HA-review GO seq5; nul open critical/high/medium. Details en bronbinding: implementation_result.json en candidate-files.json.

Op HA zijn uitsluitend de 20 nieuwe previewbestanden geïnstalleerd en elk teruggelezen op SHA256. Vier bestaande controller-/guardconfiguraties hielden exact dezelfde hash inclusief JSON-volgorde. De bestaande ID.3-opdracht gen29/u1794521275 bleef actief; circa 10,64 kW bij de laatste waarneming. Geen actuator-, helper-, limiet-, Stop-, Start-, reload- of herstartactie op de bestaande regeling.

Concrete blokkade: de lopende HA-loadercache herkent het nieuwe domein nog niet. Geen HA-herstart of cachepatch uitgevoerd. Er zijn nog geen previewconfig-entry of live previewentiteiten; de preview loopt dus nog niet mee. Installatiebewijs: /config/ev_v2_control_20261106/adaptive_charge_preview_20261113/{install_receipt.json,runtime_outcome.json,observations.jsonl}.

Vervolg na een afzonderlijk gekozen reguliere HA-herstart: eerst receipt, bronhashes en ontdekking controleren; niet opnieuw installeren over de bestaande previewmap. Voeg dan uitsluitend de read-only preview-entry met voertuigsubentries toe. Laat native besturing, helpers, autolimieten en Shortcuts intact. Besturing is in dit pakket onmogelijk, niet alleen uitgeschakeld. Geen nieuwe heartbeat/daemon of fysieke proef geautoriseerd in deze oplevering.
