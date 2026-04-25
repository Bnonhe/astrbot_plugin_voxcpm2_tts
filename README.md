# astrbot_plugin_voxcpm2_tts

AstrBot VoxCPM2 语音合成插件，支持多种合成模式，参数组合自动确定行为。

## 功能特性

- 🎙️ **Voice Design** — 用自然语言描述生成音色（无需参考音频）
- 🧬 **Voice Clone** — 基于参考音频克隆音色
- 🎯 **Ultimate Clone** — 高保真克隆（参考音频 + 转录文本）
- 🔧 **LoRA Fine-tune** — LoRA 微调音色
- 🔗 **LoRA + Clone** — LoRA 与克隆叠加
- 🤖 **三种触发方式** — Hook 自动 TTS / 手动命令 / LLM 工具调用
- 💾 **智能缓存** — 相同参数复用缓存音频
- 🔄 **LoRA 热切换** — 运行时加载/卸载 LoRA，无需重启服务

## 前置要求

- [AstrBot](https://github.com/Soulter/AstrBot) >= 4.16
- [VoxCPM2 Server](https://github.com/OpenBMB/VoxCPM) 运行中
- GPU（推荐 RTX 4070 Ti SUPER 或同等显存）

## 安装

### 方式一：AstrBot 插件市场（推荐）

在 AstrBot WebUI 的插件市场中搜索 `voxcpm2_tts` 安装。

### 方式二：手动安装

将本仓库克隆到 AstrBot 的 `data/plugins/` 目录：

```bash
cd /path/to/AstrBot/data/plugins/
git clone https://github.com/<your-username>/astrbot_plugin_voxcpm2_tts.git
```

重启 AstrBot 即可。

## 配置

在 AstrBot WebUI 的插件配置页面中设置：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `server.url` | VoxCPM2 Server 地址 | `http://172.22.11.55:8000` |
| `server.api_key` | API Key | `voxcpm2` |
| `server.timeout` | 请求超时（秒） | `120` |
| `voice.voice_style` | 声音风格描述 | 空（随机） |
| `voice.reference_wav_path` | 参考音频路径 | 空 |
| `voice.prompt_text` | 参考音频转录文本 | 空 |
| `lora.lora_path` | LoRA 权重路径 | 空 |
| `lora.lora_alpha` | LoRA Alpha | `32` |
| `generation.cfg_value` | CFG 引导强度 | `2.0` |
| `generation.inference_timesteps` | 推理步数 | `10` |
| `generation.output_format` | 输出格式 | `mp3` |
| `auto_tts.enable` | 启用自动转语音 | `true` |
| `auto_tts.probability` | 触发概率 | `1.0` |

### 合成模式自动判断

插件根据参数组合自动确定合成模式：

| voice_style | reference_wav_path | prompt_text | lora_path | 模式 |
|---|---|---|---|---|
| ✅ | ❌ | ❌ | ❌ | Voice Design |
| - | ✅ | ❌ | ❌ | Voice Clone |
| - | ✅ | ✅ | ❌ | Ultimate Clone |
| - | ❌ | ❌ | ✅ | LoRA |
| - | ✅ | ❌ | ✅ | LoRA + Clone |
| - | ✅ | ✅ | ✅ | LoRA + Clone (Ultimate) |

## 命令

| 命令 | 说明 |
|---|---|
| `tts_say <内容>` / `说 <内容>` | 手动触发 TTS |
| `tts_on` | 开启当前会话自动语音 |
| `tts_off` | 关闭当前会话自动语音 |
| `tts_all_on` | 开启全局自动语音 |
| `tts_all_off` | 关闭全局自动语音 |
| `tts_status` | 查看当前 TTS 状态 |
| `tts_lora_status` | 查看 Server 当前 LoRA 状态 |
| `tts_reset_lora` | 卸载 LoRA，恢复基础模型 |
| `tts_load_lora [路径] [alpha]` | 加载指定 LoRA |
| `sid` | 获取当前会话 UMO |

## LLM 工具调用

插件注册了 `voxcpm2_tts` 工具，LLM 可主动调用将文本转为语音。支持 `<tts>...</tts>` 标签。

## Control Instruction

VoxCPM2 支持在文本前加括号指令控制音色和语速：

```
(warm female voice, speaking slowly)你好，欢迎使用
```

- 插件配置 `voice_style` 留空时，LLM 的括号指令会原样传给 VoxCPM2
- 配置了 `voice_style` 时，以用户配置为准，LLM 指令会被清除

## VoxCPM2 Server 部署

参考 [VoxCPM2 官方文档](https://voxcpm.readthedocs.io/) 部署 Server。

配套的 Server v2 增强脚本（支持 LoRA 路径方式加载）见 `scripts/voxcpm2_server_v2.py`。

## 许可证

MIT License
