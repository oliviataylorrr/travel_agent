import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.wallet import wallet_status
print(wallet_status()) # Prints public address and presence flags, never password/secret.
