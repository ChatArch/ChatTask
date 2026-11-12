"""Synthetic native-event fixtures; no model calls are made in these unit tests."""
import json
import importlib.util
import pytest


def converter():
    spec=importlib.util.find_spec('chattask.task_trajectories')
    assert spec is not None,'ATIF conversion is not implemented'
    from chattask.task_trajectories import codex_to_atif
    return codex_to_atif


def test_atif_preserves_actual_commands_observations_and_messages():
    native=[{'type':'thread.started','thread_id':'native-session'},
            {'type':'item.completed','item':{'type':'command_execution','id':'item_1',
                                            'command':'python main.py','aggregated_output':'42\n','exit_code':0}},
            {'type':'item.completed','item':{'type':'agent_message','text':'The output is 42.'}},
            {'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':10}}]
    result=converter()('\n'.join(json.dumps(e) for e in native),'Run the program',model='configured-model',version='observed-version')
    assert result['session_id']=='native-session'
    assert result['agent']['version']=='observed-version'
    assert result['steps'][1]['tool_calls'][0]['arguments']['command']=='python main.py'
    assert result['steps'][1]['observation']['results'][0]['content']=='42\n'
    assert result['steps'][2]['message']=='The output is 42.'
    assert [s['step_id'] for s in result['steps']]==[1,2,3]


def test_failed_native_turn_does_not_create_an_assistant_success():
    events='\n'.join(json.dumps(e) for e in [{'type':'thread.started','thread_id':'session-failed'},
                                          {'type':'turn.failed','error':{'message':'Connection failed'}}])
    result=converter()(events,'Do the task')
    assert len(result['steps'])==1
    assert result['extra']['native_terminal']==['turn.failed']
    assert result['agent']['version']=='unrecorded'


def test_no_events_cannot_be_fabricated_into_a_trajectory():
    with pytest.raises(ValueError):converter()('', 'Task')
