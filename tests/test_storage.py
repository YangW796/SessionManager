from datetime import datetime
import json
from pathlib import Path
import shutil

import pytest

from conftest import CHILD, FIXTURES, REVERT, SID
from sessionmanager.compression import text_lines
from sessionmanager.manager import delete_session, delete_sessions, deletion_blockers, discover
from sessionmanager.providers import CodexProvider, KimiProvider, _exchange


def test_official_home_and_override_precedence(tmp_path, monkeypatch, rollout, kimi_session):
    for variable in ('SESSIONMANAGER_CODEX_ROOTS','SESSIONMANAGER_KIMI_ROOTS'):
        monkeypatch.delenv(variable, raising=False)
    c, k = tmp_path/'c', tmp_path/'k'
    monkeypatch.setenv('CODEX_HOME',str(c)); monkeypatch.setenv('KIMI_CODE_HOME',str(k))
    rollout(c/'sessions'); kimi_session(k/'sessions')
    assert len(CodexProvider().discover()) == len(KimiProvider().discover()) == 1
    monkeypatch.setenv('SESSIONMANAGER_CODEX_ROOTS',str(tmp_path/'empty-c'))
    monkeypatch.setenv('SESSIONMANAGER_KIMI_ROOTS',str(tmp_path/'empty-k'))
    assert not CodexProvider().discover() and not KimiProvider().discover()


def test_codex_metadata_preview_and_exact_sidecars(storage, rollout):
    c, _ = storage
    path = rollout(c)
    lock = Path(str(path)+'.lock'); lock.write_text('lock')
    unrelated = c/f'notes-{SID}.txt'; unrelated.write_text('keep')
    unknown = c/f'backup-{SID}.jsonl'; unknown.write_text(path.read_text())
    session, = discover('codex')
    assert set(session.files) == {path, lock}
    assert session.prompt == '检查这个项目，并保留完整的问题。\nSecond line.'
    assert session.response == '完整回答。\nI will inspect the project.'
    assert session.started_at == datetime(2026,10,1,12)
    result = delete_session(session)
    assert not result.failed and unrelated.exists() and unknown.exists() and c.exists()


def test_compressed_rollout_and_duplicate_representation(storage, rollout):
    c, _ = storage
    compressed = rollout(c,compressed=True)
    try:
        session, = discover('codex')
    except ValueError:
        pytest.fail('Compressed fixture not discovered')
    assert session.prompt.startswith('检查') and session.files == (compressed,)
    plain = rollout(c)
    session, = discover('codex')
    assert set(session.files) == {plain,compressed}
    assert not delete_session(session).failed
    assert not plain.exists() and not compressed.exists()


def test_zstd_concatenated_frames_and_truncation(tmp_path):
    path = tmp_path/'concat.jsonl.zst'
    data = (FIXTURES/'codex-rollout.jsonl.zst').read_bytes()
    path.write_bytes(data+data)
    assert len(list(text_lines(path))) == 8
    path.write_bytes(data[:-10])
    with pytest.raises(OSError):
        list(text_lines(path))


def test_missing_decoder_blocks_codex_deletion(storage, rollout, monkeypatch):
    import sessionmanager.compression as compression
    c, _ = storage
    ordinary = rollout(c/'one',sid=CHILD)
    rollout(c/'two',compressed=True)
    def unavailable(path):
        raise OSError('decoder unavailable')
        yield b''
    monkeypatch.setattr(compression,'_chunks',unavailable)
    with pytest.warns(RuntimeWarning):
        session, = discover('codex')
    assert session.blocked_reasons
    with pytest.warns(RuntimeWarning):
        result=delete_session(session)
    assert result.failed and ordinary.exists()


def test_cross_project_archived_reference_blocks_parent(storage, rollout):
    c, _ = storage
    parent=rollout(c)
    archive=c.parent/'archived_sessions'
    child=rollout(archive, sid=CHILD, base=SID, cwd='/another-project')
    sessions=discover('codex'); original=next(s for s in sessions if s.session_id==SID)
    assert any(s.archived for s in sessions)
    result=delete_session(original)
    assert result.failed and 'Referenced by' in result.failed[0][1]
    assert parent.exists() and child.exists()


