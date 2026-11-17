"""Bounded streaming proxy; human website cookies never reach guest workloads."""
import httpx
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from .auth import AccessDenied

HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','host','cookie','set-cookie','authorization','open-sandbox-api-key','x-api-key','api-key','x-chatsandbox-access','x-authenticated-user','x-user-id','x-caller-id','content-length'}


async def stream(request,url,*,transport=None,upstream_headers=None):
    headers={k:v for k,v in request.headers.items() if k.lower() not in HOP and not k.lower().startswith('x-forwarded-')}
    headers.update(upstream_headers or {})
    async def body():
        count=0
        async for part in request.stream():
            count+=len(part)
            if count>64*1024*1024: raise AccessDenied(413,'sandbox_upload_too_large')
            yield part
    client=httpx.AsyncClient(timeout=httpx.Timeout(30,read=300),follow_redirects=False,trust_env=False,transport=transport)
    try:
        response=await client.send(client.build_request(request.method,url,params=list(request.query_params.multi_items()),headers=headers,content=body()),stream=True)
    except Exception:
        await client.aclose(); raise AccessDenied(503,'sandbox_dataplane_unavailable') from None
    if 300<=response.status_code<400:
        await response.aclose(); await client.aclose(); raise AccessDenied(502,'sandbox_redirect_not_supported')
    async def close(): await response.aclose(); await client.aclose()
    out={k:v for k,v in response.headers.items() if k.lower() not in HOP}
    out.update({'Cache-Control':'no-store','Content-Security-Policy':"sandbox; default-src 'none'; frame-ancestors 'none'",'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'})
    return StreamingResponse(response.aiter_raw(),status_code=response.status_code,headers=out,background=BackgroundTask(close))
