"""OpenAI-compatible TencentDB proxy, with opt-in knowledge for controlled evaluations."""

import os
from urllib.parse import quote, urlsplit
from uuid import uuid4

from minisweagent.models.litellm_model import LitellmModel, LitellmModelConfig


class TencentDBModelConfig(LitellmModelConfig):
    proxy_url: str = "http://127.0.0.1:8096"
    space_id: str = "default"
    api_key_env: str = "TDAI_MEMORY_KEY"
    knowledge_enabled: bool = False
    team_id: str = ""
    agent_id: str = ""
    knowledge_url: str = ""
    """Knowledge service URL reachable from the shell environment, including /v3."""
    session_id: str = ""
    """Leave empty for a fresh ID per task; set only when explicitly resuming a session."""


class TencentDBModel(LitellmModel):
    def __init__(self, **kwargs):
        super().__init__(config_class=TencentDBModelConfig, **kwargs)
        for field, env in (
            ("proxy_url", "TDAI_PROXY_URL"),
            ("space_id", "TDAI_SPACE_ID"),
            ("team_id", "TDAI_TEAM_ID"),
            ("agent_id", "TDAI_AGENT_ID"),
            ("knowledge_url", "TDAI_KNOWLEDGE_URL"),
        ):
            if field not in kwargs and env in os.environ:
                setattr(self.config, field, os.environ[env])
        for value in (self.config.proxy_url, self.config.knowledge_url):
            if value:
                url = urlsplit(value)
                if (
                    url.scheme not in ("http", "https")
                    or not url.netloc
                    or url.username
                    or url.password
                    or url.query
                    or url.fragment
                ):
                    raise ValueError(
                        "TencentDB URLs must be absolute HTTP(S) URLs without credentials, query or fragment"
                    )
        if not self.config.proxy_url or not self.config.space_id.strip():
            raise ValueError("TencentDB proxy_url and space_id must not be empty")
        if self.config.knowledge_enabled and not (self.config.team_id.strip() and self.config.agent_id.strip()):
            raise ValueError("Knowledge requires team_id and agent_id (or TDAI_TEAM_ID and TDAI_AGENT_ID)")
        if not os.getenv(self.config.api_key_env):
            raise ValueError(f"Set {self.config.api_key_env} to the MemoryPanel user key")
        # A model overlay may inherit a direct provider's transport settings.
        # Resolve credentials only at request time and never save them in trajectories.
        for key in ("api_key", "api_base", "base_url", "custom_llm_provider", "extra_headers", "default_headers"):
            self.config.model_kwargs.pop(key, None)
        self.session_id = self.config.session_id or str(uuid4())

    def format_message(self, **kwargs) -> dict:
        if kwargs.get("role") == "system" and not self.config.session_id:
            self.session_id = str(uuid4())
        return super().format_message(**kwargs)

    def _query(self, messages: list[dict], **kwargs):
        headers = {
            "x-conversation-id": self.session_id,
            "x-tdai-knowledge": "enabled" if self.config.knowledge_enabled else "disabled",
        }
        if self.config.knowledge_enabled:
            headers |= {"x-team-id": self.config.team_id, "x-agent-id": self.config.agent_id}
            if self.config.knowledge_url:
                headers["x-tdai-knowledge-url"] = self.config.knowledge_url
        return super()._query(
            messages,
            **(
                kwargs
                | {
                    "custom_llm_provider": "openai",
                    "api_base": f"{self.config.proxy_url.rstrip('/')}/mini-swe-agent/{quote(self.config.space_id, safe='')}/v1",
                    "api_key": os.environ[self.config.api_key_env],
                    "extra_headers": headers,
                }
            ),
        )

    def serialize(self) -> dict:
        data = super().serialize()
        data["info"]["tencentdb"] = {
            "session_id": self.session_id,
            "knowledge_enabled": self.config.knowledge_enabled,
        }
        return data
