"""VoxCPM2 TTS 插件配置类"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ServerConfig:
    url: str = "http://172.22.11.55:8000"
    api_key: str = "voxcpm2"
    timeout: int = 120


@dataclass
class VoiceConfig:
    voice_style: str = ""
    reference_wav_path: str = ""
    prompt_text: str = ""


@dataclass
class LoRAConfig:
    lora_path: str = ""
    lora_alpha: float = 32


@dataclass
class GenerationConfig:
    cfg_value: float = 2.0
    inference_timesteps: int = 10
    output_format: str = "mp3"
    sample_rate: int = 48000


@dataclass
class AutoTTSConfig:
    enable: bool = True
    probability: float = 1.0
    mode: str = "blacklist"
    enabled_umos: List[str] = field(default_factory=list)
    disabled_umos: List[str] = field(default_factory=list)


@dataclass
class TextOutputConfig:
    enable: bool = False
    mode: str = "whitelist"
    enabled_umos: List[str] = field(default_factory=list)
    disabled_umos: List[str] = field(default_factory=list)


@dataclass
class TextFilterConfig:
    max_length: int = 500
    min_length: int = 2
    allow_mixed: bool = False
    cooldown: int = 3


@dataclass
class CacheConfig:
    enabled: bool = True
    expire_hours: int = 0
    path: str = ""


@dataclass
class VoxCPM2Config:
    enabled: bool = True
    server: ServerConfig = field(default_factory=ServerConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    lora: LoRAConfig = field(default_factory=LoRAConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    auto_tts: AutoTTSConfig = field(default_factory=AutoTTSConfig)
    text_output: TextOutputConfig = field(default_factory=TextOutputConfig)
    text_filter: TextFilterConfig = field(default_factory=TextFilterConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "VoxCPM2Config":
        """从插件配置字典构建配置对象"""
        cfg = cls()
        if not d:
            return cfg

        cfg.enabled = d.get("enabled", True)

        if "server" in d:
            s = d["server"]
            cfg.server = ServerConfig(
                url=s.get("url", "http://172.22.11.55:8000"),
                api_key=s.get("api_key", "voxcpm2"),
                timeout=s.get("timeout", 120),
            )

        if "voice" in d:
            v = d["voice"]
            cfg.voice = VoiceConfig(
                voice_style=v.get("voice_style", ""),
                reference_wav_path=v.get("reference_wav_path", ""),
                prompt_text=v.get("prompt_text", ""),
            )

        if "lora" in d:
            l = d["lora"]
            cfg.lora = LoRAConfig(
                lora_path=l.get("lora_path", ""),
                lora_alpha=l.get("lora_alpha", 32),
            )

        if "generation" in d:
            g = d["generation"]
            cfg.generation = GenerationConfig(
                cfg_value=g.get("cfg_value", 2.0),
                inference_timesteps=g.get("inference_timesteps", 10),
                output_format=g.get("output_format", "mp3"),
                sample_rate=g.get("sample_rate", 48000),
            )

        if "auto_tts" in d:
            a = d["auto_tts"]
            cfg.auto_tts = AutoTTSConfig(
                enable=a.get("enable", True),
                probability=a.get("probability", 1.0),
                mode=a.get("mode", "blacklist"),
                enabled_umos=a.get("enabled_umos", []),
                disabled_umos=a.get("disabled_umos", []),
            )

        if "text_output" in d:
            t = d["text_output"]
            cfg.text_output = TextOutputConfig(
                enable=t.get("enable", False),
                mode=t.get("mode", "whitelist"),
                enabled_umos=t.get("enabled_umos", []),
                disabled_umos=t.get("disabled_umos", []),
            )

        if "text_filter" in d:
            f = d["text_filter"]
            cfg.text_filter = TextFilterConfig(
                max_length=f.get("max_length", 500),
                min_length=f.get("min_length", 2),
                allow_mixed=f.get("allow_mixed", False),
                cooldown=f.get("cooldown", 3),
            )

        if "cache" in d:
            c = d["cache"]
            cfg.cache = CacheConfig(
                enabled=c.get("enabled", True),
                expire_hours=c.get("expire_hours", 0),
                path=c.get("path", ""),
            )

        return cfg
