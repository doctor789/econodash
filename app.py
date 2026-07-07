from flask import Flask, render_template, jsonify, make_response
import requests
import sqlite3
import json
import time
import threading
import concurrent.futures
from pathlib import Path
import yfinance as yf
import quantum_vc

app = Flask(__name__)

_yf_lock = threading.Lock()  # serialize all yfinance fetches to avoid rate limiting

DB_PATH = Path(__file__).parent / 'cache.db'
CACHE_TTL       = 60 * 60 * 24
STOCK_CACHE_TTL = 60 * 60
DB_VERSION = '10'

COUNTRIES = {
    'JP': '日本', 'US': 'アメリカ', 'CN': '中国', 'DE': 'ドイツ', 'GB': 'イギリス',
}

INDICATORS = {
    'gdp_growth':    ('NY.GDP.MKTP.KD.ZG', 'GDP成長率'),
    'inflation':     ('FP.CPI.TOTL.ZG',    'インフレ率'),
    'unemployment':  ('SL.UEM.TOTL.ZS',    '失業率'),
    'current_acct':  ('BN.CAB.XOKA.GD.ZS', '経常収支(GDP比%)'),
    'trade_balance': ('NE.RSB.GNFS.ZS',    '貿易収支(GDP比%)'),
    'real_rate':     ('FR.INR.RINR',        '実質金利'),
    'debt_gdp':      ('GC.DOD.TOTL.GD.ZS', '債務残高/GDP比'),
}

STOCK_INDICES = {
    '^N225':    {'name': '日経225',   'country': 'JP'},
    '^GSPC':    {'name': 'S&P500',   'country': 'US'},
    '^GDAXI':   {'name': 'DAX',      'country': 'DE'},
    '^FTSE':    {'name': 'FTSE100',  'country': 'GB'},
    '000001.SS':{'name': '上海総合', 'country': 'CN'},
}

STOCK_PE_PROXY = {
    '^GSPC':    'SPY',
    '^N225':    'EWJ',
    '^GDAXI':   'EWG',
    '^FTSE':    'EWU',
    '000001.SS':'FXI',
}

BOND_TICKERS = {
    '^TNX': {'name': '米国10年債', 'country': 'US'},
    '^FVX': {'name': '米国5年債',  'country': 'US'},
    '^TYX': {'name': '米国30年債', 'country': 'US'},
    '^IRX': {'name': '米国3ヶ月',  'country': 'US'},
}

# FRED series IDs
FRED_SERIES = {
    'bond10yr': {
        'US': 'IRLTLT01USM156N',
        'JP': 'IRLTLT01JPM156N',
        'DE': 'IRLTLT01DEM156N',
        'GB': 'IRLTLT01GBM156N',
    },
    'policy': {
        'US': 'FEDFUNDS',
        'JP': 'IRSTCI01JPM156N',
        'DE': 'IRSTCI01DEM156N',
        'GB': 'IRSTCI01GBM156N',
    },
    'monetary_base': {
        'US': 'BOGMBASE',          # 米国マネタリーベース（月次、1959〜）
        'JP': 'MABMM301JPM189S',   # 日本M0・月次（OECD、〜2023）
        'DE': 'MABMM301EZM189S',   # ユーロ圏M0・月次（OECD、〜2023）
        'GB': 'MABMM301GBM189S',   # 英国M0・月次（OECD、〜2023）
    },
}

