"""話者声紋プロファイル管理

音声サンプルから声紋（speaker embedding）を抽出・キャッシュし、
エピソードの話者分離結果とマッチングして話者名を自動割り当てする。

使い方:
  1. assets/speakers/ に各話者の音声サンプル（10-30秒のWAV）を配置
     ファイル名が話者名になる（例: せんだ.wav → 「せんだ」）
  2. config.yaml で speaker_voices_dir を設定
  3. 初回実行時に自動で声紋を抽出・キャッシュ
"""

import logging
import os
from pathlib import Path

import numpy as np
import torch

from . import ROOT_DIR

logger = logging.getLogger(__name__)

# 声紋モデルのキャッシュ（バッチ処理で使い回す）
_embedding_cache: dict = {"inference": None, "token": None}

# コサイン距離の閾値（これ以上離れていたら別人とみなす）
VOICEPRINT_THRESHOLD = 0.55


def _get_embedding_inference(hf_token: str, device: str):
    """声紋抽出用の Inference を取得（キャッシュ付き）。

    pyannote/wespeaker-voxceleb-resnet34-LM を使用。
    diarization 3.1 パイプラインと同じモデルなので追加ダウンロード不要。
    """
    if _embedding_cache["inference"] is None or _embedding_cache["token"] != hf_token:
        from pyannote.audio import Inference, Model

        logger.info("声紋モデル (wespeaker-voxceleb-resnet34-LM) をロード中...")
        model = Model.from_pretrained(
            "pyannote/wespeaker-voxceleb-resnet34-LM",
            use_auth_token=hf_token,
        )
        inference = Inference(model, window="whole")
        inference.to(torch.device(device))
        _embedding_cache["inference"] = inference
        _embedding_cache["token"] = hf_token
    return _embedding_cache["inference"]


def _cosine_distance(e1: np.ndarray, e2: np.ndarray) -> float:
    """コサイン距離を計算する（0.0=同一人物, 2.0=正反対）。"""
    a, b = e1.flatten(), e2.flatten()
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm == 0:
        return 1.0
    return 1.0 - float(np.dot(a, b) / norm)


def _resolve_path(path_str: str) -> Path:
    """設定ファイルのパスを解決する（絶対パス or ROOT_DIR相対）。"""
    p = Path(path_str)
    if p.is_absolute():
        return p
    return ROOT_DIR / p


def load_voiceprints(
    voices_dir: str,
    voiceprints_dir: str,
    hf_token: str,
    device: str = "cpu",
) -> dict[str, np.ndarray]:
    """声紋プロファイルを読み込む。

    assets/speakers/ のWAVファイルから声紋を抽出し、
    config/voiceprints/ に .npy としてキャッシュする。
    WAVファイルが更新されたら自動で再抽出する。

    Returns:
        {話者名: 256次元embeddingベクトル} の辞書。WAVがなければ空辞書。
    """
    voices_path = _resolve_path(voices_dir)
    vp_path = _resolve_path(voiceprints_dir)

    if not voices_path.exists():
        return {}

    audio_exts = ("*.wav", "*.m4a", "*.mp3", "*.ogg", "*.flac")
    audio_files = sorted(
        f for ext in audio_exts for f in voices_path.glob(ext)
    )
    if not audio_files:
        return {}

    vp_path.mkdir(parents=True, exist_ok=True)

    voiceprints: dict[str, np.ndarray] = {}
    for wav_file in audio_files:
        name = wav_file.stem
        npy_file = vp_path / f"{name}.npy"

        if npy_file.exists() and npy_file.stat().st_mtime >= wav_file.stat().st_mtime:
            voiceprints[name] = np.load(str(npy_file))
            logger.info("声紋キャッシュ読み込み: %s", name)
        else:
            logger.info("声紋を抽出中: %s ← %s", name, wav_file.name)
            inference = _get_embedding_inference(hf_token, device)
            embedding = inference(str(wav_file))
            np.save(str(npy_file), embedding)
            voiceprints[name] = embedding
            logger.info("声紋を保存: %s", npy_file.name)

    logger.info("登録済み話者: %s", ", ".join(voiceprints.keys()))
    return voiceprints


