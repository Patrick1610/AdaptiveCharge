import json
from inspect import cleandoc
from pathlib import Path

BASE='/ev-v2-proef'
BANNER={
 'type':'markdown',
 'content':'''# AdaptiveCharge
{% if is_state('binary_sensor.enphase_preview_control_active', 'off') and state_attr('sensor.enphase_preview_preview', 'read_only') == true %}
**Hoofdschakelaar uit — alleen meerekenen.**
{% elif is_state('binary_sensor.enphase_preview_control_active', 'on') %}
**Hoofdschakelaar aan.** Alleen een eigen Start geeft een laadopdracht.
{% else %}
**Besturing wacht op bevestiging of afronding.**
{% endif %}
De oude bediening staat onder [EV laden](/dashboard-stormbreaker).
Alleen één regeling mag de laadpaal besturen. Stop beëindigt de opdracht;
een fasepauze of nulvermogen doet dat niet.'''
}
WARNINGS={
 'vehicle_measurement_stale_or_time_unknown':'Voertuigmeting is verouderd of de meettijd ontbreekt.',
 'invalid_price_rows':'Er zijn geen bruikbare stroomtarieven. Manual wacht; Green mag doorlopen.',
 'prices_missing':'De prijsbron ontbreekt. Manual wacht; Green mag doorlopen.',
 'prices_unavailable':'Enever is niet beschikbaar; alleen zelfstandig Green-laden is toegestaan.',
 'export_price_unknown_cost_estimate_incomplete':'Terugleverprijs ontbreekt; kosten zijn nog niet volledig te bepalen.',
 'forecast_stale_ignored':'Zonneprognose is verouderd en telt niet mee.',
 'forecast_invalid_ignored':'Zonneprognose is niet bruikbaar.',
 'native_vehicle_identity_ambiguous':'De actieve auto is niet eenduidig vast te stellen.',
 'price_gap':'De tariefreeks bevat een gat.',
}
PHASES={'idle':'Geen actieve opdracht','waiting_data':'Wacht op bruikbare gegevens','watching':'Doelbewaking','charging':'Fysiek bijladen waargenomen','paused':'Opdracht bewaard · fysiek laden nog niet bevestigd','scheduled':'Ingepland','would_boost':'Zou aanvullend laden','verifying':'Energiegrens bereikt · wacht op voertuigbevestiging','within_tolerance':'Klein restant binnen tolerantie','blocked':'Besturing vraagt aandacht','cancelled':'Opdracht beëindigd','disconnected':'Ontkoppeling gezien','fault':'Brongegevens vragen aandacht'}

def md(text):
 return {'type':'markdown','content':cleandoc(text)}

def context(sensor):
 return "{% set e = '"+sensor+"' %}\n{% set d = state_attr(e, 'demand') or {} %}\n{% set o = state_attr(e, 'observations') or {} %}\n{% set p = state_attr(e, 'plan') or {} %}\n"

def status(sensor,name):
 return md(context(sensor)+'''## '''+name+'''
{% set phases = '''+json.dumps(PHASES,ensure_ascii=False)+''' %}
**{{ phases.get(states(e), 'Wacht op gegevens') }}**
{% if state_attr(e, 'plan_provisional') != false %}
**Voertuigmeting onbevestigd.** De schatting is voorlopig. Een bewaard energiebudget kan worden afgemaakt; dit is geen bevestiging van doelbereik.
{% endif %}
{% set messages = '''+json.dumps(WARNINGS,ensure_ascii=False)+''' %}
{% for warning in state_attr(e, 'warnings') or [] %}
- {{ messages.get(warning, 'Bron of planning vraagt aandacht. Bekijk de bronstatus voordat je dit advies gebruikt.') }}
{% endfor %}
{% if not state_attr(e, 'warnings') %}
Geen bronwaarschuwingen gemeld.
{% endif %}
''')

