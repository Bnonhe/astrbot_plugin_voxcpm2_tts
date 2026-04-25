"""AstrBot VoxCPM2 TTS 插件入口"""

import json
import os
import time
import uuid
import logging
import httpx
from typing import Any, Optional, List

from astrbot.api.star import Context, Star, register
from astrbot.api import logger as astrbot_logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, Record

from .core import VoxCPM2Config, VoxCPM2TTSService, TTSPolicy, TTSCache

logger = logging.getLogger(__name__)

# 语音上下文注入 TTL（秒）：超过此时间的语音文本不再注入到 LLM 请求
RECENT_SPOKEN_CONTEXT_TTL_SECONDS = 300


@register("voxcpm2_tts", "VoxCPM2 TTS", "VoxCPM2 语音合成插件，支持 Voice Design / Voice Clone / Ultimate Clone / LoRA 微调", "1.0.0")
class VoxCPM2TTSPlugin(Star):
    """VoxCPM2 TTS 插件"""

    def __init__(self, context: Context, config: Optional[dict] = None):
        super().__init__(context)
        self.config = VoxCPM2Config.from_dict(config or {})
        self.service = VoxCPM2TTSService(self.config)
        self.policy = TTSPolicy(self.config)
        self.cache = TTSCache(self.config)

    async def initialize(self):
        """插件启用时初始化：同步 Server 的 LoRA 状态与插件配置一致"""
        logger.info(
            f"[VoxCPM2 TTS] 初始化中: "
            f"server={self.config.server.url}, "
            f"mode={self.service._determine_mode()}"
        )

        # 启动时同步 LoRA 状态
        # - 配置了 lora_path → 加载该 LoRA
        # - 未配置 lora_path → 卸载（确保配置留空时 Server 不残留旧 LoRA）
        try:
            if self.config.lora.lora_path:
                await self.service.load_lora(
                    self.config.lora.lora_path,
                    self.config.lora.lora_alpha,
                )
                logger.info(f"[VoxCPM2 TTS] 启动时已加载 LoRA: {self.config.lora.lora_path}")
            else:
                await self.service.reset_lora()
                logger.info("[VoxCPM2 TTS] 启动时已卸载 LoRA（配置为空）")
        except Exception as e:
            logger.warning(f"[VoxCPM2 TTS] LoRA 同步失败（不影响 TTS 功能）: {e}")

        logger.info(
            f"[VoxCPM2 TTS] 初始化完成: "
            f"server={self.config.server.url}, "
            f"mode={self.service._determine_mode()}"
        )

    async def terminate(self):
        """插件销毁时清理"""
        logger.info("[VoxCPM2 TTS] 插件已卸载")

    # ─── 自动 TTS Hook ──────────────────────────────────────────

    @filter.on_decorating_result(priority=14)
    async def on_decorating_result(self, event: AstrMessageEvent):
        """
        拦截 LLM 回复，自动触发 TTS。
        支持两种模式：
        1. 含 <tts>...</tts> 标签：每段独立合成语音
        2. 无标签：整条文本判断是否自动 TTS
        """
        # 1. 检查插件是否启用
        if not self.config.enabled:
            return

        # 2. 获取结果链
        result = event.get_result()
        if result is None:
            return
        chain = result.chain
        if not chain:
            return

        # 3. 收集所有 Plain 文本
        plain_texts = [seg for seg in chain if isinstance(seg, Plain)]
        if not plain_texts:
            return

        # 4. 合并文本
        text = "".join(seg.text for seg in plain_texts).strip()
        if not text:
            return

        # 5. 获取会话 UMO
        umo = self._get_umo(event)

        # 6. 检查是否包含 <tts> 标签
        if VoxCPM2TTSService.has_tts_tags(text):
            await self._handle_tts_tagged(chain, text, umo)
        else:
            await self._handle_auto_tts(chain, text, umo)

    async def _handle_tts_tagged(self, chain: list, text: str, umo: str):
        """处理含 <tts> 标签的消息：每段标签内文本独立合成语音"""
        segments = VoxCPM2TTSService.parse_tts_segments(text)
        tts_segments = [s for s in segments if s["is_tts"]]

        if not tts_segments:
            return

        # 收集所有 TTS 音频和对应文本
        audio_records = []
        combined_text_parts = []
        failed_texts = []  # 合成失败/跳过的段落，作为纯文本回退

        for seg in tts_segments:
            seg_text = seg["text"]
            if not seg_text.strip():
                continue

            # 检查单段长度限制
            if len(seg_text) > self.config.text_filter.max_length:
                logger.warning(f"[VoxCPM2 TTS] 单段超长 ({len(seg_text)}>{self.config.text_filter.max_length})，跳过: {seg_text[:50]}...")
                failed_texts.append(seg_text)
                combined_text_parts.append(seg_text)
                continue

            # 尝试缓存
            mode = self.service._determine_mode()
            config_snapshot = self.service._config_snapshot()
            fmt = self.config.generation.output_format

            cached = self.cache.get(seg_text, mode, config_snapshot, fmt)
            if cached is not None:
                logger.info(f"[VoxCPM2 TTS] 缓存命中 (tts段)")
                audio_path = self._save_audio(cached, fmt)
                audio_records.append(Record(file=audio_path, url=audio_path))
                combined_text_parts.append(seg_text)
                continue

            # 合成语音
            try:
                audio_bytes = await self.service.synthesize(seg_text)
                self.cache.set(seg_text, mode, config_snapshot, fmt, audio_bytes)
                audio_path = self._save_audio(audio_bytes, fmt)
                audio_records.append(Record(file=audio_path, url=audio_path))
                combined_text_parts.append(seg_text)
            except Exception as e:
                logger.error(f"[VoxCPM2 TTS] 段落合成失败: {e}")
                failed_texts.append(seg_text)
                combined_text_parts.append(seg_text)

        if not audio_records and not combined_text_parts:
            return

        # 重建消息链
        chain.clear()

        # 如果配置了文字+语音同时输出，插入去掉 <tts> 标签后的纯文本
        if self.policy.should_output_text(umo):
            clean_text = VoxCPM2TTSService._TTS_TAG_RE.sub('', text).strip()
            if clean_text:
                chain.append(Plain(clean_text))

        # 插入所有语音
        for record in audio_records:
            chain.append(record)

        # 合成失败/跳过的段落作为纯文本回退（确保用户能看到）
        if failed_texts and not self.policy.should_output_text(umo):
            for ft in failed_texts:
                chain.append(Plain(ft))

        # 记录语音文本到会话状态
        if combined_text_parts:
            combined = "\n".join(combined_text_parts)
            self.policy.set_spoken_text(umo, combined)

    async def _handle_auto_tts(self, chain: list, text: str, umo: str):
        """处理无 <tts> 标签的消息：走原有的自动 TTS 逻辑"""
        # 检查是否混合消息（含图片/文件等）
        has_mixed = any(not isinstance(seg, Plain) for seg in chain)

        # 策略判断
        should_tts, reason = self.policy.should_auto_tts(umo, text, has_mixed)
        if not should_tts:
            logger.debug(f"[VoxCPM2 TTS] 跳过: {reason}")
            return

        # 尝试从缓存获取
        mode = self.service._determine_mode()
        config_snapshot = self.service._config_snapshot()
        fmt = self.config.generation.output_format

        cached = self.cache.get(text, mode, config_snapshot, fmt)
        if cached is not None:
            logger.info(f"[VoxCPM2 TTS] 缓存命中")
            audio_path = self._save_audio(cached, fmt)
            self._replace_chain(chain, audio_path, text, umo)
            return

        # 调用 TTS 合成
        try:
            audio_bytes = await self.service.synthesize(text)
        except Exception as e:
            logger.error(f"[VoxCPM2 TTS] 合成失败: {e}")
            return

        # 写入缓存
        self.cache.set(text, mode, config_snapshot, fmt, audio_bytes)

        # 保存音频文件并替换消息链
        audio_path = self._save_audio(audio_bytes, fmt)
        self._replace_chain(chain, audio_path, text, umo)

    # ─── LLM 上下文注入 Hook ──────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, request: Any):
        """
        在 LLM 请求时注入最近语音上下文。
        让 LLM 知道自己之前用语音说了什么，保持对话连贯。
        """
        await self._inject_recent_spoken_context(event, request)

    # ─── 消息发送后 Hook ──────────────────────────────────────────

    if hasattr(filter, "after_message_sent"):

        @filter.after_message_sent(priority=-1000)
        async def after_message_sent(self, event: AstrMessageEvent):
            """
            消息发送后，将语音文本写入对话历史。
            确保消息真正发出去后才持久化，避免发送失败但历史已记。
            """
            try:
                result = event.get_result()
                if not result:
                    return

                chain = getattr(result, "chain", None) or []
                if not any(isinstance(c, Record) for c in chain):
                    return

                umo = self._get_umo(event)
                text, conv_id = self.policy.consume_pending_history(umo)
                if text:
                    await self._append_assistant_text_to_history(
                        event, text, conversation_id=conv_id
                    )
            except Exception as e:
                logger.error(f"[VoxCPM2 TTS] after_message_sent error: {e}")

    # ─── 手动合成命令 ──────────────────────────────────────────

    @filter.command("tts_say", alias={"说"})
    async def on_command(self, event: AstrMessageEvent):
        """手动触发 TTS：tts_say <内容>（不填则使用默认测试语句）"""
        text = event.message_str.strip()
        if not text:
            text = "你好，我是 VoxCPM2 语音合成助手。"

        try:
            audio_bytes = await self.service.synthesize(text)
        except Exception as e:
            logger.error(f"[VoxCPM2 TTS] 合成失败: {e}")
            yield event.plain_result(f"语音合成失败: {e}")
            return

        audio_path = self._save_audio(audio_bytes, self.config.generation.output_format)

        # 记录语音文本到会话状态
        umo = self._get_umo(event)
        self.policy.set_spoken_text(umo, text)

        yield event.chain_result([Record(file=audio_path, url=audio_path)])

    # ─── 会话控制命令 ──────────────────────────────────────────

    @filter.command("tts_on")
    async def tts_on(self, event: AstrMessageEvent):
        """开启当前会话的自动语音输出"""
        umo = self._get_umo(event)
        self.policy.enable_umo(umo)
        yield event.plain_result(f"✅ 已开启当前会话的自动语音 (UMO: {umo})")

    @filter.command("tts_off")
    async def tts_off(self, event: AstrMessageEvent):
        """关闭当前会话的自动语音输出"""
        umo = self._get_umo(event)
        self.policy.disable_umo(umo)
        yield event.plain_result(f"❌ 已关闭当前会话的自动语音 (UMO: {umo})")

    @filter.command("tts_all_on")
    async def tts_all_on(self, event: AstrMessageEvent):
        """开启全局自动语音输出"""
        self.config.auto_tts.enable = True
        yield event.plain_result("✅ 已开启全局自动语音输出")

    @filter.command("tts_all_off")
    async def tts_all_off(self, event: AstrMessageEvent):
        """关闭全局自动语音输出（保留手动/LLM工具触发）"""
        self.config.auto_tts.enable = False
        yield event.plain_result("❌ 已关闭全局自动语音输出（手动\"说\"和LLM工具仍可用）")

    # ─── 状态查询命令 ──────────────────────────────────────────

    @filter.command("tts_status")
    async def tts_status(self, event: AstrMessageEvent):
        """查看当前 TTS 状态"""
        umo = self._get_umo(event)
        mode = self.service._determine_mode()
        auto = self.config.auto_tts

        # 检查当前会话是否在名单内
        umo_status = self.policy.get_umo_status(umo)

        # 模式中文映射
        mode_names = {
            "base": "基础模型（随机音色）",
            "voice_design": "Voice Design（文字指令生成）",
            "voice_clone": "Voice Clone（参考音频克隆）",
            "ultimate_clone": "Ultimate Clone（高保真克隆）",
            "lora": "LoRA Fine-tune（LoRA 音色）",
            "lora_clone": "LoRA + Clone（LoRA + 克隆叠加）",
        }

        status_text = (
            f"🎤 VoxCPM2 TTS 状态\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"合成模式: {mode_names.get(mode, mode)}\n"
            f"全局自动: {'✅ 开启' if auto.enable else '❌ 关闭'}\n"
            f"自动概率: {auto.probability:.0%}\n"
            f"名单模式: {auto.mode}\n"
            f"当前会话: {umo_status}\n"
            f"UMO: {umo}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"服务地址: {self.config.server.url}\n"
            f"输出格式: {self.config.generation.output_format}\n"
            f"CFG 强度: {self.config.generation.cfg_value}\n"
            f"参考音频: {'✅' if self.config.voice.reference_wav_path else '❌'}\n"
            f"LoRA: {'✅ ' + self.config.lora.lora_path if self.config.lora.lora_path else '❌'}\n"
            f"缓存: {'✅' if self.config.cache.enabled else '❌'}"
        )
        yield event.plain_result(status_text)

    @filter.command("sid")
    async def get_sid(self, event: AstrMessageEvent):
        """获取当前会话 UMO（用于配置黑白名单）"""
        umo = self._get_umo(event)
        yield event.plain_result(f"当前会话 UMO: {umo}")

    @filter.command("tts_reset_lora")
    async def tts_reset_lora(self, event: AstrMessageEvent):
        """卸载 VoxCPM2 Server 当前加载的 LoRA，恢复基础模型"""
        try:
            result = await self.service.reset_lora()
            yield event.plain_result(f"✅ LoRA 已卸载，Server 恢复基础模型")
        except Exception as e:
            yield event.plain_result(f"❌ LoRA 卸载失败: {e}")

    @filter.command("tts_load_lora")
    async def tts_load_lora(self, event: AstrMessageEvent):
        """加载指定 LoRA：tts_load_lora <路径> [alpha]
        
        示例：tts_load_lora /mnt/e/WSL/voxcpm2_lora/checkpoints/latest 20
        不填参数则使用配置文件中的 lora_path
        """
        parts = event.message_str.strip().split()
        if not parts:
            # 无参数：使用配置文件中的 lora_path
            lora_path = self.config.lora.lora_path
            lora_alpha = self.config.lora.lora_alpha
        elif len(parts) == 1:
            lora_path = parts[0]
            lora_alpha = self.config.lora.lora_alpha
        else:
            lora_path = parts[0]
            try:
                lora_alpha = float(parts[1])
            except ValueError:
                yield event.plain_result("❌ alpha 参数必须是数字")
                return

        if not lora_path:
            yield event.plain_result("❌ 未配置 lora_path，请传入路径或先在插件配置中设置")
            return

        try:
            await self.service.load_lora(lora_path, lora_alpha)
            yield event.plain_result(f"✅ LoRA 已加载: {lora_path} (alpha={lora_alpha})")
        except Exception as e:
            yield event.plain_result(f"❌ LoRA 加载失败: {e}")

    @filter.command("tts_lora_status")
    async def tts_lora_status(self, event: AstrMessageEvent):
        """查看 VoxCPM2 Server 当前 LoRA 状态"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.config.server.url}/health",
                    headers={"Authorization": f"Bearer {self.config.server.api_key}"},
                )
                data = response.json()
                lora_loaded = data.get("lora_loaded", False)
                lora_path = data.get("lora_path")
                lora_alpha = data.get("lora_alpha")

                status = "✅ 已加载" if lora_loaded else "❌ 未加载"
                info = f"路径: {lora_path}" if lora_path else ""
                alpha_info = f", alpha={lora_alpha}" if lora_alpha else ""

                yield event.plain_result(f"🎤 Server LoRA 状态\n━━━━━━━━━━━━━━━━━━\n{status} {info}{alpha_info}")
        except Exception as e:
            yield event.plain_result(f"❌ 无法获取 Server 状态: {e}")

    # ─── LLM 工具调用 ──────────────────────────────────────────

    @filter.llm_tool()
    async def voxcpm2_tts(self, event: AstrMessageEvent, message: str = ""):
        """当需要用语音回复用户时调用此工具，将文本转为语音发送。

        Args:
            message(string): 要转换为语音的文本内容，支持 <tts>...</tts> 标签
        """
        if not message.strip():
            return

        # 预处理：剥离 <tts>/</tts> 标签，合并多段内容
        import re as _re
        cleaned = _re.sub(r'</?tts[^>]*>', '', message).strip()
        # 清理多余空白（多段拼接时可能有换行）
        cleaned = _re.sub(r'\n+', ' ', cleaned).strip()

        if not cleaned:
            return

        try:
            audio_bytes = await self.service.synthesize(cleaned)
        except Exception as e:
            logger.error(f"[VoxCPM2 TTS] LLM 工具调用合成失败: {e}")
            yield event.plain_result(cleaned)
            return

        audio_path = self._save_audio(audio_bytes, self.config.generation.output_format)

        # 记录语音文本到会话状态
        umo = self._get_umo(event)
        self.policy.set_spoken_text(umo, cleaned)

        yield event.chain_result([Record(file=audio_path, url=audio_path)])

    # ─── 辅助方法 ──────────────────────────────────────────────

    @staticmethod
    def _get_umo(event: AstrMessageEvent) -> str:
        """获取会话唯一标识（UMO）"""
        try:
            session = event.session
            return getattr(session, "session_id", str(session))
        except Exception:
            return "unknown"

    def _save_audio(self, audio_bytes: bytes, fmt: str) -> str:
        """将音频字节保存到文件，返回文件路径"""
        cache_dir = self.cache._cache_dir
        ext = "mp3" if fmt == "mp3" else "wav"
        filename = f"tts_{uuid.uuid4().hex[:12]}.{ext}"
        filepath = os.path.join(cache_dir, filename)
        with open(filepath, "wb") as f:
            f.write(audio_bytes)
        return filepath

    def _replace_chain(self, chain: list, audio_path: str, text: str, umo: str):
        """替换消息链：清空原链，插入语音（可选+文字），并记录语音文本到会话状态"""
        chain.clear()
        chain.append(Record(file=audio_path, url=audio_path))

        # 如果配置了文字+语音同时输出
        if self.policy.should_output_text(umo):
            chain.insert(0, Plain(text))

        # 记录语音文本到会话状态，供后续上下文注入和历史持久化使用
        self.policy.set_spoken_text(umo, text)

    # ─── LLM 上下文注入 ──────────────────────────────────────────

    async def _inject_recent_spoken_context(
        self, event: AstrMessageEvent, request: Any
    ) -> None:
        """
        将最近语音文本注入 LLM 请求的 contexts 中。
        这样 LLM 在下一轮对话时能感知自己之前用语音说了什么。
        """
        umo = self._get_umo(event)
        logger.info(f"[VoxCPM2 TTS] on_llm_request triggered, umo={umo}")
        spoken_text = self.policy.get_recent_spoken_text(umo)
        logger.info(f"[VoxCPM2 TTS] spoken_text={spoken_text!r}, sessions={list(self.policy._sessions.keys())}")
        if not spoken_text:
            return

        # 检查 TTL：超过 5 分钟的语音文本不再注入
        st = self.policy._get_session(umo)
        if time.time() - st.last_spoken_time > RECENT_SPOKEN_CONTEXT_TTL_SECONDS:
            return

        # 注入到 request.contexts
        contexts = getattr(request, "contexts", None)
        if contexts is None:
            contexts = []
        elif isinstance(contexts, str):
            try:
                contexts = json.loads(contexts)
            except Exception:
                contexts = []

        if not isinstance(contexts, list):
            return

        # 防重复：如果 contexts 中已有相同文本，不重复注入
        if self._contexts_have_assistant_text(contexts, spoken_text):
            return

        # 注入 assistant 消息，_no_save=True 防止被持久化（历史由 after_message_sent 写入）
        contexts.append({"role": "assistant", "content": spoken_text, "_no_save": True})
        request.contexts = contexts
        logger.info(
            f"[VoxCPM2 TTS] 注入语音上下文 umo={umo} text={spoken_text[:80]}"
        )

    @staticmethod
    def _contexts_have_assistant_text(contexts: list, text: str) -> bool:
        """检查 contexts 中是否已包含相同的 assistant 文本"""
        cleaned = (text or "").strip()
        if not cleaned or not isinstance(contexts, list):
            return False

        for item in reversed(contexts[-8:]):
            if not isinstance(item, dict):
                continue
            if item.get("role") != "assistant":
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip() == cleaned:
                return True
        return False

    # ─── 对话历史持久化 ──────────────────────────────────────────

    async def _append_assistant_text_to_history(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> bool:
        """
        将语音文本写入 AstrBot 对话历史。
        即使会话重启，LLM 也能通过历史记录看到之前的语音内容。
        """
        try:
            cleaned = (text or "").strip()
            if not cleaned:
                return False

            manager = getattr(self.context, "conversation_manager", None)
            if manager is None:
                return False

            sid = self._get_umo(event)
            target_cid = (conversation_id or "").strip() or await manager.get_curr_conversation_id(sid)
            if not target_cid:
                target_cid = await manager.new_conversation(sid)

            conversation = await manager.get_conversation(sid, target_cid)
            if conversation is None:
                target_cid = await manager.new_conversation(sid)
                conversation = await manager.get_conversation(sid, target_cid)
            if conversation is None:
                return False

            raw_history = getattr(conversation, "history", "[]") or "[]"
            try:
                history = json.loads(raw_history)
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []

            history.append({"role": "assistant", "content": cleaned})
            await manager.update_conversation(sid, target_cid, history=history)
            logger.info(
                f"[VoxCPM2 TTS] 已写入对话历史 sid={sid} cid={target_cid} text={cleaned[:60]}"
            )
            return True
        except Exception as e:
            logger.error(f"[VoxCPM2 TTS] 写入对话历史失败: {e}")
            return False
