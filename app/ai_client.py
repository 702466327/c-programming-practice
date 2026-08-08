"""AI 客户端模块 - 封装大模型 API 调用（纯标准库实现）

支持多家 OpenAI 兼容接口平台，通过环境变量配置：
    AI_PLATFORM  平台标识（如 siliconflow / openai / deepseek ...）
    AI_API_KEY   API 密钥
    AI_API_BASE  接口地址（可选，覆盖平台默认值）
    AI_MODEL     模型名（可选，覆盖平台默认值）

例如：
    $env:AI_PLATFORM="openai"; $env:AI_API_KEY="sk-xxx"; python server.py
"""

import json
import os
import re
import time
import urllib.request
import urllib.error

# ===== 支持的 AI 平台（全部为 OpenAI 兼容 chat/completions 接口） =====
# 默认模型按 2026-08 各平台当前主流型号更新；用户可在启动页手动修改或拉取实时模型列表。
AI_PLATFORMS = {
    "siliconflow": {
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V4-Flash",
        "note": "注册送免费额度，模型丰富；DeepSeek-V4-Flash 约 ¥1/M 输入",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-5.6-terra",
        "note": "官方接口，需要国际网络环境；gpt-5.6-terra 为性价比档",
    },
    "deepseek": {
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-flash",
        "note": "性价比高，国内直连；旧模型名 deepseek-chat/reasoner 已于 2026-07 下线",
    },
    "zhipu": {
        "name": "智谱 AI (GLM)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4.7-flash",
        "note": "GLM 系列，glm-4.7-flash 免费（替代已下线的 glm-4.5-flash）",
    },
    "dashscope": {
        "name": "阿里云通义千问 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "note": "qwen-plus 自动更新到最新版；旗舰为 qwen3-max",
    },
    "moonshot": {
        "name": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2.6",
        "note": "Kimi 系列，国内直连；旗舰 kimi-k3，旧 moonshot-v1 将于 2026-08 停用",
    },
    "hunyuan": {
        "name": "腾讯混元 Hunyuan",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "default_model": "hunyuan-turbo",
        "note": "腾讯混元，需在腾讯云开通；turbo 适合通用对话/点评",
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "default_model": "",
        "note": "任意支持 /chat/completions 的接口，需自填接口地址与模型名",
    },
}

# 平台不支持 /models 接口时的内置备选清单（仅供参考，以实际接口为准）
FALLBACK_MODELS = {
    "siliconflow": [
        "deepseek-ai/DeepSeek-V4-Flash",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3",
        "Qwen/Qwen3-235B-A22B",
        "Qwen/Qwen2.5-72B-Instruct",
        "OpenAI/gpt-oss-120b",
        "moonshotai/Kimi-K2",
    ],
    "openai": [
        "gpt-5.6-terra",
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
    ],
    "deepseek": [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ],
    "zhipu": [
        "glm-4.7-flash",
        "glm-4.6",
        "glm-4.5",
        "glm-4-plus",
    ],
    "dashscope": [
        "qwen-plus",
        "qwen-max",
        "qwen3-max",
        "qwen3-max-preview",
        "qwen-flash",
        "qwen-turbo",
        "qwen3-coder-plus",
        "qwen3-coder-flash",
        "qwen-long-latest",
    ],
    "moonshot": [
        "kimi-k3",
        "kimi-k2.6",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
    ],
    "hunyuan": [
        "hunyuan-turbo",
        "hunyuan-turbo-latest",
        "hunyuan-lite",
        "hunyuan-standard",
        "hunyuan-standard-256K",
        "hunyuan-pro",
        "hunyuan-code",
    ],
}

# 拉取模型列表时过滤掉非文本对话类模型（语音/图像/向量等）
NON_CHAT_PATTERN = re.compile(
    r"(whisper|tts|embedding|dall|moderation|audio|image|realtime|video|"
    r"transcri|rerank|reranker|ocr|asr|visual|vl-|vl$|vqa)",
    re.I,
)