# ---------- DB ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS indicator_cache (
            country TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL,
            updated_at REAL NOT NULL, PRIMARY KEY (country, key))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS exchange_cache (
            id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS stock_cache (
            ticker TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS oecd_cache (
            indicator TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)''')
        row = conn.execute("SELECT value FROM _meta WHERE key='db_version'").fetchone()
        if not row or row['value'] != DB_VERSION:
            print('DBバージョン更新: キャッシュをクリアします...')
            conn.execute('DELETE FROM indicator_cache')
            conn.execute('DELETE FROM stock_cache')
            conn.execute('DELETE FROM oecd_cache')
            conn.execute("INSERT OR REPLACE INTO _meta VALUES ('db_version', ?)", (DB_VERSION,))

SEED_PATH = Path(__file__).parent / 'seed_data.json'

def load_seed_data():
    if not SEED_PATH.exists():
        return
    try:
        with open(SEED_PATH, encoding='utf-8') as f:
            seed = json.load(f)
    except Exception as e:
        print(f'シードデータ読み込みエラー: {e}')
        return
    now = time.time()
    loaded = 0
    with get_db() as conn:
        for ticker, data in seed.get('stocks', {}).items():
            if not conn.execute('SELECT 1 FROM stock_cache WHERE ticker=?', (ticker,)).fetchone():
                conn.execute('INSERT OR IGNORE INTO stock_cache VALUES (?,?,?)',
                             (ticker, json.dumps(data), now))
                loaded += 1
        for country, inds in seed.get('indicators', {}).items():
            for key, data in inds.items():
                if not conn.execute('SELECT 1 FROM indicator_cache WHERE country=? AND key=?',
                                    (country, key)).fetchone():
                    conn.execute('INSERT OR IGNORE INTO indicator_cache VALUES (?,?,?,?)',
                                 (country, key, json.dumps(data), now))
                    loaded += 1
        if seed.get('exchange'):
            if not conn.execute('SELECT 1 FROM exchange_cache WHERE id=1').fetchone():
                conn.execute('INSERT OR IGNORE INTO exchange_cache (id,data,updated_at) VALUES (1,?,?)',
                             (json.dumps(seed['exchange']), now))
                loaded += 1
        for rate_type in ['bond10yr', 'policy', 'monetary_base']:
            if rate_type in seed:
                if not conn.execute('SELECT 1 FROM oecd_cache WHERE indicator=?', (rate_type,)).fetchone():
                    conn.execute('INSERT OR IGNORE INTO oecd_cache VALUES (?,?,?)',
                                 (rate_type, json.dumps(seed[rate_type]), now))
                    loaded += 1
    if loaded:
        print(f'シードデータ読み込み完了 ({loaded}件)')

def is_fresh(updated_at, ttl=CACHE_TTL):
    return (time.time() - updated_at) < ttl

# ---------- World Bank ----------

def wb_fetch(country, indicator_code, years=80):
    url = (f'https://api.worldbank.org/v2/country/{country}'
           f'/indicator/{indicator_code}?format=json&mrv={years}&per_page=100')
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
    if data:
        with get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO indicator_cache VALUES (?,?,?,?)',
                         (country, key, json.dumps(data), time.time()))
    return data

# FRED/IMF から計算・取得するためWBから再取得しない指標
COMPUTED_INDICATORS = {'real_rate', 'debt_gdp'}

def get_indicator(country, key):
    ind_code = INDICATORS[key][0]
    with get_db() as conn:
        row = conn.execute('SELECT data, updated_at FROM indicator_cache WHERE country=? AND key=?',
                           (country, key)).fetchone()
    if row and is_fresh(row['updated_at']):
        return json.loads(row['data'])
    # 計算系指標はWB再取得せず既存キャッシュをそのまま返す
    if key in COMPUTED_INDICATORS:
        return json.loads(row['data']) if row else []
    return fetch_and_cache_indicator(country, key, ind_code)

# ---------- 為替 ----------

def fetch_and_cache_exchange():
    try:
        r = requests.get('https://open.er-api.com/v6/latest/USD', timeout=10)
        r.raise_for_status()
        data = r.json()
        targets = ['JPY', 'EUR', 'CNY', 'GBP', 'AUD', 'KRW', 'CHF', 'CAD']
        rates = {c: round(data['rates'][c], 4) for c in targets if c in data['rates']}
        result = {'base': 'USD', 'rates': rates, 'updated': data.get('time_last_update_utc', '')}
    except Exception:
        result = {'base': 'USD',
                  'rates': {'JPY': 149.5, 'EUR': 0.92, 'CNY': 7.24,
                            'GBP': 0.79, 'AUD': 1.53, 'KRW': 1325.0,
                            'CHF': 0.89, 'CAD': 1.36},
                  'updated': 'N/A'}
    with get_db() as conn:
        conn.execute('INSERT OR REPLACE INTO exchange_cache (id,data,updated_at) VALUES (1,?,?)',
                     (json.dumps(result), time.time()))
    return result

def get_exchange():
    with get_db() as conn:
        row = conn.execute('SELECT data, updated_at FROM exchange_cache WHERE id=1').fetchone()
    if row and is_fresh(row['updated_at']):
        return json.loads(row['data'])
    return fetch_and_cache_exchange()

# ---------- 株・米国債 (yfinance) ----------

def fetch_yf(ticker):
    with _yf_lock:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period='max')
            if hist.empty:
                print(f'yfinance {ticker}: history empty')
                time.sleep(1.5)
                return None
            fi = t.fast_info
            current  = round(float(fi.last_price), 2)
            prev     = round(float(fi.previous_close), 2)
            change_p = round((current - prev) / prev * 100, 2) if prev else 0
            history  = [(str(d.date()), round(float(v), 2))
                        for d, v in zip(hist.index, hist['Close'])]
            per = None
            try:
                pe_ticker = STOCK_PE_PROXY.get(ticker, ticker)
                pe_src = yf.Ticker(pe_ticker) if pe_ticker != ticker else t
                per_raw = pe_src.info.get('trailingPE')
                if per_raw and per_raw == per_raw:  # not NaN
                    per = round(float(per_raw), 1)
            except Exception:
                pass
            time.sleep(1.5)
            return {'current': current, 'prev': prev, 'change_pct': change_p, 'history': history, 'per': per}
        except Exception as e:
            print(f'yfinance {ticker} error: {e}')
            time.sleep(1.5)
            return None

def fetch_and_cache_stock(ticker):
    data = fetch_yf(ticker)
    if data:
        with get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO stock_cache VALUES (?,?,?)',
                         (ticker, json.dumps(data), time.time()))
    return data

def get_stock(ticker):
    with get_db() as conn:
        row = conn.execute('SELECT data, updated_at FROM stock_cache WHERE ticker=?',
                           (ticker,)).fetchone()
    if row and is_fresh(row['updated_at'], STOCK_CACHE_TTL):
        return json.loads(row['data'])
    return fetch_and_cache_stock(ticker)

# ---------- FRED 10年国債・政策金利 ----------

def fetch_fred(series_id, start_year=1950):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        rows = []
        for line in r.text.strip().split('\n')[1:]:
            parts = line.strip().split(',')
            if len(parts) == 2 and parts[1] and parts[1] != '.':
                date, val = parts[0][:7], parts[1]  # "YYYY-MM"
                if int(date[:4]) >= start_year:
                    rows.append((date, round(float(val), 3)))
        return rows
    except Exception as e:
        print(f'FRED {series_id} error: {e}')
        return []

def fetch_and_cache_rates(rate_type):
    series_map = FRED_SERIES[rate_type]
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {code: ex.submit(fetch_fred, sid) for code, sid in series_map.items()}
        for code, fut in futures.items():
            result[code] = fut.result()
    if any(v for v in result.values()):
        with get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO oecd_cache VALUES (?,?,?)',
                         (rate_type, json.dumps(result), time.time()))
    return result

def get_rates(rate_type):
    with get_db() as conn:
        row = conn.execute('SELECT data, updated_at FROM oecd_cache WHERE indicator=?',
                           (rate_type,)).fetchone()
    if row and is_fresh(row['updated_at']):
        return json.loads(row['data'])
    return fetch_and_cache_rates(rate_type)

# ---------- 実質金利（政策金利 - インフレ率）計算 ----------

def compute_and_cache_real_rates():
    """FRED政策金利 - WBインフレ率 で実質金利を計算しキャッシュに保存。"""
    try:
        policy_all = get_rates('policy')
    except Exception:
        policy_all = {}
    with get_db() as conn:
        for country in ['US', 'JP', 'DE', 'GB']:
            policy_data = policy_all.get(country, [])
            if not policy_data:
                continue
            row = conn.execute('SELECT data FROM indicator_cache WHERE country=? AND key=?',
                               (country, 'inflation')).fetchone()
            if not row:
                continue
            inflation = json.loads(row['data'])
            inf_map = {y: v for y, v in inflation}
            # 月次→年次平均
            annual = {}
            for date, val in policy_data:
                y = date[:4]
                annual.setdefault(y, []).append(val)
            avg = {y: round(sum(vs)/len(vs), 3) for y, vs in annual.items()}
            # 実質金利 = 政策金利年平均 - インフレ率
            real = sorted(
                [(y, round(avg[y] - inf_map[y], 2))
                 for y in avg if y in inf_map],
                key=lambda x: x[0]
            )
            if real:
                conn.execute('INSERT OR REPLACE INTO indicator_cache VALUES (?,?,?,?)',
                             (country, 'real_rate', json.dumps(real), time.time()))
                print(f'実質金利計算完了 {country}: {len(real)}件 ({real[0][0]}~{real[-1][0]})')

# ---------- 債務残高/GDP（IMF DataMapper）----------

IMF_COUNTRY = {'JP': 'JPN', 'US': 'USA', 'CN': 'CHN', 'DE': 'DEU', 'GB': 'GBR'}

def fetch_and_cache_imf_debt():
    """IMF DataMapper APIから政府債務/GDPを取得してキャッシュに保存。"""
    codes = ','.join(IMF_COUNTRY.values())
    url = f'https://www.imf.org/external/datamapper/api/v1/GGXWDG_NGDP/{codes}'
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        values = r.json().get('values', {}).get('GGXWDG_NGDP', {})
        current_year = str(time.strftime('%Y'))
        with get_db() as conn:
            for our_code, imf_code in IMF_COUNTRY.items():
                country_data = values.get(imf_code, {})
                series = sorted(
                    [(str(y), round(float(v), 1))
                     for y, v in country_data.items()
                     if str(y) <= current_year and v is not None],
                    key=lambda x: x[0]
                )
                if series:
                    conn.execute('INSERT OR REPLACE INTO indicator_cache VALUES (?,?,?,?)',
                                 (our_code, 'debt_gdp', json.dumps(series), time.time()))
                    print(f'IMF債務GDP {our_code}: {len(series)}件 ({series[0][0]}~{series[-1][0]})')
    except Exception as e:
        print(f'IMF debt fetch error: {e}')

# ---------- 起動時プリフェッチ ----------

def prefetch_all():
    print('DBキャッシュ確認中...')
    with get_db() as conn:
        tasks_ind = [(c, k, ic) for c in COUNTRIES
                     for k, (ic, _) in INDICATORS.items()
                     if k not in COMPUTED_INDICATORS  # FRED/IMF系はWB取得しない
                     and (not (row := conn.execute(
                         'SELECT updated_at FROM indicator_cache WHERE country=? AND key=?',
                         (c, k)).fetchone()) or not is_fresh(row['updated_at']))]

        tasks_stk = [tk for tk in {**STOCK_INDICES, **BOND_TICKERS}
                     if not (row := conn.execute(
                         'SELECT updated_at FROM stock_cache WHERE ticker=?',
                         (tk,)).fetchone()) or not is_fresh(row['updated_at'], STOCK_CACHE_TTL)]

        tasks_oecd = [rt for rt in ['bond10yr', 'policy', 'monetary_base']
                      if not (row := conn.execute(
                          'SELECT updated_at FROM oecd_cache WHERE indicator=?',
                          (rt,)).fetchone()) or not is_fresh(row['updated_at'])]

        ex_row = conn.execute('SELECT updated_at FROM exchange_cache WHERE id=1').fetchone()
        need_ex = not ex_row or not is_fresh(ex_row['updated_at'])

    total = len(tasks_ind) + len(tasks_stk) + len(tasks_oecd) + (1 if need_ex else 0)
    if total:
        print(f'APIから取得中... (指標{len(tasks_ind)}, 株{len(tasks_stk)}, OECD{len(tasks_oecd)})')
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for c, k, ic in tasks_ind:
                ex.submit(fetch_and_cache_indicator, c, k, ic)
            for rt in tasks_oecd:
                ex.submit(fetch_and_cache_rates, rt)
            if need_ex:
                ex.submit(fetch_and_cache_exchange)
        # Fetch stocks sequentially to avoid Yahoo Finance rate limiting
        for tk in tasks_stk:
            fetch_and_cache_stock(tk)
            time.sleep(2)
        print('キャッシュ完了！')
    else:
        print('キャッシュ有効。即時提供します。')
    # 計算系指標（常に最新化）
    compute_and_cache_real_rates()
    fetch_and_cache_imf_debt()

# ---------- Flask ----------

@app.route('/')
def index():
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/api/indicators')
def api_indicators():
    all_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {(c, k): ex.submit(get_indicator, c, k)
                   for c in COUNTRIES for k in INDICATORS}
        for (c, k), fut in futures.items():
            all_data.setdefault(c, {'name': COUNTRIES[c]})
            all_data[c][k] = fut.result()
    labels = {k: v[1] for k, v in INDICATORS.items()}
    return jsonify({'countries': all_data, 'labels': labels})

@app.route('/api/exchange')
def api_exchange():
    return jsonify(get_exchange())

@app.route('/api/summary')
def api_summary():
    summary = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {(c, k): ex.submit(get_indicator, c, k)
                   for c in COUNTRIES for k in INDICATORS}
        for (c, k), fut in futures.items():
            rows = fut.result()
            if rows:
                year, value = rows[-1]
                summary.setdefault(c, {'name': COUNTRIES[c]})
                summary[c][k] = {'value': value, 'year': year, 'label': INDICATORS[k][1]}
    return jsonify(summary)

def thin_history(history, max_points=2000):
    if len(history) <= max_points:
        return history
    step = len(history) // max_points
    return history[::step]

@app.route('/api/markets')
def api_markets():
    result = {'stocks': {}, 'bonds': {}}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        sf = {tk: ex.submit(get_stock, tk) for tk in STOCK_INDICES}
        bf = {tk: ex.submit(get_stock, tk) for tk in BOND_TICKERS}
        for tk, fut in sf.items():
            d = fut.result()
            if d:
                entry = {**d, **STOCK_INDICES[tk]}
                entry['history'] = thin_history(entry.get('history', []))
                result['stocks'][tk] = entry
        for tk, fut in bf.items():
            d = fut.result()
            if d:
                entry = {**d, **BOND_TICKERS[tk]}
                entry['history'] = thin_history(entry.get('history', []))
                result['bonds'][tk] = entry
    return jsonify(result)

@app.route('/api/interest_rates')
def api_interest_rates():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_bond   = ex.submit(get_rates, 'bond10yr')
        f_policy = ex.submit(get_rates, 'policy')
    country_names = {'JP': '日本', 'US': 'アメリカ', 'DE': 'ドイツ', 'GB': 'イギリス'}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        f_mb = ex.submit(get_rates, 'monetary_base')
    return jsonify({
        'bond10yr':      {k: {'data': thin_history(v, 1000), 'name': country_names.get(k, k)}
                          for k, v in f_bond.result().items()},
        'policy':        {k: {'data': thin_history(v, 1000), 'name': country_names.get(k, k)}
                          for k, v in f_policy.result().items()},
        'monetary_base': {k: {'data': thin_history(v, 600), 'name': country_names.get(k, k)}
                          for k, v in f_mb.result().items()},
    })

@app.route('/api/vc_correlation')
def api_vc_correlation():
    """株価指数間の VC相関を量子情報論（量子相互情報量・エンタングルメント
    エントロピー）の枠組みで再定式化した相関行列を返す。"""
    histories = {}
    names = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = {tk: ex.submit(get_stock, tk) for tk in STOCK_INDICES}
        for tk, fut in futures.items():
            d = fut.result()
            hist = d.get('history') if d else None
            if hist and len(hist) > 30:
                # 直近約3年（取引日）に限定して近年の連関を評価
                histories[tk] = hist[-800:]
                names[tk] = STOCK_INDICES[tk]['name']
    result = quantum_vc.correlation_matrix(histories)
    result['names'] = [names[k] for k in result['keys']]
    result['tickers'] = result['keys']
    return jsonify(result)

@app.route('/api/debug')
def api_debug():
    with get_db() as conn:
        stocks = {r[0]: r[1] for r in conn.execute('SELECT ticker, updated_at FROM stock_cache')}
    import time as _t
    now = _t.time()
    return jsonify({
        'stocks_cached': {tk: {'age_min': round((now - ts) / 60, 1)} for tk, ts in stocks.items()},
        'stock_count': len(stocks),
        'seed_exists': SEED_PATH.exists(),
    })

def start_prefetch():
    threading.Thread(target=prefetch_all, daemon=True).start()

init_db()
load_seed_data()
start_prefetch()

if __name__ == '__main__':
    print('http://localhost:5050 でアクセスしてください')
    app.run(debug=False, port=5050, threaded=True)
