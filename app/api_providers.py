"""API presets and explicit capabilities; no credentials or provider SDKs."""
from urllib.parse import urlparse

PRESETS = {
    "openai": {"name": "OpenAI API", "base_url": "https://api.openai.com/v1", "protocol": "responses",
               "model": "gpt-6-astra", "images": True, "reasoning": True, "models": []},
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "protocol": "chat_completions",
                 "model": "deepseek-v4-flash", "images": False, "reasoning": True,
                 "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"]},
    "custom": {"name": "其他兼容 API / 本地模型", "base_url": "", "protocol": "chat_completions",
               "model": "", "images": False, "reasoning": False, "models": []},
}


def local_endpoint(base_url):
    return urlparse(base_url).hostname in {"localhost", "127.0.0.1", "::1"}


def capabilities(config, model=None):
    model = model or config["model"]
    preset = config.get("api_preset", "openai")
    if preset == "deepseek":
        # Unknown future models can use the explicit capability setting.
        images = ("vision" in model) if model in PRESETS["deepseek"]["models"] else config.get("api_images", False)
        efforts = ["none", "low", "high", "max"] if model.startswith("deepseek-v4-") else []
    else:
        images = config.get("api_images", True)
        efforts = (["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
                   if config.get("api_protocol", "responses") == "responses" else ["low", "medium", "high"])
        if not config.get("api_reasoning", True):
            efforts = []
    return {"images": images, "efforts": efforts,
            "web_search": preset == "openai" and config.get("api_protocol", "responses") == "responses"}
