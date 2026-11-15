# Voorstel: Zonneplan en economisch laden met zonnestroom

Status: uitwerking van de door Patrick gevraagde uitbreiding van 14 november 2026.
5.2.0b1 is gepubliceerd en in HA geïnstalleerd; de fysieke nachtproef loopt.
5.2.0b2 corrigeert de daarbij gevonden terugkoppeling van voertuigstroom naar
de planning. De volledige fysieke acceptatie staat nog open.
De aanpak voor onbekende verre tarieven is aangescherpt na Patricks expliciete
opdracht: een instelbare aftrek in cent/kWh onder het bekende gewogen gemiddelde,
met voorrang voor een haalbaar vertrekdoel. Dit document vervangt geen
bestaande laadopdracht of acceptatie-uitkomst.

Ontwerpuitgangspunt aangescherpt door Patrick: beoordeel de economische functie
voor **2027 zonder saldering**. De resterende zes weken van 2026 rechtvaardigen
geen afzonderlijke salderingsoptimalisatie. De uitleg over 2026 hieronder blijft
achtergrond, geen vereiste voor de gekozen uitbreiding. Dit wijzigt geen actuele
HA-tarieven of historische proefuitkomsten.

## Advies en afbakening

Patrick heeft de compacte B-uitbreiding gevraagd voor dezelfde iteratie: binnen
een eigen opdracht Green economisch uitstellen op basis van aantoonbaar
beschikbare goedkopere vervangende laadruimte. Daarnaast heeft hij zonder
opdracht een afzonderlijke prijsfilter gevraagd op basis van de gemiddelde
inkoopprijs van vandaag en, indien beschikbaar, morgen. Met opdracht wordt dat
gemiddelde alleen gebruikt voor vroeg inkopen bij een onvolledig bekende
prijshorizon; bij volledige dekking of urgentie vervalt die voorkeursgrens. Het eerdere brede B-ontwerp hieronder is niet volledig nodig voor
deze compacte variant. Vermogensinschatting en storingsherstel blijven
randvoorwaarden voor betrouwbare uitvoering.

Er zijn drie verschillende opleveringen:

| Variant | Gedrag | Betekenis |
| --- | --- | --- |
| Huidig | Green als vaste basis; aanvullende netenergie op goedkope inkoopmomenten | Geen actieve keuze om Green te weigeren voor terugleveropbrengst |
| A: contractbewust rekenen | Correcte inkoop/terugleverwaardering, zichtbare aannames, eventuele alternatieven uitsluitend als advies | Past bij de huidige Green-basis; realiseert niet automatisch de berekende arbitrage |
| B: actief economisch laden | Ook tijdelijk niet laden om zonnestroom terug te leveren, later laden wanneer dat aantoonbaar gunstiger is | Nieuwe optionele laadstrategie met een eigen tijdelijke pauze, zonder de opdracht te beëindigen |

Geen van deze varianten betekent V2G: alleen zonnepanelen leveren terug, nooit
de autoaccu. Ook B heeft geen eigen regeling van ampères, fasen of omvormers
nodig; Enphase blijft daarvoor verantwoordelijk. Wel is B meer dan alleen
kostenadministratie: hij bepaalt óf Green op een bepaald moment mag laden.

## Wat de huidige code werkelijk doet

Bronbinding: commit `16de75efac5e59d5b006626a55a50897724f794f` (5.1.0b8).

- `core/planner.py:plan()` trekt voorspelde netto Green-energie van de behoefte
  af en sorteert aanvullende laadruimte op `buy`. Het argument `sell` verandert
  die rangschikking niet; het wordt achteraf gebruikt voor `green_cost`.
- `sources.py:price_rows()` kan een expliciete netto terugleverkolom uit dezelfde
  prijsregels lezen, maar leidt geen Zonneplancontract uit de inkoopprijs af.
  Een ontbrekende kolom geeft een waarschuwing, geen verzonnen terugleverprijs.
- De prognose is een bestaande uurreeks; een recent gemeten huisvermogen of
  ingestelde fallback wordt afgetrokken. Dat is nog geen huishoudprognose voor
  elk toekomstig kwartier. Green onder het startvermogen van de auto, schakeltijd
  en werkelijk opneembare zonne-energie zijn nog niet volledig gemodelleerd.
- `core/schedule.py` toont Manual-vensters; autonome Green buiten die vensters
  staat niet als toekomstig laadblok in de huidige kwartierprojectie.
- `control.py` kent de bestaande Green/Manual-overdracht. Er is geen bewaarde
  economische pauzetoestand die Green bewust onderdrukt.

Alleen een terugleverprijs invullen maakt de huidige planner daarom nog geen
volledige zelfverbruik-versus-terugleveroptimalisatie.

## Afweging: Enphase AI in plaats van eigen economisch uitstel

Patricks screenshots van 14 november tonen onder AI-optimalisatie de EV-keuzes
Slim opladen (met doel), Gepland en Handmatig; Green vervalt in die weergave.
Dat is geen gelijkwaardige vervanging van de gewenste basis: zonder eigen
laadopdracht wel overschot laden, zonder een verplicht nieuw doel te verzinnen.

