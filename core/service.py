"""VoxCPM2 TTS HTTP 调用逻辑"""

import logging
import re
from typing import Optional

import httpx

from .config import VoxCPM2Config

logger = logging.getLogger(__name__)

# 同时使用 AstrBot 的日志系统确保输出可见
try:
    from astrbot.api import logger as astrbot_logger
except ImportError:
    astrbot_logger = None


def _log(msg: str):
    """同时输出到标准 logger 和 AstrBot logger"""
    logger.info(msg)
    if astrbot_logger:
        astrbot_logger.info(msg)


class VoxCPM2TTSService:
    """VoxCPM2 TTS HTTP 客户端"""

    # 匹配 <tts>...</tts> 标签（含换行）
    _TTS_TAG_RE = re.compile(r'<tts>(.*?)</tts>', re.DOTALL)

    def __init__(self, config: VoxCPM2Config):
        self.config = config

    @classmethod
    def parse_tts_segments(cls, text: str) -> list:
        """
        解析文本中的 <tts>...</tts> 标签，返回分段列表。
        
        返回格式: [{"text": "段落文本", "is_tts": True/False}, ...]
        - is_tts=True: 在 <tts> 标签内，需要合成语音
        - is_tts=False: 在标签外，不需要合成语音
        
        示例:
            "前面文字<tts>(warm)你好</tts>中间文字<tts>再见</tts>后面文字"
            → [{"text": "前面文字", "is_tts": False},
               {"text": "(warm)你好", "is_tts": True},
               {"text": "中间文字", "is_tts": False},
               {"text": "再见", "is_tts": True},
               {"text": "后面文字", "is_tts": False}]
        """
        segments = []
        last_end = 0

        for match in cls._TTS_TAG_RE.finditer(text):
            # 标签前的非 TTS 文本
            before = text[last_end:match.start()].strip()
            if before:
                segments.append({"text": before, "is_tts": False})

            # 标签内的 TTS 文本
            inner = match.group(1).strip()
            if inner:
                segments.append({"text": inner, "is_tts": True})

            last_end = match.end()

        # 剩余的非 TTS 文本
        remaining = text[last_end:].strip()
        if remaining:
            segments.append({"text": remaining, "is_tts": False})

        return segments

    @classmethod
    def has_tts_tags(cls, text: str) -> bool:
        """检查文本是否包含 <tts> 标签"""
        return bool(cls._TTS_TAG_RE.search(text))

    @staticmethod
    def _strip_llm_brackets(text: str) -> str:
        """清除 LLM 在文本开头注入的括号指令（如语速/风格），由插件 voice_style 统一控制"""
        return re.sub(r'^\([^)]*\)\s*', '', text)

    def _build_input_text(self, text: str) -> str:
        """构建最终输入文本：先剥离 <tts> 标签，再处理 voice_style"""
        # 1. 剥离 <tts>/</tts> 标签，清理多余换行
        text = self._TTS_TAG_RE.sub('', text)
        text = re.sub(r'\n+', ' ', text).strip()

        # 2. voice_style 有值时清除 LLM 括号指令并拼接用户风格，留空时保留 LLM 括号指令让其自由控制
        style = self.config.voice.voice_style
        if style:
            # 用户配置了 voice_style → 清除 LLM 括号指令，以用户配置为准
            clean_text = self._strip_llm_brackets(text)
            return f"({style}){clean_text}"
        else:
            # voice_style 留空 → 保留 LLM 的括号指令（如 Control_Instruction），让 LLM 自由控制风格
            return text

    def _determine_mode(self) -> str:
        """根据参数组合确定合成模式"""
        has_ref = bool(self.config.voice.reference_wav_path)
        has_prompt = bool(self.config.voice.prompt_text)
        has_lora = bool(self.config.lora.lora_path)

        if has_lora and has_ref and has_prompt:
            return "lora_clone"
        elif has_lora and has_ref:
            return "lora_clone"
        elif has_lora:
            return "lora"
        elif has_ref and has_prompt:
            return "ultimate_clone"
        elif has_ref:
            return "voice_clone"
        elif self.config.voice.voice_style:
            return "voice_design"
        else:
            return "base"

    def _build_payload(self, text: str) -> dict:
        """根据参数组合构建 HTTP 请求 payload"""
        mode = self._determine_mode()
        gen = self.config.generation
        voice = self.config.voice

        # 1. 构建输入文本（清除 LLM 括号指令，拼接 voice_style）
        input_text = self._build_input_text(text)

        # 2. 构建 voice 字段
        voice_map = {
            "lora": "lora",
            "lora_clone": "lora_clone",
            "voice_clone": "clone",
            "ultimate_clone": "ultimate",
            "voice_design": "neutral",
            "base": "neutral",
        }

        payload = {
            "model": "voxcpm2",
            "input": input_text,
            "voice": voice_map.get(mode, "neutral"),
            "response_format": gen.output_format,
            "cfg_value": gen.cfg_value,
            "inference_timesteps": gen.inference_timesteps,
        }

        # 3. 根据模式添加额外参数
        if mode in ("voice_clone", "ultimate_clone", "lora_clone"):
            payload["reference_wav_path"] = voice.reference_wav_path

        if mode in ("ultimate_clone", "lora_clone"):
            payload["prompt_text"] = voice.prompt_text

        if mode in ("lora", "lora_clone"):
            payload["lora_path"] = self.config.lora.lora_path
            payload["lora_alpha"] = self.config.lora.lora_alpha

        return payload

    def _config_snapshot(self) -> dict:
        """生成配置快照（用于缓存 key 计算）"""
        return {
            "style": self.config.voice.voice_style,
            "ref": self.config.voice.reference_wav_path,
            "prompt": self.config.voice.prompt_text,
            "lora": self.config.lora.lora_path,
            "lora_alpha": self.config.lora.lora_alpha,
            "cfg": self.config.generation.cfg_value,
            "steps": self.config.generation.inference_timesteps,
            "fmt": self.config.generation.output_format,
        }

    async def synthesize(self, text: str) -> bytes:
        """执行 TTS 合成，返回音频字节"""
        payload = self._build_payload(text)

        logger.info(
            f"[VoxCPM2 TTS] 请求: mode={self._determine_mode()}, "
            f"text='{text[:50]}', cfg={self.config.generation.cfg_value}"
        )
        logger.info(f"[VoxCPM2 TTS] payload: {payload}")

        async with httpx.AsyncClient(timeout=self.config.server.timeout) as client:
            try:
                response = await client.post(
                    f"{self.config.server.url}/v1/audio/speech",
                    headers={
                        "Authorization": f"Bearer {self.config.server.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                return response.content
            except httpx.TimeoutException:
                logger.error(f"TTS 请求超时 ({self.config.server.timeout}s)")
                raise
            except httpx.HTTPStatusError as e:
                logger.error(f"TTS HTTP 错误: {e.response.status_code} {e.response.text[:200]}")
                raise
            except httpx.ConnectError:
                logger.error(f"无法连接 VoxCPM2 Server: {self.config.server.url}")
                raise

    async def reset_lora(self) -> dict:
        """卸载 Server 当前加载的 LoRA，恢复基础模型"""
        async with httpx.AsyncClient(timeout=self.config.server.timeout) as client:
            try:
                response = await client.post(
                    f"{self.config.server.url}/v1/lora/reset",
                    headers={"Authorization": f"Bearer {self.config.server.api_key}"},
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"[VoxCPM2 TTS] LoRA 已卸载: {result}")
                return result
            except Exception as e:
                logger.error(f"LoRA 卸载失败: {e}")
                raise

    async def load_lora(self, lora_path: str, lora_alpha: float = 32.0) -> dict:
        """加载指定 LoRA 权重到 Server"""
        async with httpx.AsyncClient(timeout=self.config.server.timeout) as client:
            try:
                response = await client.post(
                    f"{self.config.server.url}/v1/lora/load",
                    headers={
                        "Authorization": f"Bearer {self.config.server.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"lora_path": lora_path, "lora_alpha": lora_alpha},
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"[VoxCPM2 TTS] LoRA 已加载: {lora_path}, alpha={lora_alpha}")
                return result
            except Exception as e:
                logger.error(f"LoRA 加载失败: {e}")
                raise
