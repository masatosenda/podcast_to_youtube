"""YouTube プレイリスト作成ユーティリティ

使い方:
  python -m scripts.create_playlist

作成されたプレイリストIDを config/config.yaml の playlist_id に設定してください。
"""

from .youtube_uploader import get_youtube_client


def create_playlist(title: str, description: str = "", privacy: str = "public") -> str:
    youtube = get_youtube_client()
    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": description,
            },
            "status": {
                "privacyStatus": privacy,
            },
        },
    ).execute()
    playlist_id = response["id"]
    print(f"プレイリスト作成完了: {title}")
    print(f"  ID: {playlist_id}")
    print(f"  URL: https://www.youtube.com/playlist?list={playlist_id}")
    return playlist_id


if __name__ == "__main__":
    create_playlist(
        title="いきぬき給湯室",
        description="ポッドキャスト「いきぬき給湯室」の全エピソード",
    )