def match_speakers(
    audio_path: str,
    diarization_segments: list[dict],
    voiceprints: dict[str, np.ndarray],
    unknown_label: str = "ゲスト",
    hf_token: str | None = None,
    device: str = "cpu",
    threshold: float = VOICEPRINT_THRESHOLD,
) -> dict[str, str]:
    """エピソードの話者IDを声紋マッチングで名前に変換する。

    1. 各話者のセグメント（2秒以上）からembeddingを抽出・平均
    2. 保存済みvoiceprintsとのコサイン距離を計算
    3. 距離が最小のペアから順にマッチング（greedy）
    4. 閾値を超えた話者は unknown_label を割り当て

    Args:
        audio_path: エピソード音声（WAV推奨）
        diarization_segments: pyannote出力 [{"start", "end", "speaker"}, ...]
        voiceprints: {話者名: embedding} の辞書
        unknown_label: マッチしなかった話者のラベル
        hf_token: HuggingFace token
        device: "cpu" or "cuda"
        threshold: マッチング閾値（コサイン距離）

    Returns:
        {SPEAKER_00: "せんだ", SPEAKER_01: "かねとも", SPEAKER_02: "ゲスト"} 等
    """
    from pyannote.core import Segment

    if not hf_token:
        hf_token = os.environ.get("HF_TOKEN", "")

    unique_speakers = sorted(set(seg["speaker"] for seg in diarization_segments))
    inference = _get_embedding_inference(hf_token, device)

    # --- 各話者の代表embeddingを計算 ---
    speaker_embeddings: dict[str, np.ndarray] = {}
    for speaker_id in unique_speakers:
        embeddings = []
        for seg in diarization_segments:
            if seg["speaker"] != speaker_id:
                continue
            duration = seg["end"] - seg["start"]
            if duration < 2.0:
                continue
            excerpt = Segment(seg["start"], seg["end"])
            try:
                emb = inference.crop(audio_path, excerpt)
                embeddings.append(emb)
            except Exception as e:
                logger.debug("セグメント embedding 抽出失敗: %s", e)

        if embeddings:
            speaker_embeddings[speaker_id] = np.mean(embeddings, axis=0)
        else:
            logger.warning("話者 %s の有効セグメントなし（2秒以上のセグメントがない）", speaker_id)

    if not speaker_embeddings:
        return {}

    # --- 距離行列を計算 ---
    ep_ids = list(speaker_embeddings.keys())
    vp_names = list(voiceprints.keys())

    distances = np.zeros((len(ep_ids), len(vp_names)))
    for i, ep_id in enumerate(ep_ids):
        for j, vp_name in enumerate(vp_names):
            distances[i, j] = _cosine_distance(
                speaker_embeddings[ep_id], voiceprints[vp_name]
            )

    # ログ出力
    for i, ep_id in enumerate(ep_ids):
        pairs = ", ".join(f"{vp_names[j]}={distances[i, j]:.4f}" for j in range(len(vp_names)))
        logger.info("声紋距離: %s → %s", ep_id, pairs)

    # --- Greedy matching: 距離が最小のペアから順に割り当て ---
    mapping: dict[str, str] = {}
    used_ep: set[int] = set()
    used_vp: set[int] = set()

    while True:
        best_dist = float("inf")
        best_i, best_j = -1, -1
        for i in range(len(ep_ids)):
            if i in used_ep:
                continue
            for j in range(len(vp_names)):
                if j in used_vp:
                    continue
                if distances[i, j] < best_dist:
                    best_dist = distances[i, j]
                    best_i, best_j = i, j

        if best_i < 0 or best_dist > threshold:
            break

        mapping[ep_ids[best_i]] = vp_names[best_j]
        used_ep.add(best_i)
        used_vp.add(best_j)
        logger.info("声紋マッチ: %s → %s (距離: %.4f)", ep_ids[best_i], vp_names[best_j], best_dist)

    # --- マッチしなかった話者に unknown_label を割り当て ---
    guest_count = 0
    for i, ep_id in enumerate(ep_ids):
        if i not in used_ep:
            guest_count += 1
            label = unknown_label if guest_count == 1 else f"{unknown_label}{guest_count}"
            mapping[ep_id] = label
            logger.info("未知話者: %s → %s (最小距離: %.4f)",
                        ep_id, label, min(distances[i, :]) if len(vp_names) > 0 else float("inf"))

    logger.info("声紋マッピング結果: %s", mapping)
    return mapping
