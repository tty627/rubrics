"""Stage 01: normalize task-only input without importing rubrics or candidate answers."""
import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from lib import stage, task_input, xlsx

XLSX = Path(os.environ.get('RP_XLSX', ROOT / 'data' / 'input.xlsx'))
INPUT_JSONL = os.environ.get('RP_INPUT_JSONL', '')
SOURCE_JSONL = os.environ.get('RP_SOURCE_JSONL', '')
OUT = 'data/tasks/01_task_dataset.jsonl'
QUESTION_COL = 3
SUBJECT_COL = 4


def normalize_subject(value):
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
        return [value.strip()] if value.strip() else []
    return [str(value)]


def task_record(raw, index, source_index, source_required=False):
    messages = task_input.task_messages(
        raw.get('task_messages') or raw.get('messages') or [])
    question = str(raw.get('question') or task_input.user_task_text(messages) or '').strip()
    if not question:
        raise ValueError(f'输入第 {index + 1} 条缺少 question/user message')
    record = {
        'rid': str(raw.get('rid') or f'q{index + 1:04d}'),
        'question': question,
        'subject': normalize_subject(raw.get('subject')),
    }
    if raw.get('xlsx_row') is not None:
        record['xlsx_row'] = raw['xlsx_row']
    if messages:
        record['task_messages'] = messages
        record['source_session_id'] = str(raw.get('source_session_id', ''))
        record['task_message_status'] = 'embedded'
        record['system_messages'] = [m['content'] for m in messages
                                     if m['role'] in ('system', 'developer')]
        record['user_messages'] = [m['content'] for m in messages if m['role'] == 'user']
        return record

    record = task_input.attach_source_messages(record, source_index)
    if source_required and record['task_message_status'] != 'matched':
        raise ValueError(
            f'{record["rid"]} 无法唯一匹配 RP_SOURCE_JSONL：'
            f'{record["task_message_status"]}')
    return record


def read_jsonl(path, source_index):
    target = Path(path)
    if not target.is_absolute():
        target = ROOT / target
    with target.open(encoding='utf-8') as stream:
        raw = [json.loads(line) for line in stream if line.strip()]
    return [task_record(record, index, source_index, bool(SOURCE_JSONL))
            for index, record in enumerate(raw)]


def read_xlsx(source_index):
    rows = xlsx.read(XLSX)
    if not rows:
        raise ValueError(f'{XLSX} 没有数据')
    records = []
    for index, row in enumerate(rows[1:]):
        raw = {
            'rid': f'q{index + 2:04d}',
            'xlsx_row': index + 2,
            'question': row.get(QUESTION_COL, ''),
            'subject': row.get(SUBJECT_COL, ''),
        }
        records.append(task_record(raw, index, source_index, bool(SOURCE_JSONL)))
    return records


def validate(records):
    ids = [record['rid'] for record in records]
    duplicates = sorted({rid for rid in ids if ids.count(rid) > 1})
    if duplicates:
        raise ValueError(f'重复 rid: {duplicates[:10]}')
    forbidden = {'draft_rubric', 'rubrics', 'ref_responses', 'ref_errors',
                 'answer', 'answer_canonical', 'anchors', 'pool', 'judged'}
    leaked = [(record['rid'], sorted(forbidden & set(record))) for record in records
              if forbidden & set(record)]
    if leaked:
        raise ValueError(f'Stage 01 出现禁止字段: {leaked[:5]}')


def main():
    source_index = task_input.load_source_index(SOURCE_JSONL)
    records = (read_jsonl(INPUT_JSONL, source_index)
               if INPUT_JSONL else read_xlsx(source_index))
    validate(records)
    stage.write_jsonl(OUT, records)
    statuses = {}
    for record in records:
        status = record['task_message_status']
        statuses[status] = statuses.get(status, 0) + 1
    source = INPUT_JSONL or str(XLSX)
    print(f'Stage 01: {len(records)} 条任务 <- {source}')
    print(f'消息来源: {statuses}')
    print('rubric/候选回答字段: 0（生成输入仅含题目与题面约束）')


if __name__ == '__main__':
    main()
