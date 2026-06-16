from anthropic import Anthropic
import os
from loguru import logger
import dotenv

dotenv.load_dotenv()

_MODEL_WINDOWS = {
    "deepseek-v4-flash": 1000000,
    "deepseek-v4-pro": 1000000,
}
DEFAULT_WINDOW = 128000

class LLMClient:
    def __init__(self, model: str):
        self.model = model
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
        self.client = self._llm_client(self.api_key, self.base_url)

    def _llm_client(self, api_key: str, base_url: str) -> Anthropic:
        try:
            return Anthropic(
                api_key=api_key,
                base_url=base_url,
                max_retries=3,
                timeout=60.0,
            )
        except Exception as e:
            logger.error(f"Error initializing LLM client: {e}")
            raise e

    def change_model(self, new_model: str) -> None:
        self.model = new_model

    def get_context_window(self) -> int:
        return _MODEL_WINDOWS.get(self.model, DEFAULT_WINDOW)
