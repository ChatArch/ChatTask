"""Streaming completion contracts use deterministic transport doubles only."""
import asyncio
import json
import httpx
import pytest
from chattask.workspace_analysis import OpenAIChatDriver, AnalysisUnavailable


def profile(_):
    return {'OPENAI_API_BASE': 'https://model.example.invalid/v1',
            'OPENAI_API_KEY': 'synthetic-model-key', 'OPENAI_API_MODEL': 'fixture-model'}


def task():
    return {'title': 'Review', 'instructions': 'Read supplied material',
            'acceptance': 'State evidence gaps', 'source': {}, 'context': []}


def frame(delta, finish=None, model='fixture-model'):
    return 'data: ' + json.dumps({'model': model, 'choices': [{'delta': delta, 'finish_reason': finish}]}, ensure_ascii=False) + '\n\n'


def test_driver_requests_stream_and_accepts_meaningful_terminal_text():
    seen = []
    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload['stream'])
        assert payload['stream'] is True
        data = frame({'role': 'assistant'}) + frame({'content': '证据'}) + frame({'content': '不足'}, 'stop') + 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=data.encode())
    driver = OpenAIChatDriver('fixture', profile_loader=profile, transport=httpx.MockTransport(handler))
    text, model = asyncio.run(driver.analyze(task()))
    assert text == '证据不足' and model == 'fixture-model' and seen == [True]


@pytest.mark.parametrize('body', [
    frame({'content': 'unfinished'}) + 'data: [DONE]\n\n',
    frame({'content': ''}, 'stop'),
    frame({'content': 'partial'}, 'length'),
    frame({'tool_calls': [{'id': 'synthetic'}]}, 'tool_calls'),
    frame({'content': 'first'}) + frame({'content': 'second'}, 'stop', model='other-model'),
])
def test_stream_rejects_missing_terminal_empty_tool_and_model_switch(body):
    driver = OpenAIChatDriver('fixture', profile_loader=profile,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body.encode())))
    with pytest.raises(AnalysisUnavailable):
        asyncio.run(driver.analyze(task()))


def test_nonstreaming_json_response_remains_compatible():
    value = {'model': 'fixture-model', 'choices': [{'finish_reason': 'stop', 'message': {'content': 'provided evidence'}}]}
    driver = OpenAIChatDriver('fixture', profile_loader=profile,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value)))
    assert asyncio.run(driver.analyze(task())) == ('provided evidence', 'fixture-model')
