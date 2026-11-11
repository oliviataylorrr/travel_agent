"""Read ONLY public wallet metadata. Keystore/private key is never exposed to an LLM."""
import json,os
from pathlib import Path
from .settings import ROOT,DATA_DIR

def wallet_directory():
    p=Path(os.environ.get('WALLET_DIR',str(DATA_DIR/'wallet')))
    if not p.exists() and (ROOT/'wallet').exists():p=ROOT/'wallet'
    return p

def public_wallet_address():
    path=wallet_directory()
    txt=path/'address.txt'
    if txt.exists():return txt.read_text().strip()
    key=path/'keystore.json'
    if key.exists():
        try:return json.loads(key.read_text()).get('address','')
        except Exception:pass
    return ''

def wallet_status():
    path=wallet_directory()
    return {'wallet_directory':str(path),'keystore_present':(path/'keystore.json').exists(),
            'password_configured':bool(os.environ.get('WALLET_PASSWORD') or (path/'.password').exists()),
            'public_address':public_wallet_address()}
