"""`.env.example` is read by Docker Compose: an empty value followed by a comment on the same line
(`VAR=   # note`) becomes the value "# note" and breaks the settings at start-up."""

import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def test_empty_values_have_no_inline_comments() -> None:
    bad = [
        line
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Z_][A-Z0-9_]*=\s+#", line)
    ]
    assert bad == []


def test_settings_load_from_env_example(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import Settings

    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            value = re.sub(r"\s+#.*$", "", m.group(2)).strip()  # compose drops " # comment" after a value
            monkeypatch.setenv(m.group(1), value)
    settings = Settings(_env_file=None)
    assert settings.llm_base_url and settings.llm_base_url.startswith("https://")
    assert settings.llm_api_key is None
    assert len(settings.llm_model_map) == 6
