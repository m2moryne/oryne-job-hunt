"""Settings, from the environment (and .env, written by setup_testnet.py)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Reads KEY=value lines into os.environ, without overriding what's already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Oryne
    oryne_api: str = _env("ORYNE_API_URL", "https://testnet-apiv1.oryne.org").rstrip("/")
    # CareerKit, the paid API: where agents reach it, and the Stellar address it's paid at.
    public_url: str = _env("PUBLIC_URL", "http://localhost:8800").rstrip("/")
    careerkit_address: str = _env("CAREERKIT_ADDRESS")
    # The demo wallet: an owner account (as Freighter would be) and the wallet contract it owns.
    demo_owner_secret: str = _env("DEMO_OWNER_SECRET")
    demo_wallet: str = _env("DEMO_WALLET")
    # The model. Any OpenAI-compatible chat API with tool calling; DeepSeek by default.
    llm_api_key: str = _env("LLM_API_KEY") or _env("DEEPSEEK_API_KEY")
    llm_base_url: str = _env("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    llm_model: str = _env("LLM_MODEL", "deepseek-chat")
    # Abuse limits for the public demo.
    max_concurrent_runs: int = int(_env("MAX_CONCURRENT_RUNS", "4"))
    runs_per_ip_per_hour: int = int(_env("RUNS_PER_IP_PER_HOUR", "6"))

    @property
    def careerkit_url(self) -> str:
        return f"{self.public_url}/careerkit"

    @property
    def facilitator(self) -> str:
        return f"{self.oryne_api}/v1/x402"

    @property
    def demo_ready(self) -> bool:
        return bool(self.demo_owner_secret and self.demo_wallet)


settings = Settings()
