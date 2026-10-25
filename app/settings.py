"""应用配置。所有运行参数来自环境变量或 .env。"""
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    app_env: str = "local"
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'app.db').as_posix()}"
    api_token: str = "local-dev-token-change-me"
    llm_provider: str = "deterministic"      # deterministic（默认，离线）/ deepseek（在线，显式启用）/ local（尚未实现）
    local_model_path: str = ""
    llm_api_key: SecretStr = SecretStr("")   # 只从环境变量 / .env 读取；SecretStr 在日志与 repr 中不会显示明文
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_s: float = 30.0
    # 浏览器登录（React 前端）：UI_USERS 形如 "alice:scrypt$...;bob:scrypt$..."，哈希由 scripts/make_ui_user.py 生成。
    # 静态 API_TOKEN 只给服务端 / 脚本用，绝不下发到浏览器；浏览器只持有 HttpOnly 会话 Cookie。
    ui_users: str = ""
    session_ttl_hours: int = 12
    web_dist_dir: str = str(ROOT / "web" / "dist")
    audit_jsonl_dir: str = str(ROOT / "data" / "audit")
    api_base_url: str = "http://127.0.0.1:8000"
    knowledge_dir: str = str(ROOT / "knowledge")
    index_dir: str = str(ROOT / "data" / "index")
    reports_dir: str = str(ROOT / "data" / "reports")

    @model_validator(mode="after")
    def _no_silent_llm(self) -> "Settings":
        # 在线 provider 必须显式启用并给出 key；禁止静默回退到公网
        if self.llm_provider not in ("deterministic", "deepseek", "local"):
            raise ValueError(f"未知的 LLM_PROVIDER：{self.llm_provider}")
        if self.llm_provider == "local" and not self.local_model_path:
            raise ValueError("LLM_PROVIDER=local 时必须设置 LOCAL_MODEL_PATH")
        if self.llm_provider == "deepseek":
            if not self.llm_api_key.get_secret_value().strip():
                raise ValueError("LLM_PROVIDER=deepseek 时必须在 LLM_API_KEY 中设置密钥（不要写进 LOCAL_MODEL_PATH）")
            url = self.llm_base_url.strip().lower()
            if not (url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost"))):
                raise ValueError("LLM_BASE_URL 必须是 https:// 地址（本机回环地址除外），避免密钥明文传输")
        if self.local_model_path.strip().startswith("sk-"):
            raise ValueError("LOCAL_MODEL_PATH 里看起来是 API 密钥；请把它移到 LLM_API_KEY（LOCAL_MODEL_PATH 只用于本地模型目录）")
        if self.app_env != "local" and self.api_token == "local-dev-token-change-me":
            raise ValueError("非本地环境必须替换默认 API_TOKEN")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
