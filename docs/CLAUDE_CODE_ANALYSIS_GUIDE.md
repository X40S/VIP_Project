# Подготовка analysis_results.json через Claude Code

Режим B: вы анализируете данные в Claude Code, а приложение только проверяет и показывает результат. AI API при импорте не вызывается.

## Что дать Claude Code

Откройте папку проекта в Claude Code. Нужны файлы:

- `dialogs.json` и `players.csv` (исходные данные)
- `rules/evaluation_rules.md` (правила, первая строка `RULES_VERSION: vN`)
- `rules/safety_baseline.md` (обязательные меры безопасности, приоритет выше правил)
- `schema/analysis_results.schema.json` (JSON Schema результата)
- `docs/analysis_results.example.json` (пример корректного файла)

## Промпт для Claude Code

```
Проанализируй все диалоги из dialogs.json с учётом данных игроков из players.csv.
Следуй правилам из rules/evaluation_rules.md и обязательным мерам из rules/safety_baseline.md.
Результат запиши в analysis_results.json строго по schema/analysis_results.schema.json, формат как в docs/analysis_results.example.json.

Требования:
- schema_version: 2. rules_version: значение после RULES_VERSION в первой строке rules/evaluation_rules.md (например, "v3").
- source: {"type": "claude_code", "model": "<название модели, которой ты работаешь>"}.
- На каждый диалог один элемент results: dialog_id, player_id и manager_id берутся из данных без изменений (manager_id это значение поля manager).
- message_id и ссылки на сообщения имеют вид "D001#3": номер сообщения в диалоге, нумерация с 1 в порядке массива messages.
- Для каждого сообщения игрока верни точку в player_points, для каждого сообщения менеджера точку в manager_points.
- Оценки целые 0-100. Критерий, не применимый к диалогу, равен null. Не выдумывай данные и нарушения, пустой список нарушений допустим.
- Тексты короткие: summary не более 2 предложений, explanation одно предложение, рекомендаций не больше трёх.
- Не используй длинное тире. Не запускай внешние API.
После записи файла проверь его валидность по схеме.
```

## Формат файла

Верхний уровень: `schema_version`, `rules_version`, `source`, `generated_at` (необязательно), `results`.

Элемент `results`: `dialog_id`, `player_id`, `manager_id`, `analysis`.

`analysis`:

| Поле | Содержимое |
|---|---|
| `profile.summary` | 1-2 коротких предложения о профиле игрока или null |
| `quality` | `tone`, `empathy`, `engagement`, `personalization`, `problem_understanding`, `completeness`, `follow_up`, `next_steps_clarity` (0-100 или null), `overall` (0-100), `summary` |
| `player_points` | `message_id`, `dissatisfaction` (0-100), `state`: neutral, irritation, dissatisfaction, disappointment, detachment, positive, formal_agreement, resolved |
| `manager_points` | `message_id`, `engagement` (0-100) |
| `rg` | `signal`: none, self_exclusion_request, loss_of_control, serious_harm, self_harm_risk, ambiguous_distress; `handled`: yes, partly, no, not_applicable; `message_refs` |
| `risk_flags` | chargeback_threat, churn_threat |
| `violations` | `title`, `category` (rg_safety, operational, communication), `code`, `severity` (low, medium, high, critical), `explanation`, `message_refs` |
| `recommendations` | `text`, `kind` (protective, service, commercial, coaching), `message_refs` |

Приоритет диалога заполнять не нужно: его рассчитывает приложение одинаково для обоих режимов.

## Импорт в приложении

Анализ чатов, режим «Импорт анализа», выбрать `analysis_results.json`, «Импортировать».

- Файл проверяется по схеме, идентификаторы игроков, диалогов и менеджеров сверяются с загруженными данными, ссылки на сообщения проверяются. Некорректные результаты пропускаются с указанием причины, остальные импортируются.
- Результаты API не заменяются, пока не включена опция «Заменить результаты API». Заменённый результат сохраняется в истории.
- Если `rules_version` файла отличается от текущей версии правил, результаты показываются с отметкой «Правила изменены». Оценки не пересчитываются.