def facts(sensor):
 return md(context(sensor)+'''### Gemelde waarden en doel
{% set soc = o.get('soc', {}) %}
{% set battery = o.get('battery_kwh', {}) %}
{% set limit = o.get('limit_soc', {}) %}
{% set target = state_attr(e, 'target_km') %}
{% set departure = state_attr(e, 'departure') %}
| | |
| :-- | --: |
| Gemelde acculading | {{ (soc.get('value') | round(0) | int | string ~ ' %') if soc.get('value') is number else 'Onbekend' }} |
| Meetmoment acculading | {{ soc.get('measured_at') | timestamp_custom('%d-%m %H:%M', true) if soc.get('measured_at') is number else 'Onbekend' }} |
| Gemelde accu-energie | {{ (battery.get('value') | round(2) | string ~ ' kWh') if battery.get('value') is number else 'Onbekend' }} |
| Meetmoment accu-energie | {{ battery.get('measured_at') | timestamp_custom('%d-%m %H:%M', true) if battery.get('measured_at') is number else 'Onbekend' }} |
| Ingestelde autolimiet | {{ (limit.get('value') | round(0) | int | string ~ ' %') if limit.get('value') is number else 'Onbekend' }} |
| Bereikdoel | {{ (target | round(0) | int | string ~ ' km') if target is number else 'Onbekend' }} |
| Doelindicatie | {{ (d.get('effective_soc') | round(1) | string ~ ' %') if d.get('effective_soc') is number else 'Onbekend' }} |
| Extra laadenergie · schatting | {{ (d.get('needed_kwh') | round(2) | string ~ ' kWh') if d.get('needed_kwh') is number else 'Onbekend' }} |
| Bereik nodig op | {{ departure | timestamp_custom('%d-%m-%Y %H:%M', true) if departure is number else 'Onbekend' }} |
{% if d.get('limited') %}
Het effectieve doel is begrensd door de bevestigde autolimiet. Een optionele Tesla-verhoging geldt pas na terugmelding.
{% endif %}''')

def planning(sensor):
 return md(context(sensor)+'''### Voorgestelde laadvensters
{% if state_attr(e, 'plan_provisional') != false %}
**Voorlopig advies; tijden en kosten zijn niet bevestigd.**
{% endif %}
{% if p.get('windows') %}
| Van | Tot | Advies | Energie |
| :-- | :-- | :-- | --: |
{% for w in p.get('windows', []) %}
{% if w.get('start') is number and w.get('end') is number and w.get('kwh') is number %}
| {{ w.start | timestamp_custom('%d-%m %H:%M:%S', true) }} | {{ w.end | timestamp_custom('%d-%m %H:%M:%S', true) }} | Bijladen | {{ w.kwh | round(2) }} kWh |
{% endif %}
{% endfor %}
{% else %}
Geen geplande tijdvensters. Een direct/urgent verzoek kan nu bijladen; zonder Start wordt alleen meegerekend.
{% endif %}

**Geschatte kosten:** {{ ('€ ' ~ (p.get('cost') | round(2))) if p.get('cost') is number else 'Niet beschikbaar' }}

{% set c = state_attr(e, 'comparison') or {} %}
**Vergelijking:** oude native berekening {{ c.get('native_mode') or 'onbekend' }} · AdaptiveCharge {{ c.get('preview_mode') or 'onbekend' }}.
Een voorgesteld venster of dezelfde modus is nog geen bewijs dat de auto zo heeft geladen.''')

