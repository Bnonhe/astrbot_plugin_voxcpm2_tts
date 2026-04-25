"""VoxCPM2 TTS 缓存管理"""

import os
import hashlib
import json
import time
import logging
from typing import Optional

from .config import VoxCPM2Config

logger = logging.getLogger(__name__)


class TTSCache:
    """语音缓存管理"""

    def __init__(self, config: VoxCPM2Config):
        self.config = config
        self._cache_dir = self._resolve_cache_dir()

    def _resolve_cache_dir(self) -> str:
        """解析缓存目录路径"""
        path = self.config.cache.path
        if not path:
            path = "data/plugins_data/astrbot_plugin_voxcpm2_tts/audio"
        os.makedirs(path, exist_ok=True)
        return path

    def _make_key(self, text: str, mode: str, config_snapshot: dict) -> Optional[str]:
        """
        生成缓存 key。
        LoRA 模式下返回 None（不使用缓存）。
        """
        if mode.startswith("lora"):
            return None

        key_data = {
            "text": text,
            "mode": mode,
            **config_snapshot,
        }
        raw = json.dumps(key_data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _key_to_path(self, key: str, fmt: str) -> str:
        """缓存 key → 文件路径"""
        ext = "mp3" if fmt == "mp3" else "wav"
        return os.path.join(self._cache_dir, f"{key}.{ext}")

    def _is_expired(self, filepath: str) -> bool:
        """检查缓存是否过期"""
        expire = self.config.cache.expire_hours
        if expire <= 0:
            return False
        mtime = os.path.getmtime(filepath)
        return (time.time() - mtime) > (expire * 3600)

    def get(self, text: str, mode: str, config_snapshot: dict, fmt: str) -> Optional[bytes]:
        """
        从缓存获取音频数据。
        返回 None 表示未命中。
        """
        if not self.config.cache.enabled:
            return None

        key = self._make_key(text, mode, config_snapshot)
        if key is None:
            return None

        filepath = self._key_to_path(key, fmt)
        if not os.path.exists(filepath):
            return None

        if self._is_expired(filepath):
            try:
                os.unlink(filepath)
            except Exception:
                pass
            return None

        try:
            with open(filepath, "rb") as f:
                data = f.read()
            logger.debug(f"缓存命中: {key}")
            return data
        except Exception as e:
            logger.warning(f"缓存读取失败: {e}")
            return None

    def set(self, text: str, mode: str, config_snapshot: dict, fmt: str, data: bytes) -> None:
        """将音频数据写入缓存"""
        if not self.config.cache.enabled:
            return

        key = self._make_key(text, mode, config_snapshot)
        if key is None:
            return

        filepath = self._key_to_path(key, fmt)
        try:
            with open(filepath, "wb") as f:
                f.write(data)
            logger.debug(f"缓存写入: {key}")
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")