# ===== 运行时配置（环境变量，启动页/启动脚本负责注入） =====
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_PLATFORM = os.environ.get("AI_PLATFORM", "").strip().lower()
AI_API_BASE = os.environ.get("AI_API_BASE", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "").strip()
AI_TIMEOUT = 20
AI_MAX_RETRIES = 2


def resolve_config(api_key=None, platform=None, base_url=None, model=None):
    """解析出最终的 (平台, 平台名, 接口地址, 模型, 密钥)

    显式传入的参数优先，其次环境变量，最后平台默认值。
    """
    platform_id = (platform or AI_PLATFORM or "siliconflow").strip().lower()
    if platform_id not in AI_PLATFORMS:
        platform_id = "custom"
    entry = AI_PLATFORMS[platform_id]

    base = (base_url or AI_API_BASE or entry.get("base_url", "")).strip().rstrip("/")
    mdl = (model or AI_MODEL or entry.get("default_model", "")).strip()
    key = (api_key if api_key is not None else AI_API_KEY).strip()
    return platform_id, entry["name"], base, mdl, key


def is_ai_available():
    """检查 AI 服务是否可用（有密钥且有可用的接口地址/模型）"""
    _, _, base, model, key = resolve_config()
    return bool(key and base and model)


def get_info():
    """返回 AI 配置摘要（不含密钥），供状态接口/启动页展示"""
    platform_id, platform_name, base_url, model, key = resolve_config()
    return {
        "available": bool(key and base_url and model),
        "platform": platform_id,
        "platform_name": platform_name,
        "model": model,
        "base_url": base_url,
    }


def test_connection(api_key=None, platform=None, base_url=None, model=None, timeout=15):
    """用最小请求测试指定配置能否连通，返回 (ok, message)"""
    _, platform_name, base, mdl, key = resolve_config(api_key, platform, base_url, model)
    if not key:
        return False, "未填写 API 密钥"
    if not base:
        return False, "缺少接口地址（请选择平台或填写自定义地址）"
    if not mdl:
        return False, "缺少模型名称（请选择平台或填写自定义模型）"

    payload = json.dumps({
        "model": mdl,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "Connection": "close",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            choices = body.get("choices") or []
            if not choices:
                return False, "接口已连通，但未返回有效响应"
            return True, f"连接成功（{platform_name} · {mdl}）"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:150].replace("\n", " ")
        if e.code == 401:
            return False, f"密钥无效或权限不足（HTTP 401）：{detail}"
        if e.code == 402:
            return False, f"账户余额或额度不足（HTTP 402）：{detail}"
        return False, f"接口返回错误 HTTP {e.code}：{detail}"
    except Exception as e:
        return False, f"无法连接：{e}"


def _filter_chat_models(raw_ids):
    """从接口返回的模型 ID 中筛出文本对话类模型（去重、过滤语音/图像/向量等）"""
    seen = set()
    result = []
    for mid in raw_ids:
        mid = str(mid).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        if NON_CHAT_PATTERN.search(mid):
            continue
        result.append(mid)
    return result[:300]


def list_models(api_key=None, platform=None, base_url=None, timeout=15):
    """拉取平台可用模型列表，返回 (ok, message, models)

    调用 OpenAI 兼容的 GET {base}/models 接口；若平台不支持或密钥无效，
    则回退到内置备选清单 FALLBACK_MODELS。
    """
    platform_id, platform_name, base, _, key = resolve_config(api_key, platform, base_url, None)
    if not key:
        return False, "未填写 API 密钥", []
    if not base:
        return False, "缺少接口地址（请选择平台或填写自定义地址）", FALLBACK_MODELS.get(platform_id, [])

    url = f"{base}/models"
    try:
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {key}", "Connection": "close"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        raw = []
        for item in data.get("data") or []:
            if isinstance(item, dict) and item.get("id"):
                raw.append(item["id"])
        models = _filter_chat_models(raw)
        if not models:
            return False, "接口返回的模型列表为空或无可用的文本模型", FALLBACK_MODELS.get(platform_id, [])
        return True, f"已从 {platform_name} 拉取 {len(models)} 个可用模型", models
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:120].replace("\n", " ")
        fallback = FALLBACK_MODELS.get(platform_id, [])
        if e.code == 401:
            return False, f"密钥无效或权限不足（HTTP 401）：{detail}", fallback
        if e.code in (404, 405):
            return False, f"该平台不支持模型列表接口（HTTP {e.code}），已提供常用模型备选", fallback
        return False, f"接口返回错误 HTTP {e.code}：{detail}", fallback
    except Exception as e:
        return False, f"无法拉取模型列表：{e}", FALLBACK_MODELS.get(platform_id, [])


