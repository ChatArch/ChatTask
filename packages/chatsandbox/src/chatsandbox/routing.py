"""Strict native request policies and bounded, instance-scoped route capabilities."""
import base64
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl

from .auth import AccessDenied

OWNER = 'chatsandbox-owner'
REQUEST = 'chatsandbox-request'


def identity(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
        raise AccessDenied(404,'sandbox_not_found')
    return value


def metadata_filter(value):
    if not value: return {}
    if len(value)>8192: raise AccessDenied(400,'invalid_metadata_filter')
    try:
        pairs=parse_qsl(value,keep_blank_values=True,strict_parsing=True,max_num_fields=32)
        if len(dict(pairs))!=len(pairs): raise ValueError()
        return dict(pairs)
    except ValueError: raise AccessDenied(400,'invalid_metadata_filter') from None


def _memory_to_mib(value):
    match = re.fullmatch(r'([0-9]+)(Mi|Gi)', str(value))
    if not match:
        raise ValueError()
    return int(match[1]) * (1024 if match[2] == 'Gi' else 1)


def _storage_to_mib(value):
    """Parse Kubernetes ephemeral-storage binary quantities (Mi/Gi only)."""
    match = re.fullmatch(r'([0-9]+)(Mi|Gi)', str(value))
    if not match:
        raise ValueError()
    return int(match[1]) * (1024 if match[2] == 'Gi' else 1)


def create_policy(body, *, max_ttl_seconds=7200, max_storage_mib=None):
    allowed={'image','entrypoint','timeout','resourceLimits','resourceRequests','env','metadata','secureAccess','extensions','networkPolicy','volumes'}
    if not isinstance(body,dict) or set(body)-allowed or any(body.get(k) for k in ['extensions','networkPolicy','volumes']):
        raise AccessDenied(400,'sandbox_feature_not_enabled')
    image=body.get('image')
    if not isinstance(image,dict) or set(image)-{'uri'} or not isinstance(image.get('uri'),str):
        raise AccessDenied(400,'invalid_sandbox_image')
    limits=body.get('resourceLimits')
    allowed_resources={'cpu','memory','ephemeral-storage'}
    if not isinstance(limits,dict) or not {'cpu','memory'} <= set(limits) or not set(limits) <= allowed_resources:
        raise AccessDenied(400,'explicit_sandbox_resources_required')
    requests=body.get('resourceRequests')
    if requests not in (None,limits):
        raise AccessDenied(400,'sandbox_requests_must_match_limits')
    try:
        raw=str(limits['cpu']); cpu=Decimal(raw[:-1]) if raw.endswith('m') else Decimal(raw)*1000
        if not cpu.is_finite() or cpu<=0 or cpu!=int(cpu): raise ValueError()
        memory=_memory_to_mib(limits['memory'])
        storage_mib = _storage_to_mib(limits['ephemeral-storage']) if 'ephemeral-storage' in limits else None
        # The fixed init container requests 100m/64Mi. Requiring at least
        # that much keeps the Pod's effective budget within this reservation.
        if not 64<=memory<=2**31-1 or not 100<=cpu<=2**31-1: raise ValueError()
        if storage_mib is not None and (not 256<=storage_mib<=2**31-1 or
                                        max_storage_mib is not None and storage_mib>max_storage_mib): raise ValueError()
    except (InvalidOperation,TypeError,ValueError,OverflowError):
        raise AccessDenied(400,'invalid_sandbox_resources') from None
    ttl=body.get('timeout',600)
    if type(ttl) is not int or not 60<=ttl<=max_ttl_seconds: raise AccessDenied(400,'invalid_sandbox_ttl')
    metadata=body.get('metadata') or {}
    if not isinstance(metadata,dict) or {OWNER,REQUEST}.intersection(metadata) or len(metadata)>30:
        raise AccessDenied(400,'invalid_sandbox_metadata')
    for key,value in metadata.items():
        if (not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9]([A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?',key) or
                not isinstance(value,str) or len(value)>63 or value and not re.fullmatch(r'[A-Za-z0-9]([A-Za-z0-9_.-]*[A-Za-z0-9])?',value)):
            raise AccessDenied(400,'invalid_sandbox_metadata')
    return image['uri'],int(cpu),memory,storage_mib,ttl


def expiration(body):
    try:
        value=datetime.fromisoformat(body['expiresAt'].replace('Z','+00:00'))
        if value.tzinfo is None or set(body)!={'expiresAt'}: raise ValueError()
        return value.timestamp()
    except (KeyError,AttributeError,TypeError,ValueError): raise AccessDenied(400,'invalid_sandbox_expiration') from None


def data_action(path,port):
    if port!=44772: raise AccessDenied(403,'sandbox_port_not_enabled')
    first=path.strip('/').split('/')[0]
    if first in {'','ping'}: return 'connect'
    if first in {'files','directories'}: return 'files'
    if first in {'command','session'}: return 'exec'
    raise AccessDenied(404,'sandbox_route_not_enabled')


class RouteTokens:
    def __init__(self,key): self.key=key

    def issue(self,grant,row,port,route):
        body={'caller':grant.caller_id,'key':grant.key_id,'version':grant.version,'sandbox':row['sandbox_id'],
              'port':port,'route':route,'actions':list(grant.actions),
              'exp':int(min(row['max_deadline'],grant.expires_at or float('inf')))}
        raw=base64.urlsafe_b64encode(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).rstrip(b'=')
        signature=base64.urlsafe_b64encode(hmac.digest(self.key,raw,'sha256')).rstrip(b'=')
        return (raw+b'.'+signature).decode()

    def read(self,token):
        try:
            if not isinstance(token,str) or len(token)>4096: raise ValueError()
            raw,sig=token.encode().split(b'.')
            expected=base64.urlsafe_b64encode(hmac.digest(self.key,raw,'sha256')).rstrip(b'=')
            if not hmac.compare_digest(sig,expected): raise ValueError()
            body=json.loads(base64.urlsafe_b64decode(raw+b'='*(-len(raw)%4)))
            if type(body['exp']) is not int or body['exp']<=time.time(): raise ValueError()
            identity(body['sandbox'])
            if not isinstance(body['route'],str) or '%' in body['route'] or '..' in body['route']: raise ValueError()
            return body
        except (KeyError,TypeError,ValueError,UnicodeError,AccessDenied): raise AccessDenied(401,'invalid_sandbox_access') from None
