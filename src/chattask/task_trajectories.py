"""Normalize observable Codex exec events, without inventing missing context."""
from __future__ import annotations
import hashlib
import json


def codex_to_atif(events, instructions, *, model=None, version=None):
    if not isinstance(events, str) or not events.strip() or len(events.encode()) > 2 * 1024 * 1024:
        raise ValueError('bounded native event text required')
    steps = [{'step_id': 1, 'source': 'user', 'message': instructions}]
    session = None
    terminal = []
    malformed = 0
    observed_ids = set()
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        if event.get('type') == 'thread.started':
            ident = event.get('thread_id')
            if not isinstance(ident, str) or not ident or session not in (None, ident):
                raise ValueError('conflicting native session identity')
            session = ident
        if event.get('type') in ('turn.completed', 'turn.failed'):
            terminal.append(event['type'])
        if event.get('type') != 'item.completed' or not isinstance(event.get('item'), dict):
            continue
        item = event['item']
        if item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            steps.append({'step_id': len(steps) + 1, 'source': 'agent', 'message': item['text']})
        elif item.get('type') == 'command_execution':
            ident, command = item.get('id'), item.get('command')
            if not isinstance(ident, str) or not isinstance(command, str):
                malformed += 1
                continue
            if ident in observed_ids:
                raise ValueError('duplicate native tool-call identity')
            observed_ids.add(ident)
            output = item.get('aggregated_output', '')
            if not isinstance(output, str):
                malformed += 1
                continue
            steps.append({'step_id': len(steps) + 1, 'source': 'agent', 'message': '',
                          'tool_calls': [{'tool_call_id': ident, 'function_name': 'shell',
                                          'arguments': {'command': command}}],
                          'observation': {'results': [{'source_call_id': ident, 'content': output}]}})
    if session is None:
        raise ValueError('no observed native session identity')
    agent = {'name': 'codex', 'version': version or 'unrecorded',
             'extra': {'version_observed': bool(version)}}
    if model is not None:
        agent['model_name'] = model
    return {'schema_version': 'ATIF-v1.8', 'session_id': session, 'agent': agent, 'steps': steps,
            'notes': 'Observable exec messages and command results only; hidden context/reasoning is not reconstructed.',
            'extra': {'native_terminal': terminal, 'malformed_or_incomplete_records': malformed,
                      'native_events_sha256': hashlib.sha256(events.encode()).hexdigest(),
                      'coverage': 'observable-exec-events'}}
