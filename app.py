from flask import Flask, render_template, jsonify
import requests
import sqlite3
import json
import time
import concurrent.futures
from datetime import datetime, timedelta
from pathlib import Path

app = Flask(__name__)

DB_PATH = Path(__file__).parent / 'cache.db'
CACHE_TTL = 60 * 60 * 24  # 24時間

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
    'trade_balance': ('NE.RSB.GNFS.ZS',    '貿易収支(GDP比%)'),
}

# ---------- DB 初期化 ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS indicator_cache (
                country     TEXT NOT NULL,
                key         TEXT NOT NULL,
                data        TEXT NOT NULL,
                updated_at  REAL NOT NULL,
                PRIMARY KEY (country, key)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS exchange_cache (
                id          INTEGER PRIMARY KEY,
                data        TEXT NOT NULL,
                updated_at  REAL NOT NULL
            )
        ''')

def is_fresh(updated_at):
    return (time.time() - updated_at) < CACHE_TTL

# ---------- World Bank API ----------

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

def fetch_and_cache_indicator(country, key, ind_code):
    data = wb_fetch(country, ind_code)
    with get_db() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO indicator_cache VALUES (?,?,?,?)',
            (country, key, json.dumps(data), time.time())
        )
    return data

def get_indicator(country, key):
    ind_code = INDICATORS[key][0]
    with get_db() as conn:
        row = conn.execute(
            'SELECT data, updated_at FROM indicator_cache WHERE country=? AND key=?',
            (country, key)
        ).fetchone()
    if row and is_fresh(row['updated_at']):
        return json.loads(row['data'])
    return fetch_and_cache_indicator(country, key, ind_code)

# ---------- 為替レート ----------

def fetch_and_cache_exchange():
    try:
        r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=10)
        r.raise_for_status()
        data = r.json()
        targets = ['JPY', 'EUR', 'CNY', 'GBP', 'AUD', 'KRW', 'CHF', 'CAD']
        rates = {c: round(data['rates'][c], 4) for c in targets if c in data['rates']}
        result = {'base': 'USD', 'rates': rates,
                  'updated': data.get('time_last_update_utc', '')}
    except Exception:
        result = {
            'base': 'USD',
            'rates': {'JPY': 149.5, 'EUR': 0.92, 'CNY': 7.24,
                      'GBP': 0.79, 'AUD': 1.53, 'KRW': 1325.0,
                      'CHF': 0.89, 'CAD': 1.36},
            'updated': 'N/A (フォールバックデータ)'
        }
    with get_db() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO exchange_cache (id, data, updated_at) VALUES (1,?,?)',
            (json.dumps(result), time.time())
        )
    return result

def get_exchange():
    with get_db() as conn:
        row = conn.execute('SELECT data, updated_at FROM exchange_cache WHERE id=1').fetchone()
    if row and is_fresh(row['updated_at']):
        return json.loads(row['data'])
    return fetch_and_cache_exchange()

# ---------- 起動時プリフェッチ ----------

def prefetch_all():
    print('DBキャッシュを確認中...')
    tasks = []
    with get_db() as conn:
        for country in COUNTRIES:
            for key, (ind_code, _) in INDICATORS.items():
                row = conn.execute(
                    'SELECT updated_at FROM indicator_cache WHERE country=? AND key=?',
                    (country, key)
                ).fetchone()
                if not row or not is_fresh(row['updated_at']):
                    tasks.append((country, key, ind_code))

        row = conn.execute('SELECT updated_at FROM exchange_cache WHERE id=1').fetchone()
        need_exchange = not row or not is_fresh(row['updated_at'])

    if tasks or need_exchange:
        print(f'APIから取得中... ({len(tasks)}件の指標)')
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for country, key, ind_code in tasks:
                ex.submit(fetch_and_cache_indicator, country, key, ind_code)
            if need_exchange:
                ex.submit(fetch_and_cache_exchange)
        print('キャッシュ完了！')
    else:
        print('キャッシュ有効。DBから即時提供します。')

# ---------- Flask ルート ----------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/indicators')
def api_indicators():
    all_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {}
        for code in COUNTRIES:
            for key in INDICATORS:
                futures[(code, key)] = ex.submit(get_indicator, code, key)
        for (code, key), fut in futures.items():
            all_data.setdefault(code, {'name': COUNTRIES[code]})
            all_data[code][key] = fut.result()

    labels = {key: label for key, (_, label) in INDICATORS.items()}
    return jsonify({'countries': all_data, 'labels': labels})

@app.route('/api/exchange')
def api_exchange():
    return jsonify(get_exchange())

@app.route('/api/summary')
def api_summary():
    summary = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {}
        for code in COUNTRIES:
            for key in INDICATORS:
                futures[(code, key)] = ex.submit(get_indicator, code, key)
        for (code, key), fut in futures.items():
            rows = fut.result()
            if rows:
                year, value = rows[-1]
                summary.setdefault(code, {'name': COUNTRIES[code]})
                summary[code][key] = {'value': value, 'year': year,
                                      'label': INDICATORS[key][1]}
    return jsonify(summary)

@app.route('/api/cache-status')
def cache_status():
    with get_db() as conn:
        rows = conn.execute('SELECT country, key, updated_at FROM indicator_cache').fetchall()
        ex_row = conn.execute('SELECT updated_at FROM exchange_cache WHERE id=1').fetchone()
    result = []
    for r in rows:
        age = int((time.time() - r['updated_at']) / 60)
        result.append({'country': r['country'], 'key': r['key'],
                       'age_min': age, 'fresh': is_fresh(r['updated_at'])})
    return jsonify({
        'indicators': result,
        'exchange': {'age_min': int((time.time() - ex_row['updated_at']) / 60),
                     'fresh': is_fresh(ex_row['updated_at'])} if ex_row else None
    })

if __name__ == '__main__':
    init_db()
    prefetch_all()
    print('http://localhost:5050 でアクセスしてください')
    app.run(debug=False, port=5050, threaded=True)
