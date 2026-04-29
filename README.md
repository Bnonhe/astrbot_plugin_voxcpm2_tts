# astrbot_plugin_voxcpm2_tts

面向 [AstrBot](https://github.com/Soulter/AstrBot) 的 VoxCPM2 语音合成插件，为 AI 对话助手提供情感化语音输出能力。支持 Voice Design / Voice Clone / Ultimate Clone / LoRA Fine-tune 四种合成模式，参数组合自动判断，无需手动切换。

---

## 目录

- [架构概览](#架构概览)
- [前置要求](#前置要求)
- [安装插件](#安装插件)
- [部署 VoxCPM2 Server](#部署-voxcpm2-server)
- [配置](#配置)
- [合成模式详解](#合成模式详解)
- [命令参考](#命令参考)
- [LLM 工具调用](#llm-工具调用)
- [Control Instruction 音色控制](#control-instruction-音色控制)
- [常见问题](#常见问题)

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          用户 (QQ / 微信 / 飞书 ...)                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AstrBot (Docker 容器内)                              │
│                                                                         │
│   用户消息 ──► LLM 推理 ──► 回复内容                                      │
│                          │                                               │
│                          ├──► [路径1] LLM 工具调用 (voxcpm2_tts)          │
│                          │                                                │
│                          ├──► [路径2] Auto TTS Hook (自动触发)             │
│                          │                                                │
│                          └──► [路径3] 手动命令 (tts_say / 说)              │
│                                                                         │
│   ┌──────────────────────────────────────────────────────────────┐      │
│   │              astrbot_plugin_voxcpm2_tts 插件                  │      │
│   │                                                               │      │
│   │   输入文本 ──► 模式判断 ──► 构建 payload ──► HTTP 请求          │      │
│   │                    │                                          │      │
│   │                    ├── voice_style → Voice Design             │      │
│   │                    ├── + reference_wav → Voice Clone          │      │
│   │                    ├── + prompt_text  → Ultimate Clone        │      │
│   │                    ├── + lora_path    → LoRA                  │      │
│   │                    └── 组合叠加        → LoRA + Clone          │      │
│   └──────────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │ HTTP POST /v1/audio/speech
                                    │ 注意：Docker 容器内 localhost ≠ WSL
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    VoxCPM2 Server (WSL Ubuntu-24.04)                    │
│                    http://<WSL_IP>:8000                                  │
│                                                                         │
│   接收请求 ──► 模式判断 ──► VoxCPM2 推理 (GPU) ──► 音频流返回            │
│                    │                                                     │
│                    ├── Voice Design (无参考音频)                         │
│                    ├── Voice Clone (参考音频路径)                         │
│                    ├── Ultimate Clone (参考音频 + 转录文本)              │
│                    ├── LoRA Fine-tune (LoRA 权重)                        │
│                    └── LoRA + Clone (叠加模式)                           │
│                                                                         │
│   RTX 4070 Ti SUPER 16GB                                               │
└─────────────────────────────────────────────────────────────────────────┘
```

**关键理解**：AstrBot 运行在 Docker 容器里，容器内的 `localhost` 指向容器自己，而不是 WSL。所以配置地址必须用 WSL 的 IP，例如 `http://172.22.11.55:8000`，不能写 `localhost`。

---

## 前置要求

### 硬件

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| GPU | NVIDIA GPU，8GB 显存 | RTX 4070 Ti SUPER，16GB 显存 |
| 内存 | 16GB | 32GB |
| 硬盘 | 20GB 可用空间 | SSD |

### 软件环境

#### 1. Windows + WSL2

本插件依赖 VoxCPM2 Server 运行在 **WSL2 (Ubuntu-24.04)** 环境下：

```powershell
# 检查 WSL 是否已安装
wsl --status

# 如果没有安装，执行
wsl --install -d Ubuntu-24.04
```

#### 2. Docker Desktop

AstrBot 需要运行在 Docker 容器里：

```powershell
# 检查 Docker 是否运行
docker --version
docker ps
```

如果没有安装，从 [Docker 官网](https://www.docker.com/products/docker-desktop/) 下载安装。

#### 3. AstrBot 已部署

确保 AstrBot 已经成功部署并运行：

```powershell
# 查看运行中的容器
docker ps

# 确认 AstrBot 容器名称或 ID
```

如果还没有部署 AstrBot，请先参考 [AstrBot 官方文档](https://github.com/Soulter/AstrBot) 完成部署。

#### 4. VoxCPM2 模型文件

VoxCPM2 是一个 2B 参数的 TTS 模型，需要下载模型权重：

- 官方模型：[VoxCPM2 HuggingFace](https://huggingface.co/FenomAI/VoxCPM2)
- 首次部署需要下载约 4-8GB 的模型文件
- 模型存放路径自定义，推荐 `/mnt/e/WSL/model/dir`

#### 5. 获取 WSL IP 地址

**这是最关键的一步**，每次 WSL 重启后 IP 可能变化：

```powershell
# 在 PowerShell 中执行
wsl.exe -d Ubuntu-24.04 hostname -I
```

返回结果示例：`172.22.11.55`

**记录这个 IP**，后续配置地址时需要用到，例如：
- 插件配置：`http://172.22.11.55:8000`
- VoxCPM2 Server 启动命令绑定：`0.0.0.0:8000`

---

## 安装插件

### 方式一：AstrBot 插件市场（推荐）

1. 打开 AstrBot WebUI（通常是 `http://localhost:6185`）
2. 进入「插件管理」或「插件市场」
3. 搜索 `voxcpm2_tts`
4. 点击安装

### 方式二：手动安装

如果你是开发者或插件市场找不到，按以下步骤操作：

**1. 克隆仓库到 AstrBot 插件目录**

```bash
# 进入 AstrBot 的插件目录（容器内）
docker exec -it <你的AstrBot容器名或ID> bash

# 或者直接用路径（取决于你的部署方式）
# 常见路径：
#   /AstrBot/data/plugins/
#   /home/docker/astrbot/data/plugins/

cd /AstrBot/data/plugins/
git clone https://github.com/caiyi/astrbot_plugin_voxcpm2_tts.git
```

**2. 重启 AstrBot**

```bash
docker restart <你的AstrBot容器名或ID>
```

**3. 验证插件加载成功**

查看 AstrBot 日志，确认以下输出：

```
[Core] [INFO] ...载入插件 astrbot_plugin_voxcpm2_tts ...
[Plug] [INFO] [VoxCPM2 TTS] 初始化完成
```

---

## 部署 VoxCPM2 Server

插件安装好后，还需要单独部署 VoxCPM2 Server——这是实际运行 TTS 推理的服务，**插件只是客户端**。

### 步骤 1：进入 WSL

```powershell
wsl.exe -d Ubuntu-24.04
```

### 步骤 2：克隆 VoxCPM2 项目

```bash
cd /workspace

# 如果已有项目，更新到最新
git -C VoxCPM2 pull origin master 2>/dev/null || {

# 如果没有，克隆项目
git clone https://github.com/OpenBMB/VoxCPM2.git
cd VoxCPM2
}
```

### 步骤 3：安装依赖

```bash
cd /workspace/VoxCPM2

# 创建虚拟环境（推荐）
python3 -m venv voxcpm2_env
source voxcpm2_env/bin/activate

# 安装依赖
pip install -r requirements.txt

# 安装 nanovllm（关键依赖，支持 GPU 加速）
pip install nanovllm_voxcpm
```

### 步骤 4：配置模型路径

编辑 `server.py`，设置模型目录：

```python
# server.py 中的 MODEL_DIR 改为你的模型路径
MODEL_DIR = "/mnt/e/WSL/model/dir"  # 替换为实际路径
```

### 步骤 5：启动服务

```bash
cd /workspace/VoxCPM2
nohup python3 server.py --port 8000 > voxcpm2.log 2>&1 &
echo "VoxCPM2 已启动，PID: $!"
```

### 步骤 6：验证服务运行

```bash
# 测试本地访问
curl http://localhost:8000/health

# 应该返回类似：
# {"status":"ready","model_loaded":true,"sample_rate":48000,...}
```

### 步骤 7：验证 Docker 容器能连接（关键！）

```bash
# 从 Docker 容器内测试连接 WSL 的 VoxCPM2
docker exec <AstrBot容器名或ID> curl http://172.22.11.55:8000/health
```

如果这一步失败，检查：
- WSL IP 是否变化（重新执行 `wsl.exe -d Ubuntu-24.04 hostname -I` 获取新 IP）
- VoxCPM2 是否绑定到 `0.0.0.0` 而不是 `127.0.0.1`

### 服务管理命令

```bash
# 查看进程
ps aux | grep server.py

# 查看日志
tail -f /workspace/VoxCPM2/voxcpm2.log

# 重启服务
pkill -f server.py
cd /workspace/VoxCPM2 && nohup python3 server.py --port 8000 > voxcpm2.log 2>&1 &

# 停止服务
pkill -f server.py
```

---

## 配置

在 AstrBot WebUI 的插件配置页面中设置。

### 服务配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `server.url` | VoxCPM2 Server 地址，**必须是 WSL IP** | `http://172.22.11.55:8000` |
| `server.api_key` | API Key，与 Server 端一致即可 | `voxcpm2` |
| `server.timeout` | 请求超时秒数，LoRA 推理较慢建议 120s | `120` |

> ⚠️ **常见错误**：`server.url` 如果填 `localhost` 或 `127.0.0.1`，Docker 容器内会连接失败，必须填 WSL IP。

### 声音配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `voice.voice_style` | 声音风格描述（括号指令），留空则 LLM 自由控制 | 空 |
| `voice.reference_wav_path` | 参考音频文件路径，用于音色克隆 | 空 |
| `voice.prompt_text` | 参考音频对应的转录文本，用于高保真克隆 | 空 |

### LoRA 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `lora.lora_path` | LoRA 权重文件路径，留空则不使用 LoRA | 空 |
| `lora.lora_alpha` | LoRA 缩放系数，alpha/r = 实际缩放比例 | `32` |

### 生成参数

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `generation.cfg_value` | CFG 引导强度，1.0=自然，2.0=默认，3.0=严格遵循指令 | `2.0` |
| `generation.inference_timesteps` | 扩散推理步数，4=快，10=默认，30=精细 | `10` |
| `generation.output_format` | 输出格式，`mp3` 体积小，`wav` 无损 | `mp3` |

### 自动语音

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `auto_tts.enable` | 是否启用自动语音合成 | `true` |
| `auto_tts.probability` | 每次回复触发语音的概率，1.0=每次都触发 | `1.0` |
| `auto_tts.mode` | `blacklist`=全部启用但排除指定会话，`whitelist`=仅指定会话启用 | `blacklist` |

---

## 合成模式详解

插件根据参数组合**自动判断**使用哪种模式，无需手动切换：

| voice_style | reference_wav_path | prompt_text | lora_path | 合成模式 | 说明 |
|-------------|---------------------|-------------|-----------|---------|------|
| 有值 | — | — | — | **Voice Design** | 用文字描述生成音色，无需参考音频 |
| — | ✅ 有 | — | — | **Voice Clone** | 基于参考音频克隆音色 |
| — | ✅ 有 | ✅ 有 | — | **Ultimate Clone** | 高保真克隆，韵律和音色双重复制 |
| — | — | — | ✅ 有 | **LoRA** | 使用 LoRA 微调的音色 |
| — | ✅ 有 | — | ✅ 有 | **LoRA + Clone** | LoRA 音色 + 克隆叠加 |
| — | ✅ 有 | ✅ 有 | ✅ 有 | **LoRA + Clone (Ultimate)** | LoRA + 完整 Ultimate Clone |
| — | — | — | — | **Base** | 基础模型，随机音色 |

### Voice Design（文字音色）

最简单的方式，不需要任何音频文件。在 `voice_style` 中用自然语言描述想要的音色：

```
(年轻女性，温柔甜美，轻微英伦口音)
```

VoxCPM2 支持中英文描述：
- `(warm female voice with slight British accent)`
- `(语速稍快，充满活力)`

### Voice Clone（音色克隆）

准备一段参考音频（5-30 秒清晰语音），填入 `reference_wav_path`：

```
voice.reference_wav_path = "/mnt/e/WSL/voice_library/serana.wav"
```

### Ultimate Clone（高保真克隆）

除了参考音频，再填入对应的转录文本，效果更好：

```
voice.reference_wav_path = "/mnt/e/WSL/voice_library/serana.wav"
voice.prompt_text = "这是参考音频的文字内容，用于高保真克隆"
```

### LoRA Fine-tune（LoRA 音色）

填入 LoRA 权重路径：

```
lora.lora_path = "/mnt/e/WSL/voxcpm2_lora/checkpoints/latest"
lora.lora_alpha = 32
```

运行时可以动态切换：
- `tts_load_lora <路径> [alpha]` — 加载指定 LoRA
- `tts_reset_lora` — 卸载 LoRA，恢复基础模型
- `tts_lora_status` — 查看当前状态

---

## 命令参考

在聊天窗口发送以下命令即可触发：

### 语音控制

| 命令 | 说明 |
|------|------|
| `tts_say <内容>` 或 `说 <内容>` | 手动触发 TTS 合成 |
| `tts_on` | 开启当前会话的自动语音 |
| `tts_off` | 关闭当前会话的自动语音 |
| `tts_all_on` | 开启全局自动语音 |
| `tts_all_off` | 关闭全局自动语音 |
| `tts_status` | 查看当前 TTS 状态和配置 |

### LoRA 管理

| 命令 | 说明 |
|------|------|
| `tts_lora_status` | 查看 Server 当前 LoRA 加载状态 |
| `tts_reset_lora` | 卸载 LoRA，恢复基础模型音色 |
| `tts_load_lora <路径> [alpha]` | 加载指定 LoRA，不填参数则用配置文件中的 |

示例：
```
tts_load_lora /mnt/e/WSL/voxcpm2_lora/checkpoints/latest 20
tts_load_lora  # 使用配置文件中的路径
```

### 调试

| 命令 | 说明 |
|------|------|
| `sid` | 获取当前会话的 UMO（用于配置黑白名单） |

---

## LLM 工具调用

插件注册了 `voxcpm2_tts` 函数工具，AstrBot 的 LLM 可以主动调用将特定文本转为语音。

### 用法

在 LLM 的回复中，使用 `<tts>...</tts>` 标签包裹需要语音输出的内容：

```
你好，这里是普通文字。<tts>这段话需要用语音读出来。</tts>这里又是普通文字。
```

LLM 在调用工具时，`<tts>` 标签内的内容会被提取并合成语音。

### 多段语音

支持多段独立语音：

```
<tts>第一段语音内容</tts>
<tts>第二段语音内容</tts>
```

每段独立合成，可以用于同一回复中切换不同语气或角色。

### 自动剥离标签

`<tts>...</tts>` 标签仅用于标记语音内容，插件会自动剥离后传给 VoxCPM2，最终音频中不会包含标签文字。

---

## Control Instruction 音色控制

VoxCPM2 支持在文本前加括号指令来控制音色和语速，称为 **Control Instruction**。

### 基本语法

```
(音色描述)要合成的内容
```

### 常用描述示例

**音色**：
```
(warm female voice)
(低沉男声)
(年轻女性，温柔甜美)
```

**语速**：
```
(slightly faster)
(语速稍快)
(slow pace, calm)
```

**情绪**：
```
(angry and forceful)
(兴奋且充满活力)
(悲伤地，低声说)
```

**组合**：
```
(warm female voice, slightly faster, cheerful tone)
(语速稍快，轻快地，年轻女性)
```

### 与插件配置的关系

| 你的配置 | 行为 |
|---------|------|
| `voice_style` **留空** | LLM 的括号指令原样传给 VoxCPM2，LLM 自由控制音色 |
| `voice_style` **有值** | 插件清除 LLM 的括号指令，以你的配置为准 |

如果希望 LLM 每次都能精细控制语气，建议将 `voice_style` 留空。

---

## 常见问题

### Q1：提示"无法连接 VoxCPM2 Server"

**原因**：Docker 容器无法访问 WSL 的 VoxCPM2 服务。

**排查步骤**：

1. 确认 VoxCPM2 服务已启动：
   ```bash
   curl http://localhost:8000/health
   ```

2. 确认使用的是 WSL IP 而不是 localhost：
   ```powershell
   wsl.exe -d Ubuntu-24.04 hostname -I
   ```
   将返回的 IP 填入插件配置的 `server.url`，例如 `http://172.22.11.55:8000`

3. 从容器内测试连接：
   ```bash
   docker exec <容器名> curl http://172.22.11.55:8000/health
   ```

### Q2：提示"LoRA 卸载/加载失败"

**原因**：`service.py` 中的 `reset_lora()` 和 `load_lora()` 方法需要更新到最新版本。

**解决方案**：重新从 GitHub 拉取最新代码并复制到容器内。

### Q3：合成的语音音色和预期不符

**可能原因**：
1. VoxCPM2 Server 残留了旧的 LoRA 权重
2. LLM 没有在 `<tts>` 标签中包含 Control Instruction

**解决方案**：
1. 执行 `tts_reset_lora` 清除 Server 残留状态
2. 在 `<tts>` 标签中明确写出音色描述，如 `<tts>(young female, gentle)你好</tts>`
3. 将插件的 `voice_style` 配置留空，让 LLM 自由控制

### Q4：音频时长为 0 或生成失败

**排查**：
1. 查看 VoxCPM2 Server 日志：`tail -f /workspace/VoxCPM2/voxcpm2.log`
2. 查看 AstrBot 容器日志：`docker logs <容器名> --tail 100`
3. 检查显存是否充足：`nvidia-smi`
4. 尝试减少 `inference_timesteps`（从 10 降到 4）

### Q5：命令被 LLM 吃掉，没有触发插件命令

**原因**：AstrBot 的命令 handler 没有匹配成功，消息进入 LLM 对话。

**解决方案**：
1. 确认插件已重启并加载成功（日志中有 `[VoxCPM2 TTS] 初始化完成`）
2. 尝试加命令前缀（如 `/tts_status` 或 `!tts_status`），不同平台命令格式可能不同
3. 查看 AstrBot 日志中命令匹配的输出

### Q6：WSL 重启后 VoxCPM2 服务停止了

**原因**：WSL 重启后进程会丢失。

**解决方案**：配置 systemd 服务让 VoxCPM2 自动启动，或使用快捷脚本重启：

```bash
# 保存为 ~/start_voxcpm2.sh
#!/bin/bash
cd /workspace/VoxCPM2
nohup python3 server.py --port 8000 > voxcpm2.log 2>&1 &
echo "VoxCPM2 PID: $!"
```

执行 `bash ~/start_voxcpm2.sh` 快速重启。

### Q7：显存不足 (CUDA out of memory)

**原因**：GPU 显存被其他程序占用，或者 LoRA 推理显存需求较高。

**解决方案**：
1. 关闭其他占用 GPU 的程序
2. 减少 `inference_timesteps`（从 10 降到 4-6）
3. 确认没有多个 VoxCPM2 进程同时运行

---

## 快速诊断清单

部署完成后，按以下顺序验证：

```powershell
# 1. WSL IP
wsl.exe -d Ubuntu-24.04 hostname -I

# 2. VoxCPM2 服务健康
curl http://localhost:8000/health

# 3. Docker 容器内连接（关键！）
docker exec <AstrBot容器> curl http://<WSL_IP>:8000/health

# 4. AstrBot 插件加载日志
docker logs <AstrBot容器> --tail 50 | grep VoxCPM2

# 5. 测试 TTS 命令
# 在聊天窗口发送：tts_say 你好，这是一条测试语音
```

---

## 许可证

MIT License
