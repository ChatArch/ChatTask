"""The whole model transfer is bounded, independently of per-read timeouts."""
import asyncio
import inspect
import json
import time
import httpx
import pytest
from chattask.workspace_analysis import OpenAIChatDriver, AnalysisUnavailable


def profile(_):
    return {'OPENAI_API_BASE': 'https://model.example.invalid/v1',
            'OPENAI_API_KEY': 'synthetic-key', 'OPENAI_API_MODEL': 'fixture-model'}


def test_total_deadline_covers_stalled_response_body_and_closes_stream():
    assert 'timeout' in inspect.signature(OpenAIChatDriver).parameters
    closed = []
    class Stall(httpx.AsyncByteStream):
        async def __aiter__(self):
            event = {'model': 'fixture-model', 'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None}]}
            yield ('data: ' + json.dumps(event) + '\n\n').encode()
            await asyncio.Event().wait()
        async def aclose(self):
            closed.append(True)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Stall()))
    driver = OpenAIChatDriver('fixture', profile_loader=profile, transport=transport, timeout=0.02)
    body = {'title': 'x', 'instructions': 'x', 'acceptance': 'x', 'source': {}, 'context': []}
    start = time.monotonic()
    with pytest.raises(AnalysisUnavailable):
        asyncio.run(driver.analyze(body))
    assert time.monotonic() - start < 3
    assert closed == [True]


def test_production_default_budget_and_invalid_override():
    assert 'timeout' in inspect.signature(OpenAIChatDriver).parameters
    assert OpenAIChatDriver('fixture').timeout == 180
    for value in (0, -1, 301, True, '30'):
        with pytest.raises(ValueError):
            OpenAIChatDriver('fixture', timeout=value)
