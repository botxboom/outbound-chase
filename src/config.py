from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "outbound-chase"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://outbound:outbound@localhost:5432/outbound_chase"
    database_url_sync: str = "postgresql://outbound:outbound@localhost:5432/outbound_chase"

    # Temporal
    temporal_host: str = "localhost"
    temporal_port: int = 7233
    temporal_namespace: str = "default"

    # LLM — provider: openai_compatible (Ollama/OpenAI) or anthropic (native API key)
    llm_provider: str = "openai_compatible"
    llm_base_url: str = "http://localhost:11434/v1"  # Ollama or OpenAI; ignored for anthropic
    llm_api_key: str = "not-needed"
    llm_model: str = "mimo-v2.5"
    llm_max_tokens: int = 512
    llm_temperature: float = 0.3
    llm_timeout_seconds: float = 180.0
    llm_disable_think: bool = True  # qwen3 thinking models: disable for faster replies

    # Voice (Vapi)
    vapi_api_key: str = ""
    vapi_phone_number_id: str = ""

    # SMS (Twilio)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # Email (Resend)
    resend_api_key: str = ""
    email_from_email: str = "onboarding@resend.dev"
    email_from_name: str = "Marigold Injury Law"

    # eSign (DocuSeal — self-hosted or cloud)
    docuseal_api_url: str = "http://localhost:3000"
    docuseal_api_key: str = ""
    docuseal_template_id_en: int = 0
    docuseal_template_id_es: int = 0
    docuseal_submitter_role: str = "First Party"

    # Clio Manage
    clio_access_token: str = ""
    clio_base_url: str = "https://app.clio.com/api/v4"

    # Compliance
    quiet_hours_start: int = 8  # 8 AM
    quiet_hours_end: int = 21  # 9 PM
    max_voice_attempts: int = 3
    max_sms_attempts: int = 5
    max_email_attempts: int = 3
    max_total_attempts: int = 10
    chase_timeout_days: int = 14

    # Demo: toggle for voice consent (production: require explicit consent)
    demo_ai_voice_consent: bool = True
    demo_fast_cadence: bool = False  # cap wait timers for live testing (no multi-day waits)
    demo_wait_seconds_cap: int = 10  # max seconds between touches when demo_fast_cadence=true
    demo_chase_timeout_minutes: int = 15  # ghosting give-up window when demo_fast_cadence=true

    # Channel modes
    use_mock_channels: bool = True
    voice_mode: str = "simulated"  # simulated | vapi
    public_base_url: str = "http://localhost:8000"
    clio_grow_fixture_path: str = "fixtures/partial_leads.json"

    # Strategy layer
    strategy_llm_signals: bool = True
    strategy_llm_timeout_seconds: float = 5.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
