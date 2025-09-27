"""
quadridigest/llm/prompts.py — корректная версия (совместима с FullText.full_text)
- НЕТ форматирования на уровне модуля;
- Все подстановки происходят ТОЛЬКО внутри функций;
- Схемы хранятся как «сырые» строки.
"""

from __future__ import annotations
from typing import List, Optional

_SUMMARIZE_SCHEMA = r"""{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "version": { "type": "string" },
    "topic": { "type": "string", "enum": ["policy","trading","personal_finance","infosec"] },
    "headline": { "type": "string", "minLength": 10, "maxLength": 160 },
    "summary": { "type": "string", "minLength": 30, "maxLength": 600 },
    "fingerprint": { "type": "string", "pattern": "^[a-f0-9]{8,64}$" },
    "evidence": { "type": "array", "items": { "type": "string" }, "maxItems": 10 }
  },
  "required": ["topic","headline","summary","fingerprint"]
}"""

# ВНИМАНИЕ: проект ожидает поле full_text
_FULL_SCHEMA = r"""{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "headline": { "type": "string", "minLength": 10, "maxLength": 160 },
    "full_text": { "type": "string", "minLength": 100, "maxLength": 4000 },
    "highlights": { "type": "array", "items": { "type": "string" }, "maxItems": 8 },
    "evidence": { "type": "array", "items": { "type": "string" }, "maxItems": 10 }
  },
  "required": ["headline","full_text"]
}"""

def build_summarize_prompt(clean: str, *, extra_instruction: Optional[str] = None) -> str:
    lines = [
        "Ты — редактор новостных дайджестов. Верни СТРОГИЙ JSON по схеме ниже.",
        "Требования:",
        "- topic: policy | trading | personal_finance | infosec.",
        "- headline: 80–120 символов, фактологично, без кликбейта.",
        "- summary: 2–4 предложения, факты и последствия.",
        "- fingerprint: hex (8–64 символа), стабилен для этой новости.",
        "- evidence: массив ссылок или пустой массив [].",
        "- version: можно 'V2'.",
        "",
        "СОХРАНИ ПОРЯДОК ПОЛЕЙ КАК В СХЕМЕ. ВЕРНИ ТОЛЬКО JSON.",
        "",
        "JSON-схема:",
        _SUMMARIZE_SCHEMA,
        "",
        "Текст для анализа:",
        '"""',
        clean,
        '"""',
    ]
    if extra_instruction:
        lines.append(extra_instruction)
    return "\n".join(lines)

def build_full_prompt(clean: str, *, evidence: Optional[List[str]] = None, extra_instruction: Optional[str] = None) -> str:
    lines = [
        "Собери расширенный материал. Верни СТРОГИЙ JSON по схеме ниже.",
        "Требования:",
        "- headline: 80–120 символов, фактологично.",
        "- full_text: 800–1200 символов: событие → последствия → что это значит.",
        "- highlights: 3–6 тезисов.",
        "- evidence: переданные ссылки и/или явно упомянутые источники.",
        "",
        "СОХРАНИ ПОРЯДОК ПОЛЕЙ КАК В СХЕМЕ. ВЕРНИ ТОЛЬКО JSON.",
        "",
        "JSON-схема:",
        _FULL_SCHEMA,
        "",
        "Текст для анализа:",
        '"""',
        clean,
        '"""',
    ]
    if evidence:
        lines.append("Источники (включи их в evidence):")
        lines.extend(f"- {e}" for e in evidence)
    if extra_instruction:
        lines.append(extra_instruction)
    return "\n".join(lines)
