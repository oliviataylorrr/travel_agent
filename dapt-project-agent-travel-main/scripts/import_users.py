import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,csv
from app.schema import init_db
from app.auth import password_hash
from app.db import transaction
p=argparse.ArgumentParser()
p.add_argument('csv_file');p.add_argument('--dry-run',action='store_true')
a=p.parse_args()
with open(a.csv_file,encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
if not rows or not {'username','password'}.issubset(rows[0]):raise SystemExit('CSV must include username,password')
seen=set()
for row in rows:
    u=row['username'].strip().lower();pw=row['password']
    if not u or len(pw)<8 or u in seen:raise SystemExit('Invalid, duplicated username or password shorter than 8 characters')
    seen.add(u)
if not a.dry_run:
    init_db()
    with transaction() as cn:
        for row in rows:
            cn.execute('INSERT INTO users(username,password_hash) VALUES(?,?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash',
                (row['username'].strip().lower(),password_hash(row['password'])))
print(f'Rows: {len(rows)}, dry run: {a.dry_run}')
