"""Trusted control client for fresh, task-scoped model/tool sessions."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import ssl
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit

class GatewayUnknown(RuntimeError):
    pass

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

@dataclass(frozen=True)
class GatewaySession:
    id: str
    model_base_url: str
    model_access_token: str = field(repr=False)
    tool_base_url: str = ''
    tool_access_token: str = field(default='', repr=False)
    expires_at: str | int | float = ''

def _https(value):
    if not isinstance(value,str) or len(value)>2048:
        raise ValueError('invalid gateway URL')
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('HTTPS gateway URL required')
    return value.rstrip('/')

def load_public_ca(path):
    if not path:
        return b''
    source=Path(path)
    if not source.is_absolute() or source.is_symlink():
        raise ValueError('gateway CA file must be an absolute regular file')
    with source.open('rb') as stream:
        data=stream.read(65537)
    if not data or len(data)>65536 or b'PRIVATE KEY' in data or b'-----BEGIN CERTIFICATE-----' not in data or b'-----END CERTIFICATE-----' not in data:
        raise ValueError('gateway CA must contain bounded public certificates only')
    try:
        data.decode('ascii')
    except UnicodeDecodeError:
        raise ValueError('gateway CA must be ASCII PEM') from None
    return data

class GatewayClient:
    def __init__(self,origin,client_token,*,ca_file='',opener=None):
        self.origin=_https(origin)
        if urlsplit(self.origin).path not in ('','/'):
            raise ValueError('gateway must be an HTTPS origin')
        if not isinstance(client_token,str) or not client_token:
            raise ValueError('gateway client token required')
        self._token=client_token
        self.ca_certificate=load_public_ca(ca_file)
        context=ssl.create_default_context()
        if self.ca_certificate:
            context.load_verify_locations(cadata=self.ca_certificate.decode('ascii'))
        self._open=opener or urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=context)).open

    def create(self,request_id,mode,plan):
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id):
            raise ValueError('task ID required')
        if mode not in ('generic','prepare','publish','verify') or (mode=='generic') != (plan is None):
            raise ValueError('invalid gateway session mode/policy')
        payload=json.dumps({'request_id':request_id,'mode':mode,'plan':plan},sort_keys=True,separators=(',',':')).encode()
        request=urllib.request.Request(self.origin+'/control/sessions',data=payload,method='POST',
            headers={'Authorization':'Bearer '+self._token,'Content-Type':'application/json','Accept':'application/json'})
        # A dropped reply is reconciled with the same task ID, never a fresh session key.
        data=None
        for attempt in range(2):
            try:
                with self._open(request,timeout=8) as response:
                    if response.status!=200 or int(response.headers.get('content-length','0'))>16384:
                        raise GatewayUnknown('gateway session outcome unknown; retry same task ID')
                    data=response.read(16385)
                break
            except urllib.error.HTTPError as exc:
                if 400<=exc.code<500:
                    raise RuntimeError('gateway session request denied') from None
                if attempt:
                    raise GatewayUnknown('gateway session outcome unknown; retry same task ID') from None
            except (urllib.error.URLError,TimeoutError,OSError):
                if attempt:
                    raise GatewayUnknown('gateway session outcome unknown; retry same task ID') from None
        if data is None:
            raise GatewayUnknown('gateway session outcome unknown; retry same task ID')
        if len(data)>16384:
            raise GatewayUnknown('gateway session outcome unknown; retry same task ID')
        try:
            value=json.loads(data)
            if not isinstance(value,dict) or set(value)!={'id','model_base_url','model_access_token','tool_base_url','tool_access_token','expires_at'}:
                raise ValueError()
            ident=value['id']
            if ident!=request_id:
                raise ValueError()
            expected=self.origin+'/sessions/'+ident
            tool=value['tool_base_url']
            if mode=='verify':
                if value['model_base_url'] or value['model_access_token'] or tool!=expected or not isinstance(value['tool_access_token'],str) or not value['tool_access_token']:
                    raise ValueError()
                model=''
            else:
                model=_https(value['model_base_url'])
                if value['model_base_url']!=expected+'/v1' or model!=expected+'/v1':
                    raise ValueError()
                if mode=='generic':
                    if tool or value['tool_access_token']:
                        raise ValueError()
                elif tool!=expected or not value['tool_access_token']:
                    raise ValueError()
                if not value['model_access_token'] or not isinstance(value['model_access_token'],str):
                    raise ValueError()
            raw_expiry=value['expires_at']
            expires=(datetime.fromtimestamp(raw_expiry,timezone.utc) if isinstance(raw_expiry,(int,float)) and not isinstance(raw_expiry,bool)
                     else datetime.fromisoformat(raw_expiry.replace('Z','+00:00')))
            if expires.tzinfo is None or expires<=datetime.now(timezone.utc):
                raise ValueError()
            return GatewaySession(ident,model,value['model_access_token'],tool,value['tool_access_token'],value['expires_at'])
        except (KeyError,TypeError,ValueError,OverflowError,AttributeError):
            raise GatewayUnknown('invalid gateway session response; retry same task ID') from None

    def close(self,request_id):
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id):
            raise ValueError('task ID required')
        request=urllib.request.Request(self.origin+'/control/sessions/'+request_id+'/close',
            data=b'',method='POST',headers={'Authorization':'Bearer '+self._token,
            'Content-Type':'application/json','Accept':'application/json'})
        try:
            with self._open(request,timeout=8) as response:
                if response.status!=200 or int(response.headers.get('content-length','0'))>4096:
                    raise GatewayUnknown('gateway lease closure outcome unknown')
                raw=response.read(4097)
            if len(raw)>4096 or json.loads(raw)!={'closed':True}:
                raise GatewayUnknown('gateway lease closure outcome unknown')
            return {'closed':True}
        except urllib.error.HTTPError as exc:
            if 400<=exc.code<500:
                raise RuntimeError('gateway lease closure denied') from None
            raise GatewayUnknown('gateway lease closure outcome unknown') from None
        except (urllib.error.URLError,TimeoutError,OSError,ValueError,TypeError):
            raise GatewayUnknown('gateway lease closure outcome unknown') from None
