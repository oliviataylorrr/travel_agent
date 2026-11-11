"""Configuration common to both apps. Never commit credentials."""
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA_DIR=Path(os.environ.get('DATA_DIR',str(ROOT/'data'))).expanduser().resolve()
DATA_DIR.mkdir(parents=True,exist_ok=True)
DB_PATH=DATA_DIR/'marketplace.sqlite3' if os.environ.get('APP_KIND','student')=='marketplace' else DATA_DIR/'student.sqlite3'
PUBLIC_BASE_URL=os.environ.get('PUBLIC_BASE_URL','http://localhost:8000').rstrip('/')
A2A_SHARED_SECRET=os.environ.get('A2A_SHARED_SECRET','')
SESSION_SECRET_KEY=os.environ.get('SESSION_SECRET_KEY','')
MARKETPLACE_URL=os.environ.get('MARKETPLACE_URL','').rstrip('/')
OPENAI_MODEL=os.environ.get('OPENAI_MODEL','gpt-4.1-mini')
PAYMENT_MODE=os.environ.get('PAYMENT_MODE','simulated')
