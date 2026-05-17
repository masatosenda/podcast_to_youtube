"""
ffmpegで静止画＋音声からMP4を生成する

出力解像度: 1920x1080（アスペクト比を保持してパディング）
映像コーデック: libx264 (stillimage tune)
音声コーデック: AAC 192kbps
"""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# libass対応のffmpegを検索（macOS ARM → Intel → Linux の順でフォールバック）
_FFMPEG_CANDIDATES = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",  # macOS ARM (Homebrew)
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",      # macOS Intel (Homebrew)
]


def _find_ffmpeg() -> str:
    """利用可能なffmpegパスを返す"""
    for candidate in _FFMPEG_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    # フォールバック: PATHから検索
    return shutil.which("ffmpeg") or "ffmpeg"


FFMPEG = _find_ffmpeg()


def build_video(
    artwork_path: str,
    audio_path: str,
    output_path: str,
    srt_path: str | None = None,
) -> None:
    """静止画と音声からMP4ファイルを生成する。"""
    artwork = Path(artwork_path)
    audio = Path(audio_path)
    output = Path(output_path)

    if not artwork.exists():
        raise FileNotFoundError(f"アートワーク画像が見つかりません: {artwork_path}")
    if not audio.exists():
        raise FileNotFoundError(f"音声ファイルが見つかりません: {audio_path}")

    output.parent.mkdir(parents=True, exist_ok=True)

    vf_parts = [
        "scale=1920:1080:force_original_aspect_ratio=decrease",
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
    ]

    if srt_path and Path(srt_path).exists():
        escaped_srt = str(srt_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        subtitle_style = (
            "FontSize=22,FontName=Hiragino Kaku Gothic ProN,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            "Outline=2,Shadow=0,MarginV=40"
        )
        vf_parts.append(f"subtitles='{escaped_srt}':force_style='{subtitle_style}'")
        logger.info("字幕焼き込み: %s", srt_path)

    vf = ",".join(vf_parts)

    cmd = [
        FFMPEG,
        "-y",
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

    logger.info("MP4生成開始: %s (ffmpeg: %s)", output_path, FFMPEG)
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error("ffmpeg stderr:\n%s", result.stderr[-2000:])
        raise subprocess.CalledProcessError(
            result.returncode, cmd,
            output=result.stdout,
            stderr=result.stderr,
        )

    size_mb = output.stat().st_size / 1024 / 1024
    logger.info("MP4生成完了: %s (%.1f MB)", output_path, size_mb)
