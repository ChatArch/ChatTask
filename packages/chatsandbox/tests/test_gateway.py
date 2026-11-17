"""Native lifecycle and streaming behavior with in-process, synthetic dependencies."""
import json
import base64
from dataclasses import replace
from datetime import datetime,timezone
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from chatsandbox.auth import AccessDenied,Grant
from chatsandbox.gateway import GatewaySettings,create_app
from chatsandbox.ledger import Ledger


class Verifier:
    active=True
    async def verify(self,key):
        if key not in {'alice-key','bob-key'}: raise AccessDenied(401,'invalid_sandbox_key')
        caller=key.removesuffix('-key')
        return Grant(key,caller,'revision',('create','list','connect','exec','files','renew','delete'),
                     ('python:3.10-slim',),(44772,),2,2000,1024,7200)
    async def status(self,key,version): return self.active


class Native:
    def __init__(self): self.items={}; self.requests=[]; self.dataplane_headers=None
    def handle(self,request):
        self.requests.append(request)
        path=request.url.path
        if path=='/v1/sandboxes' and request.method=='POST':
            body=json.loads(request.content); sid='sandbox-'+str(len(self.items)+1)
            self.items[sid]={'id':sid,'metadata':body['metadata'],'status':{'state':'Running'}}
            return httpx.Response(202,json=self.items[sid])
        parts=path.strip('/').split('/')
        if parts[:2]==['v1','sandboxes']:
            sid=parts[2]
            if len(parts)>3 and parts[3]=='endpoints':
                return httpx.Response(200,json={'endpoint':'sandbox.example.test/runtime/'+sid+'/44772/1999999999/synthetic-native-signature','headers':{'X-EXECD-ACCESS-TOKEN':'synthetic-execd-token','OPEN-SANDBOX-API-KEY':'synthetic-internal-key'}})
            if len(parts)>3 and parts[3]=='renew-expiration': return httpx.Response(200,json=json.loads(request.content))
            if request.method=='DELETE': self.items.pop(sid,None); return httpx.Response(204)
            return httpx.Response(200,json=self.items[sid]) if sid in self.items else httpx.Response(404,json={'code':'not_found'})
        self.dataplane_headers=dict(request.headers)
        return httpx.Response(200,headers={'Set-Cookie':'guest-cookie=synthetic','Content-Type':'application/octet-stream'},stream=httpx.ByteStream(b'file-content'))


class Evidence:
    def __init__(self,native): self.native=native; self.attested=True
    def verify_absent(self,sid): return sid not in self.native.items
    def find_owned(self,metadata):
        return [sid for sid,item in self.native.items.items() if all(item['metadata'].get(k)==v for k,v in metadata.items())]
    def inspect_owned(self,sid,**kwargs):
        if sid not in self.native.items: raise RuntimeError('not ready')
        return {'provider':'kubernetes','sandbox_id':sid,'uid':'uid-'+sid,
                'cpu_millis':kwargs['cpu_millis'],'memory_mib':kwargs['memory_mib'],
                'pids_limit':128 if self.attested else None,'pids_limit_attested':self.attested,
                'privileged':False,'host_namespaces':False,'host_mount_count':0,'service_account_automounted':False}


@pytest.fixture
def service(tmp_path):
    native=Native(); evidence=Evidence(native); verifier=Verifier(); ledger=Ledger(tmp_path/'private'/'ledger.sqlite3')
    app=create_app(GatewaySettings('http://native.test','http://ingress.test','https://sandbox.example.test','synthetic-internal-key',b'x'*32),
                   verifier=verifier,evidence=evidence,ledger=ledger,transport=httpx.MockTransport(native.handle),reconcile_interval=0)
    with TestClient(app,base_url='https://sandbox.example.test') as client:
        yield client,native,evidence,verifier,ledger


def create(client,who='alice',**extra):
    body={'image':{'uri':'python:3.10-slim'},'resourceLimits':{'cpu':'1','memory':'512Mi'},'timeout':120,'metadata':{'purpose':'chattask'}}
    return client.post('/v1/sandboxes',headers={'OPEN-SANDBOX-API-KEY':who+'-key',**extra},json=body)


def test_lifecycle_owner_listing_runtime_and_cleanup(service):
    client,native,evidence,verifier,ledger=service
    response=create(client); assert response.status_code==202
    sid=response.json()['id']; path='/v1/sandboxes/'+sid; auth={'OPEN-SANDBOX-API-KEY':'alice-key'}
    assert native.requests[0].headers['OPEN-SANDBOX-API-KEY']=='synthetic-internal-key'
    assert native.items[sid]['metadata']['purpose']=='chattask'
    assert client.get(path).status_code==401
    assert client.get(path,headers={'OPEN-SANDBOX-API-KEY':'bob-key'}).status_code==404
    assert client.get('/v1/sandboxes',headers={'OPEN-SANDBOX-API-KEY':'bob-key'}).json()['items']==[]
    assert client.get('/v1/sandboxes',headers=auth).json()['pagination']['totalItems']==1
    runtime=client.get(path+'/evidence',headers=auth)
    assert runtime.status_code==200 and runtime.json()['runtime']['pids_limit']==128
    assert client.delete(path,headers=auth).status_code==204
    assert client.get(path+'/evidence',headers=auth).json()['state']=='absent'
    assert ledger.all()[0]['state']=='absent'


