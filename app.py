from flask import Flask, render_template, jsonify
import requests
from datetime import datetime
import concurrent.futures

app = Flask(__name__)

COUNTRIES = {
    'JP': '日本',
    'US': 'アメリカ',
    'CN': '中国',
    'DE': 'ドイツ',
    'GB': 'イギリス',
}

INDICATORS = {
    'gdp_growth':    ('NY.GDP.MKTP.KD.ZG', 'GDP成長率'),
    'inflation':     ('FP.CPI.TOTL.ZG',    'インフレ率'),
    'unemployment':  ('SL.UEM.TOTL.ZS',    '失業率'),
    'current_acct':  ('BN.CAB.XOKA.GD.ZS', '経常収支(GDP比%)'),
}

def wb_fetch(country, indicator_code, years=12):
    url = (
        f'https://api.worldbank.org/v2/country/{country}'
        f'/indicator/{indicator_code}?format=json&mrv={years}&per_page=100'
    )
    try:
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        payload = r.json()
        if len(payload) > 1 and payload[1]:
            rows = [(d['date'], round(d['value'], 2))
                    for d in payload[1] if d['value'] is not None]
            return sorted(rows, key=lambda x: x[0])
    except Exception:
        pass
    return []

def fetch_exchange_rates():
    try:
        r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=10)
        r.raise_for_status()
        data = r.json()
        targets = ['JPY', 'EUR', 'CNY', 'GBP', 'AUD', 'KRW', 'CHF', 'CAD']
        rates = {c: round(data['rates'][c], 4) for c in targets if c in data['rates']}
        return {'base': 'USD', 'rates': rates,
                'updated': data.get('time_last_update_utc', '')}
    except Exception:
        return {
            'base': 'USD',
            'rates': {'JPY': 149.5, 'EUR': 0.92, 'CNY': 7.24,
                      'GBP': 0.79, 'AUD': 1.53, 'KRW': 1325.0,
                      'CHF': 0.89, 'CAD': 1.36},
            'updated': 'N/A (フォールバックデータ)'
        }

def fetch_country_data(code):
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            key: ex.submit(wb_fetch, code, ind_code)
            for key, (ind_code, _) in INDICATORS.items()
        }
        for key, fut in futures.items():
            result[key] = fut.result()
    return result

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/indicators')
def api_indicators():
    all_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {code: ex.submit(fetch_country_data, code) for code in COUNTRIES}
        for code, fut in futures.items():
            all_data[code] = {'name': COUNTRIES[code], **fut.result()}

    labels = {key: label for key, (_, label) in INDICATORS.items()}
    return jsonify({'countries': all_data, 'labels': labels})

@app.route('/api/exchange')
def api_exchange():
    return jsonify(fetch_exchange_rates())

@app.route('/api/summary')
def api_summary():
    """Latest single values for dashboard cards."""
    summary = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {}
        for code in COUNTRIES:
            for key, (ind_code, label) in INDICATORS.items():
                futures[(code, key)] = ex.submit(wb_fetch, code, ind_code, years=3)

        for (code, key), fut in futures.items():
            rows = fut.result()
            if rows:
                year, value = rows[-1]
                summary.setdefault(code, {'name': COUNTRIES[code]})
                summary[code][key] = {'value': value, 'year': year,
                                      'label': INDICATORS[key][1]}
    return jsonify(summary)

if __name__ == '__main__':
    print('=== 経済指標ダッシュボード起動中 ===')
    print('http://localhost:5050 でアクセスしてください')
    app.run(debug=False, port=5050, threaded=True)
