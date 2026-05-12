"""
YouTube Data API v3でMP4アップロード＋SRT字幕添付

OAuth2認証トークンは credentials/token.json にキャッシュする。
クォータ消費: 動画アップロード=1600units、字幕添付=50units/本
デフォルト上限10,000units/日 → 1日最大6本
"""

import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import CREDENTIALS_DIR

logger = logging.getLogger(__name__)

CLIENT_SECRET_FILE = CREDENTIALS_DIR / "client_secret.json"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# バッチ処理中にクライアントを使い回すためのキャッシュ
_youtube_client = None


def get_youtube_client():
    """OAuth2認証済みYouTubeクライアントを返す（バッチ内で再利用）"""
    global _youtube_client
    if _youtube_client is not None:
        return _youtube_client

    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET_FILE.exists():
                raise FileNotFoundError(
                    f"client_secret.json が見つかりません: {CLIENT_SECRET_FILE}\n"
                    "Google Cloud Console でOAuth2クライアントIDを作成してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    _youtube_client = build("youtube", "v3", credentials=creds)
    return _youtube_client


def upload_video(
    video_path: str,
    title: str,
    description: str,
    category_id: str = "22",
    privacy_status: str = "public",
    tags: list[str] | None = None,
) -> str:
    """MP4動画をYouTubeにアップロードする。"""
    youtube = get_youtube_client()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
            "tags": tags or [],
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        chunksize=1024 * 1024,
        resumable=True,
    )

    logger.info("アップロード開始: %s", title)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            if progress % 25 == 0:  # 25%ごとにログ出力（冗長さを削減）
                logger.info("アップロード進捗: %d%%", progress)

    video_id = response["id"]
    logger.info("アップロード完了: https://youtu.be/%s", video_id)
    return video_id


def attach_subtitle(video_id: str, srt_path: str, language: str = "ja") -> None:
    """字幕SRTファイルをYouTubeビデオに添付する。"""
    youtube = get_youtube_client()

    media = MediaFileUpload(srt_path, mimetype="application/octet-stream", resumable=False)

    logger.info("字幕添付: video_id=%s", video_id)
    youtube.captions().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "language": language,
                "name": "自動生成字幕",
                "isDraft": False,
            }
        },
        media_body=media,
    ).execute()
    logger.info("字幕添付完了")


def add_to_playlist(video_id: str, playlist_id: str) -> None:
    """動画をプレイリストに追加する。"""
    youtube = get_youtube_client()
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        },
    ).execute()
    logger.info("プレイリスト追加: %s → %s", video_id, playlist_id)


def upload_episode(
    video_path: str,
    srt_path: str,
    title: str,
    description: str,
    config: dict,
) -> str:
    """動画アップロード＋字幕添付をまとめて実行する。"""
    youtube_config = config.get("youtube", {})
    try:
        video_id = upload_video(
            video_path=video_path,
            title=title,
            description=description,
            category_id=youtube_config.get("category_id", "22"),
            privacy_status=youtube_config.get("privacy_status", "public"),
            tags=youtube_config.get("default_tags", []),
        )
        attach_subtitle(video_id, srt_path)
        return video_id
    except HttpError as e:
        if e.resp.status == 403:
            logger.error("クォータ超過(403)を検知。処理を中断します。")
        raise
