"""VoxCPM2 TTS 自动转语音策略判断"""

import time
import random
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .config import VoxCPM2Config

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """会话级别的 TTS 状态追踪"""
    last_spoken_text: Optional[str] = None
    last_spoken_time: float = 0.0
    last_spoken_conversation_id: Optional[str] = None
    # 待写入对话历史的语音文本（消息发送后消费）
    pending_history_text: Optional[str] = None
    pending_history_conversation_id: Optional[str] = None


class TTSPolicy:
    """自动转语音策略判断 + 会话状态管理"""

    def __init__(self, config: VoxCPM2Config):
        self.config = config
        self._last_tts_time: Dict[str, float] = {}
        self._sessions: Dict[str, SessionState] = {}

    def _get_session(self, umo: str) -> SessionState:
        """获取或创建会话状态"""
        if umo not in self._sessions:
            self._sessions[umo] = SessionState()
        return self._sessions[umo]

    # ─── 语音文本追踪 ──────────────────────────────────────────

    def set_spoken_text(self, umo: str, text: str, conversation_id: Optional[str] = None):
        """记录一次语音合成的文本内容"""
        st = self._get_session(umo)
        st.last_spoken_text = text.strip()
        st.last_spoken_time = time.time()
        st.last_spoken_conversation_id = conversation_id
        # 同时设为待写入历史
        st.pending_history_text = text.strip()
        st.pending_history_conversation_id = conversation_id

    def get_recent_spoken_text(self, umo: str) -> Optional[str]:
        """获取最近说过的语音文本"""
        st = self._get_session(umo)
        return st.last_spoken_text

    def consume_pending_history(self, umo: str) -> Tuple[Optional[str], Optional[str]]:
        """消费待写入历史的语音文本，返回 (text, conversation_id)"""
        st = self._get_session(umo)
        text = st.pending_history_text
        conv_id = st.pending_history_conversation_id
        st.pending_history_text = None
        st.pending_history_conversation_id = None
        return text, conv_id

    def should_auto_tts(
        self, umo: str, text: str, has_mixed_content: bool = False
    ) -> Tuple[bool, str]:
        """
        判断是否应该自动转语音。
        返回: (should_tts, reason)
        """
        cfg = self.config.auto_tts
        filt = self.config.text_filter

        # 1. 全局开关
        if not cfg.enable:
            return False, "auto_tts disabled"

        # 2. 概率判断
        if random.random() > cfg.probability:
            return False, f"probability miss ({cfg.probability:.0%})"

        # 3. UMO 黑白名单
        if cfg.mode == "whitelist":
            if umo not in cfg.enabled_umos:
                return False, f"umo {umo} not in whitelist"
        elif cfg.mode == "blacklist":
            if umo in cfg.disabled_umos:
                return False, f"umo {umo} in blacklist"

        # 4. 文本长度检查
        text_len = len(text.strip())
        if text_len < filt.min_length:
            return False, f"text too short ({text_len} < {filt.min_length})"
        if text_len > filt.max_length:
            return False, f"text too long ({text_len} > {filt.max_length})"

        # 5. 混合消息检查
        if has_mixed_content and not filt.allow_mixed:
            return False, "mixed content not allowed"

        # 6. 冷却时间检查
        now = time.time()
        last = self._last_tts_time.get(umo, 0)
        if now - last < filt.cooldown:
            return False, f"cooldown ({filt.cooldown}s not elapsed)"
        self._last_tts_time[umo] = now

        return True, "ok"

    def should_output_text(self, umo: str) -> bool:
        """判断是否同时输出文字"""
        cfg = self.config.text_output
        if not cfg.enable:
            return False

        if cfg.mode == "whitelist":
            return umo in cfg.enabled_umos
        elif cfg.mode == "blacklist":
            return umo not in cfg.disabled_umos
        return False

    # ─── 动态会话控制 ──────────────────────────────────────────

    def enable_umo(self, umo: str):
        """启用指定会话的自动语音"""
        cfg = self.config.auto_tts

        if cfg.mode == "whitelist":
            if umo not in cfg.enabled_umos:
                cfg.enabled_umos.append(umo)
        elif cfg.mode == "blacklist":
            if umo in cfg.disabled_umos:
                cfg.disabled_umos.remove(umo)

    def disable_umo(self, umo: str):
        """禁用指定会话的自动语音"""
        cfg = self.config.auto_tts

        if cfg.mode == "whitelist":
            if umo in cfg.enabled_umos:
                cfg.enabled_umos.remove(umo)
        elif cfg.mode == "blacklist":
            if umo not in cfg.disabled_umos:
                cfg.disabled_umos.append(umo)

    def get_umo_status(self, umo: str) -> str:
        """获取指定会话的语音策略状态描述"""
        cfg = self.config.auto_tts

        if not cfg.enable:
            return "❌ 全局已关闭"

        if cfg.mode == "whitelist":
            if umo in cfg.enabled_umos:
                return "✅ 白名单内（自动语音开启）"
            else:
                return "❌ 不在白名单内（自动语音关闭）"
        elif cfg.mode == "blacklist":
            if umo in cfg.disabled_umos:
                return "❌ 在黑名单内（自动语音关闭）"
            else:
                return "✅ 不在黑名单内（自动语音开启）"

        return "⚠️ 未知模式"
