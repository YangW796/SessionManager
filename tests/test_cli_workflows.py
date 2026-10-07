import json
import sys
from unittest.mock import patch

import pytest

from conftest import CHILD, SID
from sessionmanager.cli import _BACK, _indexes, _select_project, _select_sessions, main
from sessionmanager.manager import discover


def test_json_listing_complete_previews_and_details(storage,rollout,capsys):
    c, _=storage;rollout(c)
    assert main(['--platform','codex','--project','/work','--json'])==0
    rows=json.loads(capsys.readouterr().out)
    assert '\n' in rows[0]['first_prompt'] and rows[0]['id']==SID
    assert main(['--platform','codex','--project','/work','--details',SID,'--json'])==0
    detail=json.loads(capsys.readouterr().out)
    assert detail==rows[0]


def test_json_dry_run_full_file_list_and_real_delete(storage,kimi_session,capsys):
    _, k=storage;owner=kimi_session(k)
    args=['--platform','kimi','--project','/work','--delete-id','session_'+SID,'--json']
    assert main(args+['--dry-run'])==0
    plan=json.loads(capsys.readouterr().out)
    assert plan['status']=='planned' and plan['file_count']==9
    assert len(plan['sessions'][0]['files'])==9 and plan['bytes']>0
    assert owner.exists()
    assert main(args+['--yes'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='deleted' and len(result['results'][0]['deleted'])==9
    assert not owner.exists()


def test_json_external_reference_blocked(storage,rollout,capsys):
    c, _=storage;parent=rollout(c);rollout(c,sid=CHILD,base=SID,cwd='/other')
    assert main(['--platform','codex','--project','/work','--delete-id',SID,'--json','--yes'])==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='blocked' and CHILD in result['blockers'][0]['reasons'][0]
    assert parent.exists()


def test_repeat_ids_batch_delete(storage,rollout,capsys):
    c, _=storage;parent=rollout(c);child=rollout(c,sid=CHILD,base=SID)
    assert main(['--platform','codex','--project','/work','--delete-id',SID,'--delete-id',CHILD,'--delete-id',SID,'--json','--yes'])==0
    result=json.loads(capsys.readouterr().out)
    assert len(result['sessions'])==2 and not parent.exists() and not child.exists()


@pytest.mark.parametrize('args',[
    ['--delete','1'],
    ['--platform','codex','--delete','1'],
    ['--project','/work'],
    ['--platform','bogus'],
    ['--page-size','0'],
    ['--platform','codex','--project','/work','--delete','999'],
    ['--platform','codex','--project','/work','--delete-id','missing'],
    ['--platform','codex','--project','/work','--delete','1'],
])
def test_script_errors_remain_json(storage,rollout,capsys,args):
    c, _=storage;path=rollout(c)
    assert main(args+['--json'])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='error' and result['exit_code']==2
    assert path.exists()


def test_no_stdin_confirmation_in_script(storage,rollout,capsys,monkeypatch):
    c, _=storage;path=rollout(c)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    with patch('builtins.input',side_effect=AssertionError('must not prompt')):
        assert main(['--platform','codex','--project','/work','--delete','1'])==2
    assert path.exists()


def test_negative_project_rejected(storage,rollout,monkeypatch,capsys):
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    with patch('builtins.input',side_effect=['-1','2']):
        assert _select_project([('/a',['a']),('/b',['b']),('/c',['c'])],None,False)==['b']
    assert 'Invalid project' in capsys.readouterr().err


@pytest.mark.parametrize('value',['0','-1','2-1','1,','1,4-2','1-100000000000000000000000000000',''])
def test_invalid_ranges_rejected_without_expansion(value):
    with pytest.raises(ValueError):
        _indexes(value,3)


def test_navigation_back_returns_one_level_and_cancel_stays(storage,rollout,monkeypatch,capsys):
    c, _=storage;path=rollout(c)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    # platform -> project -> sessions -> projects -> sessions -> cancel deletion -> sessions -> quit
    with patch('builtins.input',side_effect=['1','1','0','1','d 1','n','q']) as inputs:
        assert main([])==0
    prompts=[call.args[0] for call in inputs.call_args_list]
    assert sum(prompt.startswith('Select platform') for prompt in prompts)==1
    assert sum(prompt.startswith('Project number') for prompt in prompts)==2
    assert sum(prompt.startswith('Number/v') for prompt in prompts)==3
    assert path.exists() and 'Cancelled.' in capsys.readouterr().out


def test_explicit_platform_back_returns_projects(storage,rollout,monkeypatch):
    c, _=storage;rollout(c)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    with patch('builtins.input',side_effect=['1','0','q']) as inputs:
        assert main(['--platform','codex'])==0
    assert sum(call.args[0].startswith('Project number') for call in inputs.call_args_list)==2


def test_successful_delete_refreshes_current_project(storage,rollout,monkeypatch):
    c, _=storage;a=rollout(c);b=rollout(c,sid=CHILD)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    with patch('builtins.input',side_effect=['d 1','y','q']):
        assert main(['--platform','codex','--project','/work'])==0
    assert sum(p.exists() for p in (a,b))==1


def test_details_search_paging_do_not_delete(storage,rollout,capsys):
    c, _=storage;a=rollout(c);b=rollout(c,sid=CHILD)
    sessions=discover('codex')
    with patch('builtins.input',side_effect=['n','1','/'+SID,'d 1','/','q']):
        # index 1 is CHILD (later mtime); search SID filters it out, rejecting d 1.
        assert _select_sessions(sessions,page_size=1) is None
    output=capsys.readouterr()
    assert 'page 2/2' in output.out and 'First prompt:' in output.out
    assert 'outside the current search' in output.err
    assert a.exists() and b.exists()


def test_project_search_and_paging(monkeypatch):
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    with patch('builtins.input',side_effect=['n','/third','1']):
        assert _select_project([('/first',['a']),('/second',['b']),('/third',['c'])],None,False,page_size=1)==['c']


def test_eof_and_interrupt_are_clean(storage,rollout,monkeypatch,capsys):
    c, _=storage;rollout(c)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    with patch('builtins.input',side_effect=EOFError):
        assert main([])==0
    with patch('builtins.input',side_effect=KeyboardInterrupt):
        assert main([])==130
    assert 'Interrupted.' in capsys.readouterr().err


def test_unknown_project_script_selection(storage,rollout,capsys):
    c, _=storage;path=rollout(c)
    data=path.read_text().replace('"cwd": "/work"','"cwd": null')
    path.write_text(data)
    assert main(['--platform','codex','--project','(unknown project)','--json'])==0
    assert json.loads(capsys.readouterr().out)[0]['cwd'] is None


def test_json_partial_failure(storage,kimi_session,monkeypatch,capsys):
    from pathlib import Path
    _, k=storage;owner=kimi_session(k)
    unlink=Path.unlink
    def fail_state(path,*args,**kwargs):
        if path.name=='state.json':
            raise PermissionError('locked')
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail_state)
    assert main(['--platform','kimi','--project','/work','--delete','1','--json','--yes'])==1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='failed' and result['results'][0]['failed'][0]['reason']=='locked'
    assert (owner/'state.json').exists()


def test_search_and_sort_apply_before_script_indexes(storage,rollout,capsys):
    c, _=storage;rollout(c);rollout(c,sid=CHILD)
    assert main(['--platform','codex','--project','/work','--json','--search',SID,'--sort','size'])==0
    rows=json.loads(capsys.readouterr().out)
    assert [row['id'] for row in rows]==[SID]
    assert main(['--platform','codex','--project','/work','--json','--search',SID,'--delete','1','--dry-run'])==0
    plan=json.loads(capsys.readouterr().out)
    assert plan['sessions'][0]['id']==SID


def test_empty_project_after_delete_returns_project_list(storage,rollout,monkeypatch):
    c, _=storage;path=rollout(c)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    with patch('builtins.input',side_effect=['d 1','y','q']) as inputs:
        assert main(['--platform','codex','--project','/work'])==0
    assert inputs.call_args_list[-1].args[0].startswith('Project number')
    assert not path.exists()


def test_explicit_project_without_platform_is_honored_interactively(storage,rollout,monkeypatch):
    c, _=storage;rollout(c)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    with patch('builtins.input',side_effect=['1','q']) as inputs:
        assert main(['--project','/work'])==0
    assert not any(call.args[0].startswith('Project number') for call in inputs.call_args_list)
