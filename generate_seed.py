#!/usr/bin/env python3
"""
デプロイ前に実行して seed_data.json を更新するスクリプト。
  python generate_seed.py
"""
import sqlite3, json
from pathlib import Path

DB_PATH = Path(__file__).parent / 'cache.db'
OUT_PATH = Path(__file__).parent / 'seed_data.json'

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    seed = {}

    seed['stocks'] = {r['ticker']: json.loads(r['data'])
                      for r in conn.execute('SELECT ticker, data FROM stock_cache')}

    seed['indicators'] = {}
    for r in conn.execute('SELECT country, key, data FROM indicator_cache'):
        seed['indicators'].setdefault(r['country'], {})[r['key']] = json.loads(r['data'])

    row = conn.execute('SELECT data FROM exchange_cache WHERE id=1').fetchone()
    if row:
        seed['exchange'] = json.loads(row['data'])

    for r in conn.execute('SELECT indicator, data FROM oecd_cache'):
        seed[r['indicator']] = json.loads(r['data'])

    conn.close()

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(seed, f, ensure_ascii=False)

    stk = len(seed.get('stocks', {}))
    ind = sum(len(v) for v in seed.get('indicators', {}).values())
    print(f'seed_data.json 生成完了: 株{stk}銘柄, 指標{ind}件, {OUT_PATH.stat().st_size // 1024}KB')

if __name__ == '__main__':
    main()