def strip_comments(code):
    """剔除 C++ 注释，防止学生通过注释向 AI 索要答案"""
    # 先保护字符串内容，避免把字符串里的 // 误判为注释
    def hide_strings(s):
        result = list(s)
        i = 0
        while i < len(s):
            if s[i] == '"':
                j = i + 1
                while j < len(s) and s[j] != '"':
                    if s[j] == '\\':
                        j += 1
                    j += 1
                for k in range(i, min(j + 1, len(s))):
                    result[k] = ' '
                i = j + 1
                continue
            i += 1
        return ''.join(result)

    cleaned = hide_strings(code)
    result = list(code)
    i = 0
    while i < len(cleaned):
        # 多行注释 /* ... */
        if i + 1 < len(cleaned) and cleaned[i:i+2] == '/*':
            end = cleaned.find('*/', i + 2)
            if end != -1:
                for j in range(i, end + 2):
                    result[j] = ' '
                i = end + 2
                continue
        # 单行注释 //
        if i + 1 < len(cleaned) and cleaned[i:i+2] == '//':
            end = cleaned.find('\n', i + 2)
            if end == -1:
                end = len(cleaned)
            for j in range(i, end):
                result[j] = ' '
            i = end
            continue
        i += 1
    return ''.join(result)


def get_feedback(question_title, question_description, user_code, execution_result):
    """调用 AI 获取 C++ 代码反馈（含重试机制）"""
    if not is_ai_available():
        return None

    _, _, base_url, model, api_key = resolve_config()
    if not base_url or not model:
        return None

    # 防作弊：剔除全部注释
    clean_code = strip_comments(user_code)

    # 去注释后几乎无代码则拒绝
    code_body = re.sub(r'\s+', '', clean_code)
    if len(code_body) < 10:
        return "代码内容不足，请先编写解题代码后再提交。"

    max_desc_len = 800
    if len(question_description) > max_desc_len:
        question_description = question_description[:max_desc_len] + "..."

    prompt = f"""你是一个 C++ 编程助教，正在批改学生的编程练习。

**题目：{question_title}**

**题目描述：**
{question_description}

**学生提交的代码：**
```cpp
{clean_code}
```

**代码执行结果：**
{execution_result}

请根据以上执行结果，对这段 C++ 代码进行点评。如果编译失败，指出错误原因；如果输出不对，分析逻辑问题。

**重要规则：**
- 绝对不要直接给出完整答案或可直接复制提交的代码。
- 只点评学生已写的代码，给出修改方向和关键提示，而不是替他写代码。
- 可以提供代码片段示例说明概念，但不能是完整解题代码。

请用中文回答，控制在200字以内。"""

    url = f"{base_url}/chat/completions"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是 C++ 编程助教。只点评学生代码，不准给完整答案。代码越少越只能给提示，不能替他写。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.4,
        "max_tokens": 300
    }).encode("utf-8")

    last_error = ""
    for attempt in range(AI_MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Connection": "close"
                }
            )
            with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                choices = body.get("choices") or []
                if not choices:
                    return "AI 未返回可用点评，请根据执行结果自行检查代码。"
                return choices[0]["message"]["content"]

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            last_error = f"API 错误 {e.code}: {error_body[:150]}"
            # 4xx 错误不重试（如 400 参数错误、401 密钥错误）
            if 400 <= e.code < 500:
                break

        except Exception as e:
            last_error = str(e)

        if attempt < AI_MAX_RETRIES - 1:
            time.sleep(2 * (attempt + 1))  # 2s, 4s 递增等待

    return f"[AI 暂时不可用: {last_error}]\n\n请根据执行结果自行检查代码。"
