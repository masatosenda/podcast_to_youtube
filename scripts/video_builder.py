"""
ffmpegで静止画＋音声からMP4を生成する

出力解像度: 1920x1080（アスペクト比を保持してパディング）
映像コーデック: libx264 (stillimage tune)
音声コーデック: AAC 192kbps
"""

import subprocess
from pathlib import Path


def build_video(
    artwork_path: str,
    audio_path: str,
    output_path: str,
) -> None:
    """
    静止画と音声からMP4ファイルを生成する。

    Args:
        artwork_path: アートワーク画像パス（JPG/PNG）
        audio_path: 音声ファイルパス（mp3/m4a/wav）
        output_path: 出力MP4ファイルパス

    Raises:
        subprocess.CalledProcessError: ffmpegが失敗した場合
        FileNotFoundError: 入力ファイルが存在しない場合
    """
    artwork = Path(artwork_path)
    audio = Path(audio_path)
    output = Path(output_path)

    if not artwork.exists():
        raise FileNotFoundError(f"アートワーク画像が見つかりません: {artwork_path}")
    if not audio.exists():
        raise FileNotFoundError(f"音声ファイルが見つかりません: {audio_path}")

    output.parent.mkdir(parents=True, exist_ok=True)

    # 1920x1080にアスペクト比を保持してフィット＋パディング
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
    )

    cmd = [
        "ffmpeg",
        "-y",                    # 既存ファイルを上書き
        "-loop", "1",
        "-i", str(artwork),
        "-i", str(audio),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-vf", vf,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output),
    ]

    print(f"[video_builder] MP4生成開始: {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd,
            output=result.stdout,
            stderr=result.stderr,
        )

    print(f"[video_builder] MP4生成完了: {output_path} ({output.stat().st_size / 1024 / 1024:.1f} MB)")
