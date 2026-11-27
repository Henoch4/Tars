import json
import pytest
from src.task_tray import Task, TaskTray

def test_append_and_latest(tmp_path):
    tray=TaskTray(tmp_path/'tasks.jsonl'); tray.append(Task('T1','mine data','research'))
    assert tray.latest()['T1'].title == 'mine data'

def test_dependencies_gate_ready(tmp_path):
    tray=TaskTray(tmp_path/'tasks.jsonl'); tray.append(Task('A','a','x',state='queued')); tray.append(Task('B','b','x',state='ready',dependencies=('A',)))
    assert tray.ready() == []
    tray.append(Task('A','a','x',state='complete')); assert [x.task_id for x in tray.ready()] == ['B']

def test_live_tasks_require_approval():
    with pytest.raises(ValueError): Task('L','live','deploy',live_impact=True)

def test_evidence_transition_and_approval_are_append_only(tmp_path):
    tray=TaskTray(tmp_path/'tasks.jsonl'); tray.append(Task('L','deploy','ops',requires_approval=True,live_impact=True))
    tray.attach_evidence('L','reports/freeze.json',sha256='a' * 64); tray.transition('L','review',reason='evidence ready'); tray.approve('L','operator',scope='disabled deployment')
    rows=(tmp_path/'tasks.jsonl').read_text().splitlines(); assert len(rows)==4
    assert tray.latest()['L'].state == 'review'; assert 'reports/freeze.json' in tray.latest()['L'].evidence
