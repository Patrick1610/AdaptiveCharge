import json
from inspect import cleandoc
from pathlib import Path

BASE='/ev-v2-proef'
BANNER={
 'type':'markdown',
 'content':'''# AdaptiveCharge · preview
{% if is_state('binary_sensor.enphase_preview_control_active', 'off') and state_attr('sensor.enphase_preview_preview', 'read_only') == true %}
**Besturing uit — alleen een voorvertoning.**
{% else %}
**⚠ Besturingsstatus niet bevestigd.**
{% endif %}
De huidige laadregeling bedien je via [EV laden](/dashboard-stormbreaker).
Voorbeeldwaarden op dit dashboard veranderen alleen de preview.'''
}
WARNINGS={
 'vehicle_measurement_stale_or_time_unknown':'Voertuigmeting is verouderd of de meettijd ontbreekt.',
 'invalid_price_rows':'Er zijn geen bruikbare stroomtarieven.',
 'prices_missing':'De prijsbron ontbreekt.',
 'export_price_unknown_cost_estimate_incomplete':'Terugleverprijs ontbreekt; kosten zijn nog niet volledig te bepalen.',
 'forecast_stale_ignored':'Zonneprognose is verouderd en telt niet mee.',
 'forecast_invalid_ignored':'Zonneprognose is niet bruikbaar.',
 'native_vehicle_identity_ambiguous':'De actieve auto is niet eenduidig vast te stellen.',
 'price_gap':'De tariefreeks bevat een gat.',
}
PHASES={'idle':'Alleen advies','waiting_data':'Wacht op brongegevens','watching':'Doelbewaking in de preview','charging':'Bijladen in de preview','scheduled':'Ingepland in de preview','verifying':'Wacht op doelbevestiging','cancelled':'Preview beëindigd','disconnected':'Ontkoppeling gezien','fault':'Brongegevens vragen aandacht'}

def md(text):
 return {'type':'markdown','content':cleandoc(text)}

def context(sensor):
 return "{% set e = '"+sensor+"' %}\n{% set d = state_attr(e, 'demand') or {} %}\n{% set o = state_attr(e, 'observations') or {} %}\n{% set p = state_attr(e, 'plan') or {} %}\n"

def status(sensor,name):
 return md(context(sensor)+'''## '''+name+'''
{% set phases = '''+json.dumps(PHASES,ensure_ascii=False)+''' %}
**{{ phases.get(states(e), 'Wacht op gegevens') }}**
{% if state_attr(e, 'plan_provisional') != false %}
**Advies voorlopig.** Gebruik onderstaande schattingen nog niet als bevestigd laadplan.
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
| Bereikdoel preview | {{ (target | round(0) | int | string ~ ' km') if target is number else 'Onbekend' }} |
| Doelindicatie preview | {{ (d.get('effective_soc') | round(1) | string ~ ' %') if d.get('effective_soc') is number else 'Onbekend' }} |
| Extra laadenergie · schatting | {{ (d.get('needed_kwh') | round(2) | string ~ ' kWh') if d.get('needed_kwh') is number else 'Onbekend' }} |
| Bereik nodig op | {{ departure | timestamp_custom('%d-%m-%Y %H:%M', true) if departure is number else 'Onbekend' }} |
{% if d.get('limited') %}
Het effectieve doel is begrensd door de autolimiet. De preview wijzigt die limiet niet.
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
Nog geen laadvensters in de preview.
{% endif %}

**Geschatte kosten:** {{ ('€ ' ~ (p.get('cost') | round(2))) if p.get('cost') is number else 'Niet beschikbaar' }}

{% set c = state_attr(e, 'comparison') or {} %}
**Vergelijking:** huidige regeling {{ c.get('native_mode') or 'onbekend' }} · preview {{ c.get('preview_mode') or 'onbekend' }}.
Een voorgesteld venster of dezelfde modus is nog geen bewijs dat de auto zo heeft geladen.''')

def build() -> dict:
    """Build the three existing view paths using only preview entities."""
    cars=[
     ('Stormbreaker','tesla','sensor.stormbreaker_preview','number.stormbreaker_preview_range_target','datetime.stormbreaker_preview_departure','number.stormbreaker_soc_return_threshold'),
     ('Éowyn · ID.3','id3','sensor.eowyn_id_3_preview','number.eowyn_id_3_preview_range_target','datetime.eowyn_id_3_preview_departure','number.eowyn_id_3_soc_return_threshold'),
    ]
    overview=[BANNER,md('''### Laadpaal · waarneming
    {% set p = state_attr('sensor.enphase_preview_preview', 'physical') or {} %}
    {% set meter = state_attr('sensor.enphase_preview_preview', 'meter_kwh') %}
    {% if p.get('fresh') and p.get('kw') is number %}
    **Vermogen:** {{ p.kw | round(3) }} kW\x20\x20
    **Laden waargenomen:** {{ 'Ja' if p.get('positive') else 'Nee' }}
    {% else %}
    Er is geen verse vermogensmeting beschikbaar.
    {% endif %}

    **Laadmeter:** {{ (meter | round(2) | string ~ ' kWh') if meter is number else 'Niet beschikbaar' }}

    De preview observeert deze waarden en stuurt de laadpaal niet aan.''')]
    for name,path,sensor,*_ in cars:
     card=status(sensor,name)
     card['content']+='\n[Bekijk profiel]('+BASE+'/'+path+')'
     overview.append(card)
    views=[{'title':'Overzicht','path':'proef','icon':'mdi:flask-outline','cards':overview}]
    for name,path,sensor,target,departure,droop in cars:
     views.append({'title':name,'path':path,'icon':'mdi:car-electric','cards':[
      BANNER,status(sensor,name),facts(sensor),
      {'type':'entities','title':'Voorbeeldinstellingen · alleen preview','show_header_toggle':False,'entities':[
       {'entity':target,'name':'Bereikdoel · preview','icon':'mdi:map-marker-distance'},
       {'entity':departure,'name':'Bereik nodig op · preview'},
       {'entity':droop,'name':'Terugval voor bijladen · procentpunt'},
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
