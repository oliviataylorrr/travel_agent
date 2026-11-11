import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
"""Optional Render startup/setup helper: install encrypted keystore from a base64 secret.
Set WALLET_KEYSTORE_B64 with the base64 of the EXISTING encrypted keystore JSON.
Does not generate, decrypt, or transmit a private key.
"""
import os,base64,json
from pathlib import Path
from app.settings import DATA_DIR
raw=os.environ.get('WALLET_KEYSTORE_B64','')
if not raw:raise SystemExit('Set WALLET_KEYSTORE_B64 in Render before running this helper')
blob=base64.b64decode(raw,validate=True)
obj=json.loads(blob)
if not isinstance(obj,dict) or not {'crypto','Crypto'}.intersection(obj):
    raise SystemExit('Invalid encrypted Ethereum keystore JSON')
path=Path(os.environ.get('WALLET_DIR',str(DATA_DIR/'wallet')))
path.mkdir(parents=True,exist_ok=True)
f=path/'keystore.json'
if f.exists():raise SystemExit('Keystore exists; refusing to overwrite')
f.write_bytes(blob);f.chmod(0o600)
print('Installed encrypted keystore at',f,'(address only:',obj.get('address','unknown'),')')
