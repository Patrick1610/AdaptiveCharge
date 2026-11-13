# Uitvoeringshandover

Actuele status op 13 november 05:07 CET: PUBLISHED_PREVIEW_HACS_UPGRADE_AWAITING_ENTRY_REMOVAL_CONFIRMATION via OWNER_DIRECT_EXCEPTION.
De eerdere custom-agentpreflight blijft historisch mislukt; geen formele v4-plus-closure.

De canonieke bron is Patrick1610/AdaptiveCharge, branch codex/adaptive-charge-v5. Pre-release v5.0.0b1 staat op commit 31d521576a89b8140b11b95b940af7953b32db48. PR #56 naar develop staat open als draft; geen beschermde branch is samengevoegd. ZIP en JSON zijn teruggedownload en alle 21 bestandhashes zijn tegen de exacte tagboom geverifieerd. De 20 runtime/configbestanden zijn gelijk aan het eerder gereviewde pakket; één bestaand projecticoon is toegevoegd.

Alle 935 tests slagen (134 preview, 801 historisch). GitHub Validate (hassfest/HACS) en Preview tests zijn groen op de releasecommit. Packagingreview 4f2fed0c seq3: GO, nul critical/high/medium. Twee HACS-catalogusmetadata-checks, description/topics, zijn expliciet buiten scope van deze custom-repositorybeta; installatiechecks blijven actief. De documentatiecorrectie voor de domeinupgrade kreeg afzonderlijk GO: 766e0f35 seq1.

Patrick heeft verduidelijkt dat de oude AdaptiveCharge-installatie mag worden vervangen; de werkende native laadregeling moet behouden blijven. Oude v4-bron staat in Git onder legacy/v4, buiten het HACS-installatiepad. Op HA zijn 31 oude bestanden geverifieerd en gearchiveerd onder de eigen previewmap. De oude HA-entry ‘AdaptiveCharge- Model Y’ staat nog disabled_by=user/not_loaded en is niet verwijderd.

De ondersteunde HACS-upgrade naar adaptive_charge_preview weigert domain_changed zolang de oude adaptive_charge-entry bestaat, ook gedeactiveerd. Hiervoor staat een concrete vraag om actiebevestiging open wegens de Computer Use-regel voor onomkeerbare entryverwijdering. Behandel een onbeantwoorde vraag niet als toestemming. Na bevestiging alleen die exacte oude entry verwijderen, de oude HACS-installatie verwijderen en dezelfde beta opnieuw downloaden. Nooit de native regeling of helpers verwijderen. Daarna de 21 releasebestandhashes én de volledige bestandenset verifiëren, vervolgens één laadpaalentry met Tesla/ID.3-subentries configureren. Het bindingsplan staat in het HA-commissioningreceipt. Geen directe .storage-wijziging of cachepatch.

Laatste HA-controle 04:07:19 UTC: vier controller/guardconfiguraties hebben exact dezelfde hash, native ACTIVE staat aan met generatie 29; HomeWizard meet 0,006005 kW. Geen herstart, native actuator-, helper-, limiet-, Start/Stop- of herlaadactie aangevraagd. Nog nul previewconfigentries en geen meedraaiende previewruntime. Besturing is onmogelijk in dit pakket. Geen nieuwe heartbeat/daemon of fysieke proef.

Het eerste bestandeninstallatiebewijs en de historische uitkomst blijven ongewijzigd in de JSON. Publicatie en checkpoint staan afzonderlijk onder publication_20261113. HA-bewijsmap: /config/ev_v2_control_20261106/adaptive_charge_preview_20261113, met install_receipt.json, runtime_outcome.json, observations.jsonl en hacs_commissioning_receipt_20261113.json.
