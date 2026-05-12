"""
ローカルMac上でYouTube OAuth認証を行い token.json を生成する。

使い方:
  cd podcast-to-youtube
  pip install google-auth-oauthlib google-api-python-client
  python scripts/auth_local.py

生成された config/credentials/token.json を
Google Drive の同じパスにアップロードすればColabで認証不要になる。
"""

from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT_DIR = Path(__file__).parent.parent
CLIENT_SECRET_FILE = ROOT_DIR / "config" / "credentials" / "client_secret.json"
TOKEN_FILE = ROOT_DIR / "config" / "credentials" / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

if not CLIENT_SECRET_FILE.exists():
    print(f"ERROR: {CLIENT_SECRET_FILE} が見つかりません")
    exit(1)

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
creds = flow.run_local_server(port=0)  # ブラウザが開きます

TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
print(f"\n✅ token.json を保存しました: {TOKEN_FILE}")
print("\n次の手順:")
print("  このファイルをGoogle Driveの以下のパスにアップロードしてください:")
print("  podcast-to-youtube/config/credentials/token.json")