def test_idempotency_filters_and_cumulative_renewal(service):
    client,native,evidence,verifier,ledger=service
    first=create(client,**{'Idempotency-Key':'one'}); sid=first.json()['id']
    second=create(client,**{'Idempotency-Key':'one'})
    assert second.status_code==200 and second.json()['id']==sid and len(native.items)==1
    auth={'OPEN-SANDBOX-API-KEY':'alice-key'}
    assert client.get('/v1/sandboxes',headers=auth,params={'metadata':'purpose=chattask'}).json()['pagination']['totalItems']==1
    assert client.get('/v1/sandboxes',headers=auth,params={'metadata':'purpose=another'}).json()['items']==[]
    valid=datetime.fromtimestamp(time.time()+300,timezone.utc).isoformat()
    assert client.post('/v1/sandboxes/'+sid+'/renew-expiration',headers=auth,json={'expiresAt':valid}).status_code==200
    late=datetime.fromtimestamp(time.time()+8000,timezone.utc).isoformat()
    assert client.post('/v1/sandboxes/'+sid+'/renew-expiration',headers=auth,json={'expiresAt':late}).status_code==400


def test_endpoint_headers_streams_and_current_grant_status(service):
    client,native,evidence,verifier,ledger=service
    sid=create(client).json()['id']; auth={'OPEN-SANDBOX-API-KEY':'alice-key'}
    response=client.get('/v1/sandboxes/'+sid+'/endpoints/44772',headers=auth)
    assert response.status_code==200
    endpoint=response.json(); headers={**endpoint['headers'],'Cookie':'human-site-cookie=synthetic'}
    assert endpoint['endpoint']=='sandbox.example.test/runtime/'+sid+'/44772'
    assert 'OPEN-SANDBOX-API-KEY' not in endpoint['headers']
    assert 'X-EXECD-ACCESS-TOKEN' not in endpoint['headers']
    encoded=endpoint['headers']['X-ChatSandbox-Access'].split('.')[0]
    assert b'synthetic-native-signature' not in base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4))
    response=client.get('/runtime/'+sid+'/44772/files/download',headers=headers)
    assert response.status_code==200 and response.content==b'file-content'
    assert 'set-cookie' not in response.headers and 'sandbox' in response.headers['content-security-policy']
    assert 'cookie' not in native.dataplane_headers and 'authorization' not in native.dataplane_headers
    assert native.dataplane_headers['x-execd-access-token']=='synthetic-execd-token'
    assert native.requests[-1].url.path=='/'+sid+'/44772/1999999999/synthetic-native-signature/files/download'
    verifier.active=False
    assert client.get('/runtime/'+sid+'/44772/ping',headers=headers).status_code==401


def test_runtime_policy_unavailable_does_not_issue_capability(service):
    client,native,evidence,verifier,ledger=service
    sid=create(client).json()['id']; evidence.attested=False
    response=client.get('/v1/sandboxes/'+sid+'/endpoints/44772',headers={'OPEN-SANDBOX-API-KEY':'alice-key'})
    assert response.status_code==503


def test_sdk_eager_egress_discovery_does_not_enable_egress_traffic(service):
    client,native,evidence,verifier,ledger=service
    sid=create(client).json()['id']; auth={'OPEN-SANDBOX-API-KEY':'alice-key'}
    discovered=client.get('/v1/sandboxes/'+sid+'/endpoints/18080',headers=auth)
    assert discovered.status_code==200 and discovered.json()['headers']=={}
    assert client.get('/runtime/'+sid+'/18080/policy',headers=auth).status_code==401
    execd=client.get('/v1/sandboxes/'+sid+'/endpoints/44772',headers=auth).json()
    assert client.get('/runtime/'+sid+'/18080/policy',headers=execd['headers']).status_code==403
    assert not any('/18080' in str(r.url) for r in native.requests)


def test_terminating_pod_is_reported_deleting_until_absence_is_proven(service):
    client,native,evidence,verifier,ledger=service
    sid=create(client).json()['id'];row=ledger.all()[0]
    ledger.state(row['request_id'],'deleting');evidence.attested=False
    response=client.get('/v1/sandboxes/'+sid+'/evidence',headers={'OPEN-SANDBOX-API-KEY':'alice-key'})
    assert response.status_code==200 and response.json()['state']=='deleting'
    assert response.json()['runtime'] is None and ledger.all()[0]['state']=='deleting'
    native.items.pop(sid)
    assert client.get('/v1/sandboxes/'+sid+'/evidence',headers={'OPEN-SANDBOX-API-KEY':'alice-key'}).json()['state']=='absent'


def test_uncertain_create_remains_reserved(service):
    client,native,evidence,verifier,ledger=service
    original=native.handle
    def timeout(request):
        if request.method=='POST' and request.url.path=='/v1/sandboxes': raise httpx.ReadTimeout('synthetic timeout')
        return original(request)
    # Replace the transport callback while keeping all requests in-process.
    native_transport=httpx.MockTransport(timeout)
    app=create_app(GatewaySettings('http://native.test','http://ingress.test','https://sandbox.example.test','synthetic-internal-key',b'x'*32),
        verifier=verifier,evidence=evidence,ledger=ledger,transport=native_transport,reconcile_interval=0)
    with TestClient(app,base_url='https://sandbox.example.test') as other:
        response=create(other); assert response.status_code==503
        request_id=response.json()['request_id']
        result=other.get('/v1/operations/'+request_id,headers={'OPEN-SANDBOX-API-KEY':'alice-key'})
        assert result.status_code==200 and result.json()['state']=='uncertain'
    assert ledger.all()[0]['state']=='uncertain'