def test_reverted_rollout_reference_and_batch_order(storage, rollout):
    c, _ = storage
    old=rollout(c)
    new=rollout(c,rollout_id=REVERT,base=SID)
    child=rollout(c,sid=CHILD,base=REVERT)
    inventory=discover('codex'); parent=next(s for s in inventory if s.session_id==SID)
    assert set(parent.rollout_ids)=={SID,REVERT}
    assert len(parent.files)==2
    assert delete_session(parent).failed
    results=delete_sessions(inventory,inventory)
    assert [r.session.session_id for r in results]==[CHILD,SID]
    assert all(not r.failed for r in results)
    assert not any(p.exists() for p in (old,new,child))


def test_failed_child_keeps_parent(storage, rollout, monkeypatch):
    c, _ = storage
    parent=rollout(c);child=rollout(c,sid=CHILD,base=SID)
    inventory=discover('codex')
    unlink=Path.unlink
    def locked(path,*args,**kwargs):
        if path==child:
            raise PermissionError('locked')
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',locked)
    results=delete_sessions(inventory,inventory)
    assert all(r.failed for r in results)
    assert parent.exists() and child.exists()


def test_cycles_refused_before_mutation(storage, rollout):
    c, _ = storage
    a=rollout(c,base=CHILD);b=rollout(c,sid=CHILD,base=SID)
    inventory=discover('codex')
    assert len(deletion_blockers(inventory,inventory))==2
    assert all(r.failed for r in delete_sessions(inventory,inventory))
    assert a.exists() and b.exists()


def test_new_reference_since_selection_prevents_delete(storage, rollout):
    c, _ = storage
    parent=rollout(c);session,=discover('codex')
    rollout(c,sid=CHILD,base=SID)
    assert delete_session(session).failed and parent.exists()


def test_modified_or_replaced_file_refused(storage, rollout):
    c, _ = storage
    path=rollout(c);session,=discover('codex')
    path.write_text(path.read_text()+'{}\n')
    assert delete_session(session).failed and path.exists()


def test_kimi_complete_file_set_main_preview_utc_and_deletion(storage, kimi_session):
    _, k=storage
    owner=kimi_session(k)
    session,=discover('kimi')
    assert session.cwd=='/work' and session.title=='Project review'
    assert session.prompt=='Main question\n第二行'
    assert session.response=='Main complete answer\n完整回答'
    assert session.updated_at==datetime(2026,10,2,12)
    assert set(session.files)==set(p for p in owner.rglob('*') if p.is_file())
    global_index=k.parent/'session_index.jsonl';global_index.write_text('keep')
    result=delete_session(session)
    assert not result.failed and not owner.exists()
    assert owner.parent.exists() and global_index.exists()


def test_kimi_new_task_since_selection_refused(storage,kimi_session):
    _, k=storage;owner=kimi_session(k);session,=discover('kimi')
    (owner/'tasks'/'new.json').write_text('{}')
    result=delete_session(session)
    assert result.failed and (owner/'state.json').exists()


def test_kimi_duplicate_copies_prune_both_owners(storage,kimi_session):
    _, k=storage;one=kimi_session(k);two=k/'copy'/one.name
    shutil.copytree(one,two)
    session,=discover('kimi')
    assert len(session.owned_dirs)==2
    assert not delete_session(session).failed
    assert not one.exists() and not two.exists() and two.parent.exists()


def test_kimi_bad_metadata_and_uuid_names_skipped(storage,kimi_session):
    _, k=storage;owner=kimi_session(k)
    (owner/'state.json').write_text('[]')
    assert discover('kimi')==[]
    (owner/'state.json').write_text(json.dumps({'id':'session_'+CHILD}))
    assert discover('kimi')==[]
    (owner/'state.json').write_text('{bad')
    with pytest.warns(RuntimeWarning):
        assert discover('kimi')==[]


