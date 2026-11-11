import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.settings import A2A_SHARED_SECRET,PUBLIC_BASE_URL,SESSION_SECRET_KEY,MARKETPLACE_URL,DATA_DIR,PAYMENT_MODE
from app.agent import profile
p=profile()
checks={'A2A_SHARED_SECRET':len(A2A_SHARED_SECRET)>=16,'SESSION_SECRET_KEY':len(SESSION_SECRET_KEY)>=24,
        'PUBLIC_BASE_URL':PUBLIC_BASE_URL.startswith(('https://','http://localhost')),
        'MARKETPLACE_URL':MARKETPLACE_URL.startswith(('https://','http://localhost')),
        'DATA_DIR':DATA_DIR.exists(),'PAYMENT_MODE':PAYMENT_MODE=='simulated'}
print('Service type:',p['service_type'],'Agent ID:',p['agent_id'])
for k,v in checks.items():print(k, 'OK' if v else 'MISSING/INVALID')
if not all(checks.values()):raise SystemExit('Configuration incomplete')
