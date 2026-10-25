"""Owner-bound durable package drafts over the generic task engine."""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from .workflow import now
from .package_template import REVISION, REQUEST_ID, validate_spec, fingerprint, prepare_instructions, publish_instructions, policy
from .package_plan import verify_plan

DRAFT_ID = re.compile(r'[a-f0-9]{32}\Z')
def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def _request(body,allowed):
    if not isinstance(body,dict) or set(body)!=set(allowed) or not isinstance(body.get('request_id'),str) or not REQUEST_ID.fullmatch(body['request_id']):
        raise ValueError('invalid request fields or stable request_id')
def _task_request(prefix,owner,draft,revision,request_id):
    return prefix+'-'+_digest([owner,draft,revision,request_id])[:56]

class PackageService:
    def __init__(self,store,tasks,*,broker_required=False):
        self.store=store
        self.tasks=tasks
        self.broker_required=broker_required
        executor=getattr(tasks,'executor',None)
        if executor is not None and hasattr(executor,'register_postprocessor'):
            from .package_publication import prepare_postprocess,publish_postprocess
            executor.register_postprocessor('prepare',prepare_postprocess,600)
            executor.register_postprocessor('publish',publish_postprocess,480)

    def _owned(self,owner,ident):
        if not isinstance(ident,str) or not DRAFT_ID.fullmatch(ident):
            raise KeyError('draft not found')
        draft=self.store.get('package_drafts',ident)
        if draft.get('owner')!=owner:
            raise KeyError('draft not found')
        return draft

    def _check_other_request(self,owner,request_id,ident):
        for item in self.store.list('package_drafts'):
            if item.get('owner')==owner and item['id']!=ident and request_id in item.get('operations',{}):
                raise ValueError('request_id already used by another draft')

    def _public(self,draft):
        return {key:value for key,value in draft.items() if key not in ('owner','operations')}

    def list(self,owner):
        return [self._public(d) for d in sorted(self.store.list('package_drafts'),key=lambda x:x['created_at'],reverse=True)
                if d.get('owner')==owner][:100]

    def get(self,owner,ident):
        draft=self._owned(owner,ident)
        if draft.get('prepare_task_id') and draft.get('prepared_revision')==draft['revision'] and not draft.get('preview'):
            task=self.tasks.get(owner,draft['prepare_task_id'])
            if task['status']=='completed':
                try:
                    entry=next((x for x in task.get('artifacts',[]) if x['name']=='result.tar.gz'),None)
                    if not entry:
                        raise ValueError('preparation result archive missing')
                    data=self.store.artifact(task['id'],'result.tar.gz')
                    if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
                        raise ValueError('preparation archive digest mismatch')
                    preview=verify_plan(data,draft['spec'])
                    if preview['archive_sha256']!=entry['sha256']:
                        raise ValueError('preparation archive identity mismatch')
                    att=next((x for x in task.get('artifacts',[]) if x['name']=='trusted-validation.json'),None)
                    if not att or att['size']>50000:
                        raise ValueError('trusted Sandbox validation receipt missing')
                    att_bytes=self.store.artifact(task['id'],'trusted-validation.json')
                    if len(att_bytes)!=att['size'] or hashlib.sha256(att_bytes).hexdigest()!=att['sha256']:
                        raise ValueError('trusted validation receipt digest mismatch')
                    evidence=json.loads(att_bytes)
                    if evidence.get('plan_sha256')!=preview['plan_sha256'] or evidence.get('checks')!=preview['validation']:
                        raise ValueError('trusted validation does not bind the plan')
                    if set(evidence.get('branches',{}))!=set(preview['source_manifests']):
                        raise ValueError('trusted validation branch mismatch')
                    preview['task_id']=task['id']
                    preview['spec_fingerprint']=draft['fingerprint']
                    preview['revision']=draft['revision']
                    preview['confirmation']='PUBLISH '+preview['policy']['repository']+' '+preview['policy']['tag']+' '+preview['plan_sha256'][:12]
                    preview['preview_sha256']=_digest({k:v for k,v in preview.items() if k!='confirmation'})
                    with self.store.locked():
                        fresh=self._owned(owner,ident)
                        if fresh['revision']==draft['revision'] and fresh.get('prepare_task_id')==task['id'] and not fresh.get('preview'):
                            fresh['preview']=preview
                            fresh['updated_at']=now()
                            self.store.put('package_drafts',ident,fresh)
                        draft=fresh
                except (ValueError,KeyError,OSError) as exc:
                    draft={**draft,'prepare_status':'blocked','prepare_error':str(exc)[:500]}
            elif task['status'] in ('blocked','interrupted','cancelled'):
                draft={**draft,'prepare_status':task['status'],'prepare_error':task.get('error')}
            else:
                draft={**draft,'prepare_status':task['status']}
        if draft.get('prepare_task_id'):
            prep=self.tasks.get(owner,draft['prepare_task_id'])
            draft={**draft,'prepare_status':draft.get('prepare_status',prep['status'])}
        if draft.get('publish_task_id'):
            task=self.tasks.get(owner,draft['publish_task_id'])
            verified=task['status']=='completed' and any(item.get('name')=='trusted-publication.json' for item in task.get('artifacts',[]))
            draft={**draft,'publish_status':task['status'],'publish_result':task.get('result'),'publish_error':task.get('error'),'publish_verified':verified}
        return self._public(draft)

    async def create(self,owner,body):
        _request(body,('request_id','spec'))
        spec=validate_spec(body['spec'])
        digest=_digest({'spec':spec})
        with self.store.locked():
            for old in self.store.list('package_drafts'):
                if old.get('owner')==owner and old.get('create_request_id')==body['request_id']:
                    if old['create_fingerprint']!=digest:
                        raise ValueError('request_id input conflict')
                    return self._public(old)
            self._check_other_request(owner,body['request_id'],None)
            ident=uuid.uuid4().hex
            draft={'id':ident,'owner':owner,'template_id':'python-package','template_revision':REVISION,
                   'revision':1,'spec':spec,'fingerprint':fingerprint(spec),'created_at':now(),'updated_at':now(),
                   'create_request_id':body['request_id'],'create_fingerprint':digest,
                   'operations':{body['request_id']:{'kind':'create','fingerprint':digest}},
                   'prepare_task_id':None,'prepare_image_profile':None,'prepared_revision':None,'preview':None,'publish_task_id':None}
            self.store.put('package_drafts',ident,draft)
            return self._public(draft)

    async def update(self,owner,ident,body):
        _request(body,('request_id','revision','spec'))
        spec=validate_spec(body['spec'])
        digest=_digest({'revision':body['revision'],'spec':spec})
        with self.store.locked():
            draft=self._owned(owner,ident)
            self._check_other_request(owner,body['request_id'],ident)
            old=draft['operations'].get(body['request_id'])
            if old:
                if old['fingerprint']!=digest or old['kind']!='update':
                    raise ValueError('request_id input conflict')
                return self._public(draft)
            if draft.get('confirm_request_id'):
                raise ValueError('submitted draft is immutable; create a new draft')
            if body['revision']!=draft['revision']:
                raise ValueError('stale draft revision')
            if spec==draft['spec']:
                raise ValueError('no parameter change')
            draft.update(spec=spec,fingerprint=fingerprint(spec),revision=draft['revision']+1,
                         prepare_task_id=None,prepare_request_id=None,prepare_image_profile=None,prepared_revision=None,preview=None,updated_at=now())
            draft['operations'][body['request_id']]={'kind':'update','fingerprint':digest}
            self.store.put('package_drafts',ident,draft)
            return self._public(draft)

    async def prepare(self,owner,ident,body):
        if self.broker_required and not getattr(getattr(self.tasks,'executor',None),'gateway',None):
            raise ValueError('package preparation requires configured task gateway')
        _request(body,('request_id','revision','image_profile'))
        images=self.tasks.images() if callable(self.tasks.images) else self.tasks.images
        names=[x['id'] for x in images] if isinstance(images,list) else list(images)
        if not isinstance(body['image_profile'],str) or body['image_profile'] not in names:
            raise ValueError('configured image profile required')
        with self.store.locked():
            draft=self._owned(owner,ident)
            self._check_other_request(owner,body['request_id'],ident)
            digest=_digest(body)
            old=draft['operations'].get(body['request_id'])
            if old and (old['kind']!='prepare' or old['fingerprint']!=digest):
                raise ValueError('request_id input conflict')
            if body['revision']!=draft['revision']:
                raise ValueError('stale draft revision')
            if draft.get('confirm_request_id'):
                raise ValueError('submitted draft is immutable')
            if draft.get('prepare_request_id') and draft['prepare_request_id']!=body['request_id']:
                previous=self.tasks.get(owner,draft['prepare_task_id']) if draft.get('prepare_task_id') else None
                if previous and previous['status'] in ('queued','running','cancel_requested') or draft.get('preview'):
                    raise ValueError('preparation already selected for this revision')
                draft['preview']=None
                draft['prepare_task_id']=None
            task_request=_task_request('pkgprep',owner,ident,draft['revision'],body['request_id'])
            draft['prepare_request_id']=body['request_id']
            draft['prepare_image_profile']=body['image_profile']
            draft['prepared_revision']=draft['revision']
            draft['operations'][body['request_id']]={'kind':'prepare','fingerprint':digest}
            self.store.put('package_drafts',ident,draft)
        task=await self.tasks.create_internal(owner,{'request_id':task_request,'instructions':prepare_instructions(draft['spec']),
            'image_profile':body['image_profile']},{'mode':'prepare','plan':policy(draft['spec'])})
        with self.store.locked():
            fresh=self._owned(owner,ident)
            if fresh['revision']==draft['revision'] and fresh.get('prepare_request_id')==body['request_id']:
                fresh['prepare_task_id']=task['id'];fresh['updated_at']=now()
                self.store.put('package_drafts',ident,fresh)
        return self.get(owner,ident)

    async def confirm(self,owner,ident,body):
        if self.broker_required and not getattr(getattr(self.tasks,'executor',None),'gateway',None):
            raise ValueError('package submission requires configured task gateway')
        _request(body,('request_id','revision','preview_sha256','confirmation'))
        draft=self._owned(owner,ident)
        if body['revision']!=draft['revision']:
            raise ValueError('stale draft revision')
        if not draft.get('preview') and draft.get('prepare_task_id'):
            self.get(owner,ident)
            draft=self._owned(owner,ident)
        preview=draft.get('preview')
        if not preview:
            raise ValueError('verified preflight required')
        if body['preview_sha256']!=preview['preview_sha256'] or body['confirmation']!=preview['confirmation']:
            raise ValueError('confirmation must match exact preview and target')
        if preview['revision']!=draft['revision'] or preview['spec_fingerprint']!=draft['fingerprint'] or preview['task_id']!=draft['prepare_task_id']:
            raise ValueError('stale preparation')
        prep=self.tasks.get(owner,preview['task_id'])
        if prep['status']!='completed':
            raise ValueError('preparation not completed')
        artifact=next((a for a in prep.get('artifacts',[]) if a['name']=='result.tar.gz'),None)
        if not artifact or artifact['sha256']!=preview['archive_sha256']:
            raise ValueError('preparation result changed')
        data=self.store.artifact(prep['id'],'result.tar.gz')
        if len(data)!=artifact['size'] or hashlib.sha256(data).hexdigest()!=artifact['sha256']:
            raise ValueError('preparation archive bytes changed')
        digest=_digest(body)
        with self.store.locked():
            fresh=self._owned(owner,ident)
            self._check_other_request(owner,body['request_id'],ident)
            old=fresh['operations'].get(body['request_id'])
            if old and (old['kind']!='confirm' or old['fingerprint']!=digest):
                raise ValueError('request_id input conflict')
            if fresh.get('confirm_request_id') and fresh['confirm_request_id']!=body['request_id']:
                raise ValueError('already submitted')
            if fresh['revision']!=draft['revision'] or fresh.get('preview')!=preview:
                raise ValueError('preview changed')
            fresh['confirm_request_id']=body['request_id']
            fresh['operations'][body['request_id']]={'kind':'confirm','fingerprint':digest}
            fresh['approval']={'owner':owner,'at':now(),'revision':draft['revision'],
                'preview_sha256':preview['preview_sha256'],'confirmation':preview['confirmation']}
            self.store.put('package_drafts',ident,fresh)
        task=await self.tasks.create_internal(owner,{'request_id':_task_request('pkgpub',owner,ident,draft['revision'],body['request_id']),
            'instructions':publish_instructions(draft['spec'],preview['plan_sha256']),
            'image_profile':prep['image_profile'],'input_task_id':prep['id']},
            {'mode':'publish','plan':preview['policy'],'spec':draft['spec'],'plan_sha256':preview['plan_sha256']})
        with self.store.locked():
            fresh=self._owned(owner,ident)
            if fresh.get('confirm_request_id')==body['request_id']:
                fresh['publish_task_id']=task['id'];fresh['updated_at']=now()
                self.store.put('package_drafts',ident,fresh)
        return self.get(owner,ident)