De Europese Enphasehandleiding onderscheidt Green onder Eigen verbruik van Smart
Charging onder AI-optimalisatie. Smart kan ook net- en batterijenergie gebruiken.
De documentatie beschrijft beschikbaarheid afhankelijk van de installatie; een
zichtbaar AI-systeemprofiel alleen bewijst geen passend EV-basisgedrag.
Bron: [Enphase IQ EV Charger 2 app setup guide, secties 2, 4 en 5](https://enphase.com/sites/default/files/2025-12/usg-00078-en-eu-iq-ev-charger-2-setup-guide-integrated.pdf).

Voorkeur voor Patricks beschreven gebruik: behoud Eigen verbruik/Green en verbeter
AdaptiveCharge gericht. Enphase AI is een alternatief als de gebruiker juist de
doelgerichte EV-planning aan Enphase wil delegeren, geen kosteloze transparante
onderlaag van de bestaande Green/Manual-regelaar. Die regelaar geeft zijn eigen
bereikdoel en vertrek momenteel niet aan Enphase door.

Combineren vergt expliciete modusoverdracht en herstel naar AI/Smart in plaats van
blind Green; de huidige uitvoerder kent Green/Manual. Bovendien mag PV-prognose
niet als gegarandeerde EV-energie tellen als AI die zon bewust exporteert. Ook
zonder doel en na Stop moeten de twee regelingen hetzelfde afgesproken gedrag
hebben. De zichtbare Enphasevelden voor vaste tariefopslagen modelleren niet
automatisch de daglichtgebonden, begrensde 10% Zonnebonus of jaarlijkse saldering.
Geen AI-profiel of tariefinstelling is in deze ontwerpafweging gewijzigd.

## Contractgegevens en bronnen

Eén contractprofiel hoort bij de aansluiting/laadpaal, niet per auto. Auto's
houden hun eigen doel, vertrek, actuele limiet, verliesmarge en opdracht.

| Invoer | Hergebruik / nog vast te stellen |
| --- | --- |
| Werkelijke facturatie per uur of kwartier | Zonneplan meldt inmiddels dat sinds 1 augustus 2026 iedereen op kwartieren wordt afgerekend. Dit verduidelijkt de oudere januari-productvoorwaarden waarin beide stonden. Kwartiercontract als actuele Zonneplanbasis gebruiken; geen persoonlijke overeenkomst gelezen. |
| Inkooptarieven vandaag/morgen | Bestaande Enever-Zonneplanfeed hergebruiken; expliciet vastleggen of belasting, btw en inkoopvergoeding al inbegrepen zijn. |
| Terugleverprijsopbouw | Marktprijs plus contractuele terugleveropslag; bonus afzonderlijk. Geen afleiding door de volledige all-in inkoopprijs met 1,10 te vermenigvuldigen. |
| Inkoop-/terugleveropslag | Publieke Zonneplaninformatie noemt € 0,02/kWh inclusief btw; eigen contract is bepalend. Niet nogmaals toevoegen aan een complete feed. |
| Belastingregime en ingangsdatums | 2026 met saldering onderscheiden van 2027 zonder wettelijke saldering; btw-basis en toepasselijk belastingtarief expliciet. |
| Zonnebonus | In aanmerking komen, daglichtvenster, percentage, positieve grondslag en resterende jaarruimte. |
| Exportteller voor bonus | Betrouwbare teller vanaf kalenderjaarbegin of een bevestigd anker. Een onvolledig HA-jaar mag geen volledige jaarteller voorstellen. Exact vaststellen welke teruggeleverde kWh Zonneplan voor de grens meetelt. |
| PV-prognose en huislast | Bestaande bronnen hergebruiken; huislast moet exclusief EV zijn. Andere grote verbruikers kunnen de verwachting wijzigen. |
| Werkelijk beschikbare laadcapaciteit | De afzonderlijk voorgestelde normal/safe/voertuigbeperking gebruiken; geen vaste 5,5 kW-testoverride. |
| Werkelijke afname en teruglevering | Aansluitingsmeter/P1 voor financiële nacontrole; laadpaalmeter voor de eigen laadenergie. Alleen de EV-meter bewijst geen terugleverinkomsten. |

De Zonneplanintegratie vervangen is hiervoor niet noodzakelijk. Contractlogica
kan op de bestaande feed worden toegepast, mits de veldbetekenis bekend is.
Bij een eventueel ander/oud uurcontract moeten vier kwartieren hetzelfde
werkelijke uurtarief krijgen; financieel rangschikken op fictieve kwartiertarieven
is dan niet correct. Dit is een generieke adapterregel, geen actuele aanwijzing
dat Patrick nog een uurcontract heeft.

Publieke voorwaarden gecontroleerd op 14 november 2026:

- Zonneplan noemt marktprijs + € 0,02/kWh en 10% extra over die positieve
  grondslag. De 10% geldt tussen zonsopkomst en zonsondergang, tot 7.500 kWh
  teruglevering per kalenderjaar, niet over Powerplay-teruglevering uit de
  Zonneplanthuisbatterij. De bonus geldt ook buiten het saldeerbereik.
- Zonneplan vermeldt geen terugleverkosten. Dit is geen verificatie van de eigen
  overeenkomst of toekomstige wijzigingen.
- De wettelijke saldering stopt op 1 januari 2027. De tarieflogica moet op datum
  schakelen en kan een contract dat over de jaargrens loopt niet uniform behandelen.

Bronnen:

1. [Zonneplanproductvoorwaarden 1 januari 2026, art. 6.1–6.2 en 7](https://zonneplan-site.s3.amazonaws.com/uploads/2026/01/Productvoorwaarden-1-1-2026.pdf).
2. [Zonneplan: prijsopbouw en saldering, gewijzigd 13 november 2026](https://www.zonneplan.nl/energie/informatie-over-energiecontracten/terugleververgoeding-bij-zonnepanelen).
3. [Zonneplan: Zonnebonusvoorwaarden](https://www.zonneplan.nl/energie/zonnepanelen).
4. [ACM ConsuWijzer: salderen en terugleveren](https://consument.acm.nl/elektriciteit-en-gas/duurzame-energie/wat-is-salderen).
5. [Zonneplan: sinds 1 augustus 2026 voor iedereen kwartierprijzen](https://www.zonneplan.nl/blog/kwartierprijzen-bij-zonneplan).
6. [Zonneplan: prijzen voor morgen verschijnen tussen 13:00 en 15:00](https://www.zonneplan.nl/de-zonneplan-app-energie).

## Kostenmodel

Voor het gekozen 2027-model zijn er twee verschillende marginale prijzen:
netinkoop inclusief belasting en btw, en netto teruglevering inclusief toepasselijke
bonus maar zonder salderingscredit. Noem die prijzen nu B en S; noem de prijs van
de daadwerkelijk beschikbare vervangende energie vóór vertrek F. Bij vergelijkbaar
laadrendement en voldoende capaciteit kan Green beide andere keuzes verslaan
wanneer `S < F < B`. Eigen zonnestroom kost dan minder aan gemiste opbrengst dan
later kopen, terwijl extra netinkoop nu juist duurder is dan later kopen.

Illustratie, geen actuele tarieven of voorspelling van de belasting in 2027:
één uur 3 kW overschot, 11 kWh resterende AC-behoefte vóór vertrek, nu B=€0,30 en
S=€0,22/kWh, voldoende latere laadruimte tegen F=€0,26/kWh.

| Keuze nu | EV nu | Netimport nu | Export nu | Later laden | Netto variabele kosten |
| --- | --- | --- | --- | --- | --- |
| Niet laden | 0 kWh | 0 kWh | 3 kWh | 11 kWh | € 2,20 |
| Alleen overschot | 3 kWh | 0 kWh | 0 kWh | 8 kWh | € 2,08 |
| Volledig 11 kW | 11 kWh | 8 kWh | 0 kWh | 0 kWh | € 2,40 |

De kosten zijn huidige import plus latere import minus huidige exportopbrengst;
de drie plannen leveren evenveel EV-energie. De regelaar moet dus drie fysieke
mogelijkheden kunnen afwegen. Green is geen economisch overbodige stand, maar
ook geen onvoorwaardelijk gunstige basis. Zonder opdracht geldt de hieronder
afzonderlijk beschreven prijsfilter voor Green; dat is een gebruikersheuristiek,
zonder claim van financieel optimum of gegarandeerde latere vervanging.

Alle bedragen worden eerst naar dezelfde EUR/kWh- en btw-basis genormaliseerd.
Negatieve marktprijzen en negatieve terugleverwaarden blijven negatief; onbekend
blijft onbekend. Vaste abonnementskosten zijn geen verschil tussen laadplannen.

Voor het Zonneplanprofiel, met alle componenten op dezelfde btw-basis:

```text
inkoop_all_in_i = markt_i + inkoopopslag_i + energiebelasting_i
terugleverbasis_i = markt_i + terugleveropslag_i
bonus_i = 10% * max(terugleverbasis_i, 0) * bonusgerechtigde_kWh_i
```

Bij een complete all-in feed worden inkoopopslag, belasting en btw niet nogmaals
opgeteld. Een teruggerekende marktprijs is alleen geldig met bekende inbegrepen
componenten en datums. De bonusgrondslag bevat geen energiebelasting. Zijn
jaarruimte en teldefinitie bevestigd, verdeel die ruimte in chronologische
volgorde; trek geplande export nooit van de werkelijke jaarteller af.

Voor elk tijdvak i, met duur d in uren:

```text
H_i = huisvermogen zonder EV
G_i = verwachte PV-opwek
P_i = gepland EV-vermogen
afname_i       = max(H_i + P_i - G_i, 0) * d
teruglevering_i = max(G_i - H_i - P_i, 0) * d

variabele rekening = som(afname_i * inkoop_zonder_energiebelasting_i)
                   - som(teruglevering_i * terugleverwaarde_zonder_bonus_i)
                   - bonus_volgens_contract
                   + belasting_volgens_regime
```

Vergelijk dezelfde resterende AC-laadbehoefte en hetzelfde vertrek. De bestaande
verliesmarge is daarin al verwerkt; niet nogmaals 15% over deze energiestromen
zetten. Verschillende laadrendementen alleen meenemen als die onderbouwd zijn.

Bereken een huishoudreferentie zonder deze EV-lading en het huishouden mét het
laadplan. Het verschil is de geschatte extra economische last van de EV-opdracht.
Een vergelijking tussen twee EV-plannen gebruikt dezelfde referentie, prognose,
contractstatus en energiebehoefte. Terugleverinkomsten van zonnepanelen die niets
met de keuze veranderen mogen niet als extra EV-besparing worden meegeteld.

Belangrijk onderscheid voor 2026: bij gelijke AC-laadenergie en verschuiven binnen
hetzelfde saldeertijdvak verandert `totale afname - totale teruglevering` niet.
Voor de rangschikking van die plannen valt de belasting op dit netto jaarvolume
daarom weg, onder de aanname van dezelfde belastingbehandeling en geen gewijzigde
overige energiestromen. Een volledige jaarrekeningsimulator is daarvoor niet
noodzakelijk. Alleen bij het bepalen van absolute kosten, andere volumes,
verschillende fiscale perioden of afwijkende voorwaarden is aanvullende
jaarcontext nodig. Deze algebraïsche vereenvoudiging is een gevolgtrekking uit
het model, niet een afzonderlijke toezegging van Zonneplan.

Vanaf 2027 telt de energiebelasting op extra netafname wel mee in deze keuze.
Op de datumgrens wordt het regime gesplitst; het dan geldende belastingtarief
wordt niet uit een oud 2026-bedrag gekopieerd.

Voorbeeld, geen actuele sessieberekening: 10 kWh benodigde AC-energie, effectieve
gemiste terugleverwaarde nu € 0,33/kWh en effectieve latere inkoop € 0,22/kWh,
beide op vergelijkbare fiscale basis. Direct Green heeft € 3,30 aan gemiste
opbrengst; later kopen kost € 2,20 terwijl de zonnestroom wordt verkocht.
Het verschil is € 1,10 ten gunste van later laden. Bij terugleverwaarde € 0,12
en dezelfde inkoop is direct gebruiken juist € 1,00 gunstiger.

Een hoge terugleverprijs alleen is nooit voldoende: ook beschikbare goedkopere
energie, de deadline, de resterende behoefte en uitvoerbare laadcapaciteit tellen.

## Aangescherpt voorstel: compacte B binnen een vertrekopdracht

Dit volgt Patricks beschrijving: een eigen opdracht met toekomstig vertrek mag Green tijdelijk
onderdrukken wanneer de huidige netto terugleverwaarde hoger is dan de kosten
van uitvoerbare vervangende energie vóór vertrek. De bestaande Start/shortcut
blijft de opdracht autoriseren; alleen achtergebleven doelhelpers zijn geen recht
om een vertrekopdracht of Manual-lading te starten. De afzonderlijk geautoriseerde
prijsfilter zonder opdracht staat hieronder.

Een directe opdracht zonder toekomstig vertrek wordt niet economisch uitgesteld.
De bestaande doel-, terugval-, Stop-, ontkoppel- en autolimietregels behouden hun
betekenis. Geen extra laadenergie toestaan om handelsopbrengst te creëren.

### Beslisregel en energiebehoud

1. Neem de resterende AC-behoefte uit de bestaande opdrachtboekhouding. Verwerk
   echte meterenergie, doel-/vertrekwijzigingen en beschikbare capaciteit zoals nu;
   de prognose verleent geen nieuw energiebudget.
2. Bepaal de verwachte of actuele daadwerkelijk laadbare Green-energie van het
   te beoordelen interval. Vergelijk het plan mét die energie met het plan zonder
   die energie, onder dezelfde aannames. Uitgestelde Green mag niet tegelijk van
   de netbehoefte afgetrokken blijven worden.
3. Reserveer eerst de laadruimte die de rest van de opdracht al nodig heeft.
   Zoek vervolgens voor elke uitgestelde kWh de goedkoopste nog bruikbare
   vervangende ruimte vóór vertrek, tegen de contractueel vergelijkbare prijs.
   Het goedkoopste kwartier kan niet dubbel worden toegewezen.
4. Pauzeer alleen het deel waarvoor netto voordeel én voldoende vervangende
   capaciteit bestaan. Is alleen 2 kWh goedkoop vervangbaar, dan rechtvaardigt dat
   niet het uitstellen van 10 kWh Green. Na verstrijken van een vervangingsblok of
   verandering van capaciteit/prognose wordt opnieuw berekend.
5. Laat minimumblokduur en bestaande continuïteit gelden. Kleine wijzigingen mogen
   geen herhaalde korte pauzes/starts veroorzaken. Gelijke kosten geven voorkeur
   aan doorladen en minder schakelen; gebruik geen ongemelde geldelijke drempel.
6. Als het doel anders in gevaar komt, vervalt economisch uitstel. Ontbrekende
   terugleverinformatie of onduidelijke bonusregels maken laden niet onmogelijk:
   gebruik Green/inkoopplanning en zo nodig een bandbreedte voor de kosten.

Voorbeeld van de capaciteitsvalkuil: alle kwartieren van € 0,20 zijn al nodig voor
het bestaande tekort. De volgende extra laadruimte kost € 0,40. Green van
€ 0,30/kWh terugleverwaarde dan uitstellen levert geen voordeel op; de relevante
vervangingsprijs is € 0,40, niet de reeds volledig gebruikte € 0,20.

Dit kan als begrensde uitbreiding van de bestaande kwartierallocatie, zonder
algemene jaaroptimizer, batterijhandel, nieuwe zon-/huisregelaar of externe solver.
De uitkomst is een uitvoerbaar plan op basis van bekende gegevens, geen garantie
van het wereldwijde optimum over nog onbekende toekomstige prijzen.

### Vertrek over twee dagen of later

Een lang vertrekvenster betekent niet dat alle kwartiertarieven al bekend zijn.
Zonneplan publiceert morgenprijzen in de loop van de voorafgaande middag. Bewaar
de werkelijke deadline en herplan met de beschikbare prijzen zodra de feed wijzigt.
Er mag geen pauze ontstaan uitsluitend omdat onbekende toekomstige stroom
misschien goedkoper wordt.

Voor de compacte variant moet de huidige harde `departure_outside_48h`-adviesgrens
worden losgekoppeld van de bewaarde opdracht: bereken een begrensd schuivend
prijsvenster, zonder de echte vertrekdatum te veranderen. Tijdelijke Green-pauze
kan ook bij een latere deadline, maar alleen als al bekende, uitvoerbare goedkopere
vervanging de betreffende energie dekt. Geen volledige toekomstdekking eisen als
de benodigde vervanging al aantoonbaar binnen het bekende venster past.
Alleen wachten op één blok vlak vóór vertrek is hierbij geen prijsstrategie:
bekende goedkope laadruimte kan al voorbij zijn voordat de laatste dagprijzen
verschijnen. Onbekende toekomstige tarieven hebben geen veronderstelde nulprijs
en zijn ook niet per definitie goedkoper dan bekende kwartieren.

Geautoriseerde werkwijze voor een gedeeltelijk bekende prijshorizon:

1. Bereken het tijdgewogen gemiddelde van de volledige kalenderdag vandaag en,
   zodra volledig beschikbaar, morgen. Neem ook verstreken kwartieren mee en
   weeg 23-/25-uursdagen op echte duur. Een gedeeltelijke morgen telt niet mee.
2. De configuratie `early_offset_cents` trekt 0–100 cent/kWh van dat gemiddelde af.
   Standaard 0. Bij gemiddeld € 0,30 en aftrek 5 cent is vroeg inkopen toegestaan
   tegen € 0,25/kWh **of lager**. Kies binnen die grens de goedkoopste uitvoerbare
   bekende kwartieren, voor maximaal de resterende opdrachtenergie.
3. Alleen bij de onvolledig bekende horizon geldt die voorkeursgrens. Zodra alle
   prijzen tot vertrek bekend zijn, of wachten de benodigde laadtijd plus reserve
   zou opeten, geldt de normale doelgerichte planning zonder gemiddeldeprijsgrens.
4. Ongepland resterend tekort krijgt een ongeprijsd aaneengesloten vertrekblok.
   Geen overlap met de al toegewezen tijd/energie. Geen bruikbare prijzen betekent
   de bestaande volledige deadlinefallback, nooit een laadverbod.
5. Nieuwe tarieven herplannen uitsluitend wat nog niet werkelijk geladen is.
   Dezelfde opdracht, generatie, autolimiet en energieboekhouding blijven leidend.
6. Toon gemiddelde, aftrek/grens, tariefdekking, voorlopig plan en fallback apart.
   Een reden om te wachten mag de opdracht niet wissen.

Dit is de afgesproken regel om vroege dure pieken te vermijden. Onbekende dagen
worden niet voorspeld. Geen aanvullende verborgen dagquota of prijsvoorspeller.
De standaard is automatisch dezelfde opdracht blijven beoordelen, ook na
HA-/Gatewayherstel en bij vertraagde voertuiggegevens.

### Green zonder actieve laadopdracht

Patrick heeft de uitbreiding ook zonder Start geautoriseerd: met ingeschakelde
hoofdschakelaar en expliciet ingeschakelde prijsfilter mag Green tijdelijk worden
gepauzeerd als de huidige netto terugleverwaarde hoger is dan de tijdgewogen
gemiddelde all-in inkoopprijs van vandaag **en morgen, zodra de volledige
morgentarieven beschikbaar zijn**. De hele kalenderdag telt mee; verschillen in
intervalduur en zomer-/wintertijd moeten correct worden gewogen. Nieuwe
tarieven werken de vergelijking automatisch bij.

Zonder geldige vergelijking blijft Green toegestaan. Dit maakt geen Manual-recht
of nieuwe laadopdracht en reserveert geen toekomstige energie. Het gemiddelde is
een expliciete voorkeur voor de situatie zonder doel, geen bewijs dat later
goedkopere vervanging beschikbaar zal zijn. Een gebruikers-Stop, echte
ontkoppeling, andere bestuurder en uitgeschakelde hoofdschakelaar houden voorrang;
een eigen economische pauze mag geen gebruikers-Stop ongedaan maken.

### Uitvoering, zichtbaarheid en begrenzing

Voeg een eigen bewaarde economische pauze toe, inclusief reden, toegerekende
uitgestelde energie en vervangende vensters. De planning blijft leidend; een
verouderde pauzereden is geen zelfstandig herstartrecht. Gebruik de bestaande
actuatorroute, contextmarkering, Stop-prioriteit en opdrachtbinding. Een eigen
OFF is geen gebruikers-Stop. De tijdelijke Green-onderdrukking en hervatting
moeten fysiek met Enphase worden bewezen, zonder fasen of ampères te schrijven.

Een onbekende toestand of Gateway-/HA-herstel mag de opdracht niet wissen of een
pauze oneindig vastzetten. Zonder opdracht vervalt de opdrachtgebonden pauzereden;
de afzonderlijke prijsfilter zonder opdracht mag alleen onder haar eigen expliciete
toestemming en voorwaarden werken. Na verlies van eigendom geen andere bestuurder
overschrijven. Toon per kwartier en in de status waarom Green is
toegestaan/gepauzeerd en waar de vervangende laadenergie staat ingepland.

Benodigde wijzigingen: contractadapter/kostenvergelijking, kwartierallocatie met
vervangingscapaciteit, schuivend prijsvenster, eigen pauze/herstel in de bestaande
uitvoerder en aanvullende attributen/tests. Geen volledig huishoudmodel nodig.

Aanvullende acceptatie voor compact B: prijsfilter uit → geen Green-OFF zonder
opdracht; prijsfilter aan → juiste vergelijking vandaag/morgen zonder Manual of
nieuw opdrachtrecht; gebruikers-Stop ook zonder actieve opdracht; genoeg en
onvoldoende vervangingsruimte; goedkope slots al bezet; pauze-energie niet dubbel
afgetrokken; deadline buiten 48 uur met gedeeltelijk bekende prijzen; nieuwe
prijzen die nog niet geladen kwartieren vervangen; bekend goedkoop kwartier niet
uitsluitend om onbekende toekomstige prijzen overslaan; ongedekt restant zonder
dubbele fallbackallocatie; capaciteit normal/safe; eigen OFF versus Stop; herstart in pauze;
prijsuitval; drie-minutenregel; energiebehoud; werkelijke Green-pauze/hervatting.

## Variant A: passend bij de komende hersteliteratie

1. Voeg een gedeeld, expliciet contractprofiel toe met facturatie-interval,
   prijsbasis, geldigheidsdatums en bonusstatus.
2. Maak een zuivere tariefadapter/kostenfunctie voor Zonneplan en een generiek
   profiel met reeds complete inkoop-/terugleverregels. Kostenberekening kan geen
   actuator aanroepen of laadopdracht maken.
3. Behoud Green als toegestane basis. Toon inkoopkosten, gemiste teruglevering,
   bonus, economische totaalraming en ontbrekende gegevens afzonderlijk.
   Een raming wordt niet als gerealiseerde besparing gepresenteerd.
4. Houd onbekende onderdelen zichtbaar. De bestaande goedkope-inkoopplanning en
   deadlinefallback blijven laden toestaan. Ontbrekende bonusjaarteller blokkeert
   de auto niet; bereken zo nodig een bandbreedte met/zonder bonus.
5. Een advies om Green over te slaan is optioneel aanvullend analysewerk, geen
   noodzakelijk onderdeel van A. Het advies moet met een uitvoerbaar alternatief
   worden onderbouwd en mag geen besparing beloven die de regelaar niet uitvoert.

Dit verbetert prijsbetekenis en diagnose, maar realiseert nog geen actieve
terugleveroptimalisatie. Bestaande gebrek aan kennis over toekomstige Green
blijft als onzekerheid zichtbaar; niet met een preciezer eurobedrag verhullen.

## Brede variant B: achtergrond, niet volledig nodig voor compact B

### Planner

- Modelleer per kwartier of kleiner grenssegment drie uitvoerbare toestanden:
  Green toegestaan, aanvullend Manual en economische pauze. Splits bij
  zonsopkomst, zonsondergang, tarief-/regimegrens en gedeeltelijk laadvenster.
- Green-opname is een verwachting met startdrempels, voertuig-/laadpaalcapaciteit
  en schakeltijd; `PV minus huislast` is niet automatisch volledig laadbare energie.
- Kies een plan met voldoende energie vóór vertrek en minimale vergelijkbare
  kosten. Een groothandelsprijssortering alleen volstaat niet bij bonusgrenzen,
  verschillende uitvoeringsmodi en minimumblokduur.
- Begrens het rekenvenster zonder de echte vertrekdatum af te kappen; zie de
  meerdaagse werkwijze hierboven. Een begrensde deterministische optimalisatie
  kan zonder zware externe solver; controleer kleine gevallen met een onafhankelijke
  volledige enumeratie. Bij discretisatie moet de foutgrens zichtbaar zijn en
  mag afronden geen extra energiebudget of overschreden autolimiet opleveren.
- Neem minimumduur, overgangsverlies en het lopende blok mee. Kies bij economisch
  gelijkwaardige plannen minder starts. Een eventuele geldelijke omschakeldrempel
  is een zichtbare productinstelling, geen verborgen verzonnen kostenpost.
- Laat het doel voorgaan op prijsvoordeel. Bij onvoldoende tijd/capaciteit vervalt
  een economische pauze en wordt de beste haalbare laadpoging gedaan.
- Negatieve prijzen geven geen toestemming om boven doel, energiebudget of
  autolimiet te laden. Een toekomstig goedkoper tarief na vertrek is niet bruikbaar.

### Besturing

Een optionele `economisch`-strategie geeft tijdelijk recht om Green op de
geconfigureerde laadpaal te pauzeren, uitsluitend binnen de eigen actieve opdracht.
De bestaande `Green als basis`-strategie blijft beschikbaar.

De eigen economische pauze krijgt een apart bewaard doel en reden; hij is geen
gebruikers-Stop, geen ontkoppeling en geen nieuwe generatie. Hij behoudt doel,
vertrek en meterboekhouding. Controleer vóór ingebruikname dat de Enphasebediening
Green daadwerkelijk kan onderdrukken en hervatten zonder ongewenste blijvende
handmatige override. De huidige switch-capability alleen bewijst dat gedrag niet.
Geen fase-/ampèrewrites, PV-begrenzing of nieuwe Tesla/VW-wake-lus toevoegen.

Na herstart wordt opnieuw gepland vanuit dezelfde opdracht; geen verouderde
pauze blind terugzetten. Stop, echte ontkoppeling, autowissel en een nieuwe externe
bediening gaan voor. Prijs-/prognoseverlies beëindigt economische pauze waar nodig
en volgt het bestaande laadfallbackbeleid, zonder de opdracht te wissen.

### Sensoren en instellingen

Behoud bestaande entiteitsidentiteiten. Breid de planningssensor uit met per
kwartier: modus/rede, verwachte AC-laadenergie, netafname, benutte zon,
teruglevering, inkoop-/terugleverwaarde en bonusstatus. Toon ook het gebruikte
vermogensmodel en `kosten_volledig`/aannames. De huidige resterende Manual-minuten
niet stilzwijgend herdefiniëren; voeg eventuele totale verwachte laadduur apart toe.

Vergelijkingssensoren/attributen onderscheiden verwachte netto economische
besparing van alleen cash-inkoopkosten. Werkelijke financiële nacontrole gebruikt
PV/P1- en EV-metingen plus werkelijke tarieven; een prognose bewijst geen besparing.

## Tests vóór functionele acceptatie

| Groep | Minimale gevallen |
| --- | --- |
| Tarieven | Uur-/kwartiercontract, inbegrepen toeslagen, btw, negatieve prijzen, geen dubbele belasting/bonus, conflicterende of ontbrekende regels |
| Zonnebonus | Daglichtgrens binnen een kwartier, grondslag nul/negatief, jaarruimte op/nabij grens, ontbrekende teller, kalenderjaarwisseling, uitgesloten energiebron |
| Belasting | Gelijkblijvend netto volume in 2026, netto import én netto export, verschillende volumes, jaargrens 2026/2027, onbekend toekomstig tarief |
| Economische keuze | Direct Green goedkoper; terugleveren/later kopen goedkoper; goedkope toekomst na vertrek; geen zon; zon onder Green-startdrempel; gelijke prijzen met minder schakelingen |
| Capaciteit | Normaal/safe/voertuiglimiet, overgang tijdens lopend blok, onvoldoende capaciteit vóór vertrek |
| Budget/continuïteit | Geen nieuw budget bij herplanning, 3-minutenregel, kwartierovergang, geen extra lading bij negatieve prijs, timer-/Stop-races |
| Uitval | Ontbrekende exportwaarde of prognose valt terug op inkoopplanning; ontbrekende inkoop op deadlineblok; terugkerende bronnen herplannen dezelfde opdracht |
| Herstel B | Bewaarde economische pauze en actief Manual door HA-/Gatewayherstel, onbekende terugmelding zonder oneindige pauze, gebruiker neemt over |
| Fysiek B | Echte zonnestroom: Green pauzeert, P1-export neemt toe bij passende metingen, later fysiek netladen, doel/deadline/budget behouden; afzonderlijk Tesla en ID.3 |

Financiële rekenvoorbeelden en een onafhankelijke kleine-plannenoracle moeten
naast de bestaande regressies staan. Externe code-review blijft vereist voor
implementatie. Software-, service/readback-, fysieke en financiële uitkomsten
worden apart gerapporteerd. Geen gesimuleerde SOC of opgewekte storingen als
fysiek bewijs gebruiken.

## Omvang en keuze

Indicatieve engineeringinschatting, geen leverbelofte of gemeten doorlooptijd:

- A: circa 1–2 gerichte bouw-/testdagen naast de al voorgestelde herstelpunten,
  mits contractvelden en tariefbasis bekend zijn. Geen volledige alternatieve
  planner of Enphasepauzebesturing inbegrepen.
- Compact B inclusief noodzakelijke contractnormalisatie: circa 2–4 gerichte
  bouw-/testdagen voor deze functie, met hergebruik van de bestaande planner en
  uitvoerder. Dit is geen optelsom van A en brede B en omvat niet alle andere
  herstelpunten. Fysieke proefduur hangt af van zon en beschikbaarheid van auto's.
- Brede B: de eerdere schatting van circa 3–6 extra bouw-/testdagen bovenop A
  betrof de ruimere alternatievenplanner, pauzebesturing,
  persistentie, diagnose, regressies en review; mogelijk meer bij ontbrekende
  Enphase-capability of contractambiguïteit. Fysieke acceptatie hangt bovendien
  af van aangesloten auto's, bruikbare zon en beschikbare meetgegevens.

De exacte baten voor deze installatie zijn nog niet bepaald. Ze worden begrensd
door de werkelijk verschuifbare zonnestroom vóór vertrek en het prijsverschil.
Alleen het extra voordeel boven de bestaande Green-plus-goedkope-inkoopregeling
telt. Rekenvoorbeelden, geen voorspelling voor deze installatie:

| Werkelijk verschoven energie | Netto prijsverschil | Extra voordeel |
| --- | --- | --- |
| 2 kWh per geschikte laadbeurt | € 0,03/kWh | € 0,06 |
| 8 kWh per geschikte laadbeurt | € 0,08/kWh | € 0,64 |
| 15 kWh per geschikte laadbeurt | € 0,15/kWh | € 2,25 |
| 300 kWh per jaar | € 0,03/kWh | € 9/jaar |
| 1.000 kWh per jaar | € 0,08/kWh | € 80/jaar |
| 2.000 kWh per jaar | € 0,10/kWh | € 200/jaar |

Dit zijn netto vergelijkbare prijsverschillen na contractregels, vóór afwijkingen
door prognose en uitvoering. Zonder voldoende lange aanwezigheid, overschot op
een duur moment én goedkopere vervanging vóór vertrek is het voordeel nul.
Een persoonlijk jaarbedrag vereist representatieve historie van PV, huislast,
tarieven, aansluitingen en doelen. Het voordeel van goedkoop netladen dat de
huidige regeling al realiseert mag niet nogmaals aan compact B worden toegeschreven.

Patrick heeft toestemming gegeven om Green tijdelijk te pauzeren voor financieel
voordeel. Voor een nauwkeurig contractprofiel moeten
het eigen facturatie-interval, prijsopbouw en bonusstatus worden bevestigd of uit
een betrouwbare bron worden gelezen. Ontbrekende financiële details beperken
de optimalisatie/raming en worden nooit een blokkade voor noodzakelijk laden.

## Inrichting en bewijs voor 5.2.0b1

- `economic_enabled` en `idle_green_price_filter` staan standaard uit; bestaande
  entiteits- en domeinidentiteiten blijven behouden. Alleen `control.py` bestuurt
  de bestaande Green/Manual-selector en toegestane schakelaars.
- Zonder opdracht heeft de afzonderlijke Green-filter geen Manual-recht. Hij
  vergelijkt de huidige netto terugleverwaarde met het hierboven gedefinieerde
  gemiddelde, zonder de voor vroege inkoop bedoelde centaftrek.
- Eigen economische OFF wordt bewaard als aansluitinggebonden pauze. Een echte
  gebruikers-Stop of andere bediening krijgt voorrang en blokkeert eigen hervatten.
  Een waargenomen korte kabel-off/on beëindigt de oude opdracht; een onderbouwde
  apparaattijdstempelvernieuwing na storing verandert geen energiebudget/generatie.
- Een ongeprijsde of ontbrekende exportkolom verhindert geen geldige inkoopplanning.
  Het Zonneplanprofiel trekt expliciet bevestigde, gedateerde inbegrepen belasting
  en inkoopopslag af van de complete inkoopfeed en voegt de exportopslag toe.
  Een ontbrekende bonusjaarteller geeft een zichtbare berekening met de basiswaarde;
  geen verzonnen bonus. De 10%-bonus vereist positieve basis, daglicht, jaarbinding
  en aantoonbare ruimte binnen de 7.500 kWh, met een conservatieve fysieke grens.
- Enphase is de automatische bron voor de planningscapaciteit: het ingestelde
  normale vermogen, of het safe-vermogen bij safe mode/ontbrekende Gatewaystatus.
  Vanaf 5.2.0b2 is de Tesla-pilotstroom geen capaciteitsbron meer. De eigenaar heeft
  deze vereenvoudiging expliciet gekozen nadat 5/16 A-terugmeldingen tijdens de
  nachtproef een verkeerde urgentie en herhaald schakelen veroorzaakten.
  HomeWizard blijft de fysieke meting en de energiemeter blijft de boekhouding;
  laag Green-vermogen verlaagt geen voorspelde Manual-capaciteit. Geen fase- of
  ampèreschrijven. Een bewust vast lager planningsvermogen per auto blijft mogelijk.
  De oude stroombronvelden zijn niet meer instelbaar en achtergebleven waarden
  worden genegeerd. `capacity_source` en `vehicle_current_used` tonen de bronkeuze.
  Tijdens eigen Manual geeft minimaal twee minuten en drie verschillende verse
  HomeWizard-metingen onder 70% van de rekencapaciteit een zichtbare waarschuwing.
  `delivery` toont daarbij wanneer het resterende budget klaar zou zijn als vanaf
  nu onafgebroken met die waargenomen snelheid wordt geladen. Dat is een
  voorwaardelijke vergelijking met vertrek, geen nieuwe planning. Deze diagnose
  verandert geen vermogen, venster, opdracht of actuator en blokkeert niets.
- Een bewaard budget blijft bruikbaar bij appuitval. Een laatst bekende referentie
  kan een nieuwe expliciete Start ondersteunen; die berekening blijft voorlopig.
  Bij meteruitval blijft de bestaande ruimte bestaan; ontbrekende meterwaarden
  crediteren geen fictieve energie. Een verse voertuigdoelmelding kan wel stoppen.
- Een expliciet gekozen tweede vermogensbron mag de ontbrekende onafhankelijke
  bron vervangen. `fallback_used`, `entity_id` en `independent` maken dat zichtbaar.
- Energie- en tijdplafonds zijn configureerbaar; 30 kWh is geen onzichtbare norm
  voor een volledige 20→80%-opdracht. Een proef krijgt daarnaast een eigen begrensd
  totaal, accugrens en eindtijd. Oude fysieke resultaten worden niet gepromoveerd.
- Review, release, geïnstalleerde bestandshashes, HA-herstart, configuratiereadback
  en de uitkomsten van de nachtproef moeten afzonderlijk worden geregistreerd.
  Softwaretests bewijzen geen werkelijk doelbereik, zonexport of natuurlijke terugval.

### Herstel, meetuitval en betekenis van de raming

Gatewayuitval verlaagt alleen het rekenvermogen. De verbinding met de laadpaal
zelf wordt apart gelezen. Een werkelijk waargenomen ontkoppeling bij een online
laadpaal beëindigt de opdracht. Bij een onderbroken laadpaalverbinding blijft die
bewaard; dezelfde aansluitidentiteit hervat automatisch. Als de identiteit juist
tijdens dat gat veranderde, wacht hij op voertuigbewijs van na het gat en hervat
dan dezelfde opdracht. Een doorlopend waargenomen aansluiting met alleen een
vernieuwde apparaattijdstempel reset geen opdracht of energieboekhouding.

Er is geen harde maximale leeftijd voor de laatst bekende voertuigreferentie.
Deze blijft voorlopig en krijgt een zichtbare meettijd/leeftijd. De eerste echte
voertuigmeting van na Start mag een voorlopige beginberekening eenmalig bijstellen,
ook bij een lagere SOC. Een onbekende accu-inhoud levert geen verzonnen doelbewijs
en een alleen bewaarde referentie geeft geen recht om de autolimiet te verhogen.

Een ontbrekende laadmeter schrijft geen geleverde energie bij. Een afzonderlijke
bovengrens telt maximaal mogelijk Manual-verbruik sinds de laatste meting mee
voor het configureerbare opdrachtplafond. Bij herstel van de werkelijke meter
wordt die onzekerheidsgrens opnieuw beoordeeld en kan dezelfde opdracht hervatten.
De standaard maximale Manual-duur blijft acht uur en kan expliciet worden ingesteld.

Een eigen economische OFF vereist vooraf bevestigd fysiek Green-laden, een eigen
OFF-verzoek en terugmelding daarvan. Een al elders uitgezette lader wordt niet
geadopteerd. De verplichting om de eigen pauze terug naar Green af te ronden blijft
bewaard bij fouten of ontkoppeling; een expliciete Stop of buitenlandse bediening
neemt die verplichting met een auditmelding over. De oude laadopdracht wordt bij
een nieuwe aansluiting nooit hervat door deze Green-opruiming.

`cost_scope=allocated_required_ac_energy_only` betekent dat de kostenraming alleen
de aan het nog benodigde AC-budget toegewezen energie omvat, inclusief de
opofferingswaarde van toegewezen zonnestroom. Het is geen factuurvoorspelling of
gemeten besparing. In `quarters` blijven `minutes` en `energy_kwh` alleen Manual
betekenen; Green-toestemming, toegewezen Green en economische pauzes staan apart.
Als een actieve lange opdracht alleen vooruitkijkende tarieven heeft en daardoor
geen complete kalenderdag beschikbaar is, gebruikt hij zichtbaar het gewogen
gemiddelde van die bekende vooruitkijkende intervallen. Zonder opdracht wordt
bij ontbrekende complete dagprijzen geen economische Green-blokkade gemaakt.
