import json
from pathlib import Path
import shutil

import pytest

FIXTURES = Path(__file__).parent / 'fixtures'
SID = '123e4567-e89b-12d3-a456-426614174000'
CHILD = '123e4567-e89b-12d3-a456-426614174001'
REVERT = '123e4567-e89b-12d3-a456-426614174002'


@pytest.fixture
def storage(tmp_path, monkeypatch):
    codex = tmp_path / 'codex' / 'sessions'
    kimi = tmp_path / 'kimi' / 'sessions'
    codex.mkdir(parents=True)
    kimi.mkdir(parents=True)
    monkeypatch.setenv('SESSIONMANAGER_CODEX_ROOTS', str(codex))
    monkeypatch.setenv('SESSIONMANAGER_KIMI_ROOTS', str(kimi))
    return codex, kimi


@pytest.fixture
def rollout():
    def make(root, sid=SID, base=None, rollout_id=None, cwd='/work', compressed=False):
        root.mkdir(parents=True, exist_ok=True)
        filename = f'rollout-2026-10-01T12-00-00-{sid}' + (f'_{rollout_id}' if rollout_id else '') + '.jsonl'
        path = root / (filename + ('.zst' if compressed else ''))
        if compressed:
            assert sid == SID and base is None and rollout_id is None and cwd == '/work'
            shutil.copyfile(FIXTURES / 'codex-rollout.jsonl.zst', path)
        else:
            records = [json.loads(line) for line in (FIXTURES / 'codex-rollout.jsonl').read_text().splitlines()]
            records[0]['payload'].update(id=sid, cwd=cwd, history_base={'thread_id':base,'end_ordinal_exclusive':4,'end_byte_offset':300} if base else None)
            path.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in records)+'\n',encoding='utf-8')
        return path
    return make


@pytest.fixture
def kimi_session():
    def make(root, sid=SID):
        owner = root / 'wd_work_123456789abc' / f'session_{sid}'
        owner.mkdir(parents=True)
        metadata = json.loads((FIXTURES / 'kimi-state.json').read_text())
        metadata['id'] = owner.name
        (owner/'state.json').write_text(json.dumps(metadata,indent=2))
        main = owner/'agents'/'main'/'wire.jsonl'
        main.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES/'kimi-main-wire.jsonl',main)
        for name in ('agents/agent-0/wire.jsonl', 'tasks/t1.json', 'tasks/t1/output.log', 'logs/kimi-code.log',
                     'agents/main/plans/p1.md', 'upcoming-goals.json', 'cron/job.json'):
            path = owner/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('{"type":"turn.prompt","input":"Child question"}\n')
        return owner
    return make
