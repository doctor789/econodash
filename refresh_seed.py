#!/usr/bin/env python3
"""
WB・FRED・yfinanceをすべて再取得してseed_data.jsonを更新。
"""
import json, requests, concurrent.futures, time
from pathlib import Path
import yfinance as yf

SEED_PATH = Path(__file__).parent / 'seed_data.json'

COUNTRIES = ['JP', 'US', 'CN', 'DE', 'GB']
INDICATORS = {
    'gdp_growth':    'NY.GDP.MKTP.KD.ZG',
    'inflation':     'FP.CPI.TOTL.ZG',
    'unemployment':  'SL.UEM.TOTL.ZS',
    'current_acct':  'BN.CAB.XOKA.GD.ZS',
    'trade_balance': 'NE.RSB.GNFS.ZS',
}
STOCK_TICKERS = ['^N225', '^GSPC', '^GDAXI', '^FTSE', '000001.SS',
                 '^TNX', '^FVX', '^TYX', '^IRX']
STOCK_META = {
    '^N225':     {'name': '日経225',   'country': 'JP'},
    '^GSPC':     {'name': 'S&P500',   'country': 'US'},
    '^GDAXI':    {'name': 'DAX',      'country': 'DE'},
    '^FTSE':     {'name': 'FTSE100',  'country': 'GB'},
    '000001.SS': {'name': '上海総合', 'country': 'CN'},
    '^TNX':      {'name': '米国10年債', 'country': 'US'},
    '^FVX':      {'name': '米国5年債',  'country': 'US'},
    '^TYX':      {'name': '米国30年債', 'country': 'US'},
    '^IRX':      {'name': '米国3ヶ月',  'country': 'US'},
}
STOCK_PE_PROXY = {
    '^GSPC': 'SPY', '^N225': 'EWJ', '^GDAXI': 'EWG',
    '^FTSE': 'EWU', '000001.SS': 'FXI',
}

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
}

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
    except Exception as e:
        print(f'  WB error {country}/{indicator_code}: {e}')
    return []

def fetch_fred(series_id, start_year=1950):
    url = f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}'
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        rows = []
        for line in r.text.strip().split('\n')[1:]:
            parts = line.strip().split(',')
            if len(parts) == 2 and parts[1] and parts[1] != '.':
                date, val = parts[0][:7], parts[1]
                if int(date[:4]) >= start_year:
                    rows.append((date, round(float(val), 3)))
        return rows
    except Exception as e:
        print(f'  FRED error {series_id}: {e}')
    return []

def fetch_yf_stock(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period='max')
        if hist.empty:
            return None
        fi = t.fast_info
        current  = round(float(fi.last_price), 2)
        prev     = round(float(fi.previous_close), 2)
        change_p = round((current - prev) / prev * 100, 2) if prev else 0
        history  = [(str(d.date()), round(float(v), 2))
                    for d, v in zip(hist.index, hist['Close'])]
        per = None
        try:
            pe_src = yf.Ticker(STOCK_PE_PROXY.get(ticker, ticker))
            per_raw = pe_src.info.get('trailingPE')
            if per_raw and per_raw == per_raw:
                per = round(float(per_raw), 1)
        except Exception:
            pass
        return {'current': current, 'prev': prev, 'change_pct': change_p,
                'history': history, 'per': per,
                'name': STOCK_META.get(ticker, {}).get('name', ticker),
                'country': STOCK_META.get(ticker, {}).get('country', '')}
    except Exception as e:
        print(f'  yfinance error {ticker}: {e}')
        return None

def main():
    # 既存シードをフォールバック用に読み込み
    old = {}
    if SEED_PATH.exists():
        with open(SEED_PATH, encoding='utf-8') as f:
            old = json.load(f)
    seed = {'stocks': {}, 'exchange': old.get('exchange', {})}

    # yfinance 全履歴取得（period='max'）
    print(f'yfinance データ取得中（{len(STOCK_TICKERS)}銘柄）...')
    for ticker in STOCK_TICKERS:
        print(f'  {ticker}...', end=' ', flush=True)
        data = fetch_yf_stock(ticker)
        time.sleep(2)
        if data and data['history']:
            h = data['history']
            print(f'{len(h)}件 ({h[0][0]}〜{h[-1][0]})')
            seed['stocks'][ticker] = data
        else:
            seed['stocks'][ticker] = old.get('stocks', {}).get(ticker, {})
            print('失敗 → 既存データ流用')

    # World Bank 指標を再取得（80年分）
    print('World Bank データ取得中（80年分）...')
    seed['indicators'] = {}
    tasks = [(c, k, code) for c in COUNTRIES for k, code in INDICATORS.items()]

    def fetch_wb_task(args):
        country, key, code = args
        data = wb_fetch(country, code, years=80)
        return country, key, data

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for country, key, data in ex.map(fetch_wb_task, tasks):
            if not data:
                # 取得失敗時は既存データを流用
                data = old.get('indicators', {}).get(country, {}).get(key, [])
                if data:
                    print(f'  WB {country}/{key}: 既存データ流用 {len(data)}件')
            else:
                print(f'  WB {country}/{key}: {len(data)}件 ({data[0][0]}〜{data[-1][0]})')
            seed['indicators'].setdefault(country, {})[key] = data

    # FRED 金利データを再取得（1950年〜）
    print('FRED データ取得中（1950年〜）...')
    for rate_type, series_map in FRED_SERIES.items():
        result = {}
        for code, sid in series_map.items():
            data = fetch_fred(sid, start_year=1950)
            result[code] = data
            if data:
                print(f'  FRED {rate_type}/{code}: {len(data)}件 ({data[0][0]}〜{data[-1][0]})')
        seed[rate_type] = result

    with open(SEED_PATH, 'w', encoding='utf-8') as f:
        json.dump(seed, f, ensure_ascii=False)

    stk = len(seed.get('stocks', {}))
    ind = sum(len(v) for v in seed.get('indicators', {}).values())
    size = SEED_PATH.stat().st_size // 1024
    print(f'\nseed_data.json 生成完了: 株{stk}銘柄, 指標{ind}件, {size}KB')

if __name__ == '__main__':
    main()
