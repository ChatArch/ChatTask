"""Conversation/automation submission through the existing site login and HTTP API.

No direct store access, owner impersonation, credential export or automatic retry
of an uncertain mutation. The caller supplies a stable message-derived request ID.
"""
from __future__ import annotations
import ipaddress,json,re
from urllib.parse import urlsplit
import httpx

class IntakeUnknown(RuntimeError):
    """A submission may have committed; reconcile with the same request identity."""


def submit_task(settings,body,*,client=None):
    origin=settings.origin.rstrip('/')
    parsed=urlsplit(origin)
    loopback=parsed.hostname=='localhost'
    try:loopback=loopback or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:pass
    if parsed.scheme!='https' and not loopback:raise ValueError('HTTPS required for site credentials outside loopback')
    if not settings.username or not settings.password:raise ValueError('configured site login required')
    if not isinstance(body,dict) or not isinstance(body.get('request_id'),str):raise ValueError('stable request_id required')
    owned=client is None
    client=client or httpx.Client(base_url=origin,timeout=30,follow_redirects=False)
    csrf=None
    try:
        try:
            login=client.post(origin+'/auth/login',headers={'Origin':origin},json={'username':settings.username,'password':settings.password,'next':'/'},follow_redirects=False)
            if login.status_code!=200:raise ValueError('site login rejected')
            value=login.json()
            if value.get('authenticated') is not True or not isinstance(value.get('csrf_token'),str):raise ValueError('site login did not return an authenticated session')
            csrf=value['csrf_token']
        except httpx.HTTPError as exc:raise ValueError('site login unavailable; no task submission attempted') from None
        try:
            response=client.post(origin+'/api/task-intake',headers={'Origin':origin,'X-CSRF-Token':csrf},json=body,follow_redirects=False)
            if response.status_code>=500:raise IntakeUnknown('Task outcome unknown; reconcile using the same request_id and unchanged input')
            if response.status_code!=200:raise ValueError('task submission rejected with HTTP '+str(response.status_code))
            receipt=response.json();ident=receipt.get('task_id');kind=receipt.get('kind')
            if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident) or kind not in ('durable','python-package'):
                raise IntakeUnknown('Task receipt is unverified; reconcile using the same request_id')
            expected=origin+('/#package='+ident if kind=='python-package' else '/?task='+ident)
            if receipt.get('url')!=expected:raise IntakeUnknown('Task receipt URL is unverified; reconcile using the same request_id')
            return receipt
        except (httpx.HTTPError,json.JSONDecodeError) as exc:
            raise IntakeUnknown('Task outcome unknown; reconcile using the same request_id and unchanged input') from None
    finally:
        if csrf:
            try:client.post(origin+'/auth/logout',headers={'Origin':origin,'X-CSRF-Token':csrf},json={},follow_redirects=False)
            except httpx.HTTPError:pass
        if owned:client.close()
