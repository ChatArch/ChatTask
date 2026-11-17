"""Native OpenSandbox API with explicit caller ownership and durable budgets."""
from contextlib import asynccontextmanager
from dataclasses import dataclass,field
import asyncio
import hashlib
import json
import math
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse,Response
from starlette.concurrency import run_in_threadpool

from .auth import AccessDenied
from .config import origin
from .routing import OWNER,REQUEST,RouteTokens,identity,create_policy,metadata_filter,expiration,data_action
from . import proxy


@dataclass(frozen=True)
class GatewaySettings:
    upstream: str
    ingress: str
    public_origin: str
    api_key: str=field(repr=False)
    signing_key: bytes=field(repr=False)

    def __post_init__(self):
        for value in [self.upstream,self.ingress,self.public_origin]: origin(value)
        if not self.public_origin.startswith('https://') or len(self.signing_key)<32 or not self.api_key:
            raise ValueError('Gateway requires public HTTPS and private authentication material')


def create_app(settings,*,verifier,evidence,ledger,transport=None,reconcile_interval=15):
    tokens=RouteTokens(settings.signing_key)

    async def native(method,path,*,body=None,params=None):
        try:
            async with httpx.AsyncClient(timeout=60,follow_redirects=False,trust_env=False,transport=transport) as client:
                return await client.request(method,settings.upstream+path,json=body,params=params,
                    headers={'OPEN-SANDBOX-API-KEY':settings.api_key})
        except httpx.HTTPError: raise AccessDenied(503,'sandbox_backend_unavailable') from None

    async def reconcile():
        for row in await run_in_threadpool(ledger.all):
            if row['state']=='absent': continue
            try:
                if row['sandbox_id']:
                    if await run_in_threadpool(evidence.verify_absent,row['sandbox_id']):
                        await run_in_threadpool(ledger.state,row['request_id'],'absent')
                else:
                    matches=await run_in_threadpool(evidence.find_owned,{REQUEST:row['request_id']})
                    if len(matches)==1: await run_in_threadpool(ledger.activate,row['request_id'],matches[0])
                    # No match never releases an uncertain in-flight creation.
            except Exception: continue

    async def monitor():
        while True:
            await asyncio.sleep(reconcile_interval); await reconcile()

    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(monitor()) if reconcile_interval else None
        try: yield
        finally:
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

    app=FastAPI(title='ChatSandbox',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.reconcile=reconcile

    @app.exception_handler(AccessDenied)
    async def denied(request,error):
        return JSONResponse({'code':error.code,'message':error.code},status_code=error.status,headers={'Cache-Control':'no-store'})

    @app.get('/health')
    async def health(): return {'status':'ready','package':'ChatSandbox','version':'0.1.0'}

    async def json_body(request):
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>2*1024*1024: raise AccessDenied(413,'request_too_large')
        try: return json.loads(data)
        except (ValueError,UnicodeError): raise AccessDenied(400,'invalid_json') from None

    async def authenticated(request):
        standard=request.headers.getlist('open-sandbox-api-key'); authorization=request.headers.getlist('authorization')
        if len(standard)>1 or len(authorization)>1 or standard and authorization: raise AccessDenied(401,'invalid_sandbox_key')
        key=standard[0] if standard else authorization[0][7:] if authorization and authorization[0].startswith('Bearer ') else None
        return await verifier.verify(key)

    async def checked_runtime(row):
        try:
            runtime=await run_in_threadpool(lambda:evidence.inspect_owned(row['sandbox_id'],metadata={REQUEST:row['request_id']},cpu_millis=row['cpu_millis'],memory_mib=row['memory_mib']))
            if runtime.get('pids_limit')!=128 or runtime.get('pids_limit_attested') is not True:
                raise ValueError('Runtime policy is not attested')
            return runtime
        except Exception:
            raise AccessDenied(503,'sandbox_runtime_unavailable') from None

    async def native_endpoint(row,port,deadline):
        response=await native('GET','/v1/sandboxes/'+row['sandbox_id']+'/endpoints/'+str(port),params={'expires':str(int(deadline))})
        if response.status_code!=200: raise AccessDenied(503,'sandbox_endpoint_unavailable')
        try:
            body=response.json(); target=urlsplit('https://'+body['endpoint']); route=target.path.removeprefix('/runtime/').rstrip('/')
            prefix=row['sandbox_id']+'/'+str(port)
            if (target.netloc!=urlsplit(settings.public_origin).netloc or not target.path.startswith('/runtime/') or
                    target.query or target.fragment or '%' in route or '\\' in route or
                    any(part in {'.','..',''} for part in route.split('/')) or
                    not route.startswith(prefix+'/')): raise ValueError()
            scoped={'opensandbox-secure-access','x-execd-access-token','x-sandbox-id','x-sandbox-port'}
            headers={k:v for k,v in (body.get('headers') or {}).items() if k.lower() in scoped}
            return route,headers
        except (KeyError,TypeError,ValueError): raise AccessDenied(502,'invalid_native_sandbox_endpoint') from None

    def raw(response):
        return Response(response.content,status_code=response.status_code,
            media_type=response.headers.get('content-type','application/json'),headers={'Cache-Control':'no-store'})

    @app.get('/v1/operations/{request_id}')
    async def operation(request: Request,request_id: str):
        grant=await authenticated(request); grant.require('connect')
        return await run_in_threadpool(ledger.operation,identity(request_id),grant.caller_id)

    @app.api_route('/v1/sandboxes',methods=['POST','GET'])
    async def collection(request: Request):
        grant=await authenticated(request)
        if request.method=='POST':
            grant.require('create'); body=await json_body(request)
            image,cpu,memory,ttl=create_policy(body)
            row,new=await run_in_threadpool(lambda:ledger.reserve(grant,body,image=image,cpu=cpu,memory=memory,ttl=ttl,idempotency=request.headers.get('idempotency-key')))
            if not new:
                if row['state']=='active': return raw(await native('GET','/v1/sandboxes/'+row['sandbox_id']))
                raise AccessDenied(409,'sandbox_creation_pending' if row['state']!='absent' else 'sandbox_request_already_finished')
            forwarded={**body,'secureAccess':True,'metadata':{**(body.get('metadata') or {}),
                OWNER:hashlib.sha256(grant.caller_id.encode()).hexdigest()[:32],REQUEST:row['request_id']}}
            try: response=await native('POST','/v1/sandboxes',body=forwarded)
            except AccessDenied:
                await run_in_threadpool(ledger.state,row['request_id'],'uncertain')
                return JSONResponse({'code':'sandbox_creation_pending','request_id':row['request_id']},status_code=503)
            if response.status_code in {200,201,202}:
                try: sandbox_id=identity(response.json()['id'])
                except (KeyError,TypeError,ValueError,AccessDenied):
                    await run_in_threadpool(ledger.state,row['request_id'],'uncertain')
                    raise AccessDenied(502,'invalid_native_sandbox_response') from None
                await run_in_threadpool(ledger.activate,row['request_id'],sandbox_id)
            else:
                await run_in_threadpool(ledger.state,row['request_id'],'absent' if response.status_code in {400,401,403,404,422,429} else 'uncertain')
            return raw(response)
        grant.require('list'); rows=await run_in_threadpool(ledger.all,grant.caller_id)
        wanted=metadata_filter(request.query_params.get('metadata')); items=[]
        for row in rows:
            if not row['sandbox_id'] or row['state']=='absent': continue
            response=await native('GET','/v1/sandboxes/'+row['sandbox_id'])
            if response.status_code==200:
                item=response.json()
                if all((item.get('metadata') or {}).get(k)==v for k,v in wanted.items()): items.append(item)
            elif response.status_code!=404: raise AccessDenied(503,'sandbox_inventory_unavailable')
        try:
            page=int(request.query_params.get('page','1')); size=int(request.query_params.get('pageSize','20'))
            if not 1<=page<=1000 or not 1<=size<=100: raise ValueError()
        except ValueError: raise AccessDenied(400,'invalid_pagination') from None
        count=len(items)
        return {'items':items[(page-1)*size:page*size],'pagination':{'page':page,'pageSize':size,'totalItems':count,'totalPages':math.ceil(count/size),'hasNextPage':page*size<count}}

    @app.api_route('/v1/sandboxes/{sandbox_id}',methods=['GET','DELETE'])
    @app.api_route('/v1/sandboxes/{sandbox_id}/{subpath:path}',methods=['GET','POST','PATCH','PUT','DELETE'])
    async def instance(request: Request,sandbox_id: str,subpath: str=''):
        grant=await authenticated(request); identity(sandbox_id)
        row=await run_in_threadpool(ledger.owned,sandbox_id,grant.caller_id)
        if subpath=='evidence' and request.method=='GET':
            grant.require('connect')
            if await run_in_threadpool(evidence.verify_absent,sandbox_id):
                await run_in_threadpool(ledger.state,row['request_id'],'absent')
                return {'sandbox_id':sandbox_id,'owned':True,'state':'absent','runtime':None}
            if row['state']=='deleting':
                return {'sandbox_id':sandbox_id,'owned':True,'state':'deleting','runtime':None}
            runtime=await checked_runtime(row)
            return {'sandbox_id':sandbox_id,'owned':True,'state':'active','runtime':runtime}
        if row['state']=='absent': raise AccessDenied(404,'sandbox_not_found')
        if not subpath:
            grant.require('delete' if request.method=='DELETE' else 'connect')
            if request.method=='DELETE': await run_in_threadpool(ledger.state,row['request_id'],'deleting')
            response=await native(request.method,'/v1/sandboxes/'+sandbox_id)
            if request.method=='DELETE' and response.status_code>=400 and response.status_code!=404:
                await run_in_threadpool(ledger.state,row['request_id'],'active')
            return raw(response)
        if subpath.startswith('endpoints/') and request.method=='GET':
            grant.require('connect')
            try: port=int(subpath.removeprefix('endpoints/'))
            except ValueError: raise AccessDenied(400,'invalid_port') from None
            if port==18080:
                # SDK 1.1.0 eagerly resolves this endpoint even when no egress
                # sidecar exists. Discovery must succeed for connect/create;
                # issue no capability and never forward this disabled port.
                await checked_runtime(row)
                return {'endpoint':urlsplit(settings.public_origin).netloc+'/runtime/'+sandbox_id+'/18080','headers':{}}
            if port not in grant.ports or port!=44772: raise AccessDenied(403,'sandbox_port_denied')
            await checked_runtime(row)
            deadline=int(min(row['max_deadline'],grant.expires_at or float('inf')))
            await native_endpoint(row,port,deadline)
            route=sandbox_id+'/'+str(port)
            # Native bearer URIs/headers stay entirely on the server. Base64
            # capabilities are readable; embedding the native URI would bypass
            # fresh CRS status checks if an internal Service were reachable.
            return {'endpoint':urlsplit(settings.public_origin).netloc+'/runtime/'+route,
                    'headers':{'X-ChatSandbox-Access':tokens.issue(grant,row,port,route)}}
        if subpath=='renew-expiration' and request.method=='POST':
            grant.require('renew'); body=await json_body(request); expires=expiration(body)
            if not time.time()<expires<=min(row['max_deadline'],row['created']+grant.max_ttl_seconds,grant.expires_at or float('inf')):
                raise AccessDenied(400,'sandbox_ttl_invalid')
            response=await native('POST','/v1/sandboxes/'+sandbox_id+'/'+subpath,body=body)
            if response.status_code<300: await run_in_threadpool(ledger.renewed,row['request_id'],expires)
            return raw(response)
        raise AccessDenied(404,'sandbox_route_not_enabled')

    @app.api_route('/runtime/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE','HEAD'])
    async def dataplane(request: Request,path: str):
        try:
            raw_path=request.scope.get('raw_path',b'').decode('ascii')
            if '%' in raw_path or '\\' in raw_path or any(part in {'.','..',''} for part in path.split('/')):
                raise ValueError()
        except (ValueError,UnicodeError):
            raise AccessDenied(400,'invalid_sandbox_route') from None
        values=request.headers.getlist('x-chatsandbox-access'); payload=tokens.read(values[0] if len(values)==1 else None)
        route=payload['sandbox']+'/'+str(payload['port'])
        if path!=route and not path.startswith(route+'/'): raise AccessDenied(403,'sandbox_route_mismatch')
        if not await verifier.status(payload['key'],payload['version']): raise AccessDenied(401,'sandbox_access_revoked')
        row=await run_in_threadpool(ledger.owned,payload['sandbox'],payload['caller'])
        if row['state']!='active' or time.time()>=min(row['expires_at'],row['max_deadline']): raise AccessDenied(401,'sandbox_access_expired')
        if data_action(path[len(route):],payload['port']) not in payload['actions']: raise AccessDenied(403,'sandbox_action_denied')
        await checked_runtime(row)
        native_route,native_headers=await native_endpoint(row,payload['port'],payload['exp'])
        return await proxy.stream(request,settings.ingress+'/'+native_route+path[len(route):],
                                  upstream_headers=native_headers,transport=transport)

    return app