def test_unknown_files_and_bad_records_do_not_crash(storage,rollout):
    c, _=storage;path=rollout(c)
    lines=path.read_text().splitlines()
    path.write_text('\n'.join([lines[0],'[]','null','{broken',*lines[1:]])+'\n')
    session,=discover('codex')
    assert session.prompt and session.response
    junk=c/f'rollout-{CHILD}.jsonl';junk.write_text('{broken')
    with pytest.warns(RuntimeWarning):
        session,=discover('codex')
    assert junk not in session.files and session.blocked_reasons


def test_kimi_invalid_timestamp_is_ignored(storage,kimi_session):
    _, k=storage;owner=kimi_session(k)
    state=json.loads((owner/'state.json').read_text());state['createdAt']=1e100;state['updatedAt']='bad'
    (owner/'state.json').write_text(json.dumps(state))
    session,=discover('kimi')
    assert session.started_at is None and session.updated_at is None


def test_symlink_files_directories_and_swapped_parent(storage,kimi_session,tmp_path):
    _, k=storage;owner=kimi_session(k)
    outside=tmp_path/'outside';outside.mkdir();target=outside/'keep';target.write_text('keep')
    (owner/'link').symlink_to(target)
    (owner/'linked-dir').symlink_to(outside,target_is_directory=True)
    session,=discover('kimi')
    assert target not in session.files and all(not p.is_symlink() for p in session.files)
    assert not delete_session(session).failed
    assert target.exists() and owner.exists()  # links keep the owner non-empty


def test_parent_swapped_for_symlink_refused(storage,kimi_session,tmp_path):
    _, k=storage;owner=kimi_session(k);session,=discover('kimi')
    moved=tmp_path/'moved';owner.rename(moved);owner.symlink_to(moved,target_is_directory=True)
    assert delete_session(session).failed
    assert (moved/'state.json').exists()


def test_streaming_preview_joins_text_and_skips_nonobjects(tmp_path):
    path=tmp_path/'wire.jsonl'
    records=[json.loads(x) for x in (FIXTURES/'kimi-main-wire.jsonl').read_text().splitlines()]
    records=records[:-1]+[{'type':'turn.ended'}]
    path.write_text('[]\n'+ '\n'.join(json.dumps(x) for x in records))
    assert _exchange(path)==('Main question\n第二行','Partial answer')


def test_kimi_legacy_field_names_and_iso_time(storage,kimi_session):
    _, k=storage;owner=kimi_session(k)
    path=owner/'state.json';state=json.loads(path.read_text())
    state['workDir']=state.pop('cwd');state['customTitle']=state.pop('title')
    state['createdAt']='2026-10-01T20:00:00+08:00';state['updatedAt']='2026-10-02T20:00:00+08:00'
    path.write_text(json.dumps(state,indent=2))
    session,=discover('kimi')
    assert session.cwd=='/work' and session.title=='Project review'
    assert session.started_at==datetime(2026,10,1,12) and session.updated_at==datetime(2026,10,2,12)


def test_malformed_record_types_skipped(storage,rollout):
    c, _=storage;path=rollout(c);lines=path.read_text().splitlines()
    path.write_text('\n'.join([lines[0],'{"type":[]}','{"payload":{"type":{}}}',*lines[1:]]))
    session,=discover('codex')
    assert session.prompt and session.response


def test_unreadable_archive_blocks_deletion(storage,rollout,monkeypatch):
    import sessionmanager.providers as providers
    c, _=storage;path=rollout(c)
    walk=providers.walk_files
    def denied(root):
        if root.name=='archived_sessions':
            raise PermissionError('archive not readable')
        yield from walk(root)
    monkeypatch.setattr(providers,'walk_files',denied)
    with pytest.warns(RuntimeWarning):
        session,=discover('codex')
    assert 'archive not readable' in session.blocked_reasons[0]
    with pytest.warns(RuntimeWarning):
        assert delete_session(session).failed
    assert path.exists()


def test_uuid_substring_directories_are_not_sessions(storage,kimi_session):
    _, k=storage;owner=kimi_session(k)
    state=owner/'state.json';data=json.loads(state.read_text());data['id']=owner.name+'-backup'
    state.write_text(json.dumps(data));owner.rename(owner.with_name(owner.name+'-backup'))
    assert discover('kimi')==[]