def build() -> dict:
    """Build the three existing view paths using only preview entities."""
    cars=[
     ('Stormbreaker','tesla','sensor.stormbreaker_preview','number.stormbreaker_preview_range_target','datetime.stormbreaker_preview_departure','number.stormbreaker_soc_return_threshold'),
     ('Éowyn · ID.3','id3','sensor.eowyn_id_3_preview','number.eowyn_id_3_preview_range_target','datetime.eowyn_id_3_preview_departure','number.eowyn_id_3_soc_return_threshold'),
    ]
    overview=[BANNER,{'type':'entities','title':'Hoofdschakelaar · laadpaal','show_header_toggle':False,'entities':[
     {'entity':'switch.enphase_preview_control_master','name':'AdaptiveCharge-besturing'},
    ]},md('''### Besturing
    {% set e = 'sensor.enphase_preview_preview' %}
    {% set status = state_attr(e, 'control_status') %}
    {% if status == 'physical_start_unconfirmed' %}
    **Laadstart nog niet bevestigd.** De opdracht blijft bewaard. Controleer de auto en laadpaal; er wordt geen extra Start verstuurd.
    {% elif state_attr(e, 'control_fault') or status == 'price_feed_stop_unconfirmed' %}
    **Besturing vraagt aandacht.** Er komt geen automatische nieuwe laadstart.
    Controleer het fysieke laadvermogen; een aangevraagde stop kan nog onbevestigd zijn.
    {% elif status == 'waiting_prices' %}
    **Wacht op bruikbare stroomtarieven.** De opdracht blijft bewaard.
    {% elif status == 'enabled_no_request' %}
    **Klaar voor een opdracht.** Kies auto, doel en vertrek en druk op Start.
    {% elif state_attr(e, 'active_vehicle') %}
    **Opdracht bewaard.** De voertuigkaart toont planning en voortgang.
    {% else %}
    **Geen actieve laadopdracht.**
    {% endif %}
    {% set a = state_attr(e, 'limit_audit') or {} %}
    {% if a.get('retired_reason') in ['foreign_controller_owner', 'writer_identity_changed', 'restore_readback_timeout'] %}
    **Laatste limietherstel vraagt controle.** AdaptiveCharge schreef {{ a.get('written') }}%; de oorspronkelijke Tesla-instelling was {{ a.get('original') }}%. Controleer de voertuiglimiet in de Tesla-app.
    {% endif %}'''),md('''### Laadpaal · waarneming
    {% set p = state_attr('sensor.enphase_preview_preview', 'physical') or {} %}
    {% set meter = state_attr('sensor.enphase_preview_preview', 'meter_kwh') %}
    {% if p.get('fresh') and p.get('kw') is number %}
    **Vermogen:** {{ p.kw | round(3) }} kW\x20\x20
    **Laden waargenomen:** {{ 'Ja' if p.get('positive') else 'Nee' }}
    {% else %}
    Er is geen verse vermogensmeting beschikbaar.
    {% endif %}

    **Laadmeter:** {{ (meter | round(2) | string ~ ' kWh') if meter is number else 'Niet beschikbaar' }}

    Dit is gemeten laadvermogen, los van de berekende planning.''')]
    for name,path,sensor,*_ in cars:
     card=status(sensor,name)
     card['content']+='\n[Bekijk profiel]('+BASE+'/'+path+')'
     overview.append(card)
    views=[{'title':'Overzicht','path':'proef','icon':'mdi:flask-outline','cards':overview}]
    for name,path,sensor,target,departure,droop in cars:
     views.append({'title':name,'path':path,'icon':'mdi:car-electric','cards':[
      BANNER,status(sensor,name),facts(sensor),
      {'type':'entities','title':'Doel en vertrek','show_header_toggle':False,'entities':[
       {'entity':target,'name':'Bereikdoel','icon':'mdi:map-marker-distance'},
       {'entity':departure,'name':'Bereik nodig op'},
       {'entity':droop,'name':'Terugval voor bijladen · procentpunt'},
       {'entity':'button.'+('stormbreaker' if path=='tesla' else 'eowyn_id_3')+'_start','name':'Start opdracht'},
       {'entity':'button.'+('stormbreaker' if path=='tesla' else 'eowyn_id_3')+'_stop','name':'Stop opdracht'},
      ]},planning(sensor)
     ]})
    return {'title':'EV · AdaptiveCharge','views':views}


def main() -> None:
    """Write a candidate file; never connect to HA or install a dashboard."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'dashboards/preview.json')
    args = parser.parse_args()
    config = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'views': len(config['views']), 'cards': sum(len(v['cards']) for v in config['views']), 'bytes': args.output.stat().st_size}))


if __name__ == '__main__':
    main()
