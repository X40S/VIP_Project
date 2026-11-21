"""Экспорт JSON Schema файла analysis_results.json из Pydantic-моделей.

Запуск: python -m scripts.export_schema
"""
import json
from pathlib import Path

from app.schemas import ImportFile

OUT = Path(__file__).resolve().parent.parent / "schema" / "analysis_results.schema.json"


def build() -> str:
    return json.dumps(ImportFile.model_json_schema(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"Записано: {OUT}")
