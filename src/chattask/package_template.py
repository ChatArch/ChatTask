"""Versioned, pure Python package template and deterministic instructions."""
from __future__ import annotations
import hashlib
import json
import re
from packaging.version import Version, InvalidVersion

REVISION = 1
FIELDS = ('mode','package_name','module','cli','owner','repository','description',
          'requirements','version','python_min','license','docs')
DEFAULTS = {'version':'0.0.2','python_min':'3.10','license':'MIT','docs':'bilingual_mkdocs'}
NAME = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?\Z')
CLI_NAME = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z')
MODULE = re.compile(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z')
GITHUB = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z')
REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z')
REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z')

def form_schema():
    return {'id':'python-package','revision':REVISION,'fields':[
        {'name':'mode','label':'项目模式','type':'choice','choices':['new','existing'],'required':True},
        {'name':'package_name','label':'PyPI 包名','type':'text','required':True},
        {'name':'module','label':'Python 模块','type':'text','required':True},
        {'name':'cli','label':'命令行入口','type':'text','required':True},
        {'name':'owner','label':'GitHub 所有者','type':'text','required':True},
        {'name':'repository','label':'仓库名','type':'text','required':True},
        {'name':'description','label':'包描述','type':'text','required':True},
        {'name':'requirements','label':'功能要求','type':'textarea','required':True},
        {'name':'version','label':'目标版本','type':'text','default':'0.0.2','required':True},
        {'name':'python_min','label':'最低 Python','type':'choice','choices':['3.10','3.11','3.12'],'default':'3.10'},
        {'name':'license','label':'许可证','type':'choice','choices':['MIT','Apache-2.0','BSD-3-Clause'],'default':'MIT'},
        {'name':'docs','label':'文档','type':'choice','choices':['bilingual_mkdocs','none'],'default':'bilingual_mkdocs'}],
        'defaults':DEFAULTS,'repository_visibility':'private','ci':'standard','publish':'OIDC'}

def intent_prefill(sentence):
    if not isinstance(sentence,str) or len(sentence)>500:
        raise ValueError('intent must be at most 500 characters')
    result={}
    if any(word in sentence.lower() for word in ('已有','现有','existing')):
        result['mode']='existing'
    elif any(word in sentence.lower() for word in ('新建','创建','new')):
        result['mode']='new'
    tokens=re.findall(r'(?<![A-Za-z0-9._-])([A-Za-z][A-Za-z0-9._-]{0,98})(?![A-Za-z0-9._-])',sentence)
    reserved={'python','package','pkg','new','existing','create','publish','release','pypi','github','version','a','an','the'}
    names=[name for name in tokens if name.lower() not in reserved and NAME.fullmatch(name)]
    if len(set(names))==1:
        result['package_name']=names[0]
    return result

def validate_spec(value):
    if not isinstance(value,dict) or set(value)-set(FIELDS):
        raise ValueError('unknown package field')
    v={**DEFAULTS,**value}
    if v.get('mode') not in ('new','existing'):
        raise ValueError('mode must be new or existing')
    for key,pattern in [('package_name',NAME),('module',MODULE),('cli',CLI_NAME),
                        ('owner',GITHUB),('repository',REPO)]:
        item=v.get(key)
        if not isinstance(item,str) or not pattern.fullmatch(item):
            raise ValueError('invalid literal '+key+'; choose an explicit valid name (no silent normalization)')
    if v['repository'] in ('.','..') or v['repository'].endswith('.git'):
        raise ValueError('invalid repository name')
    for key,limit in [('description',500),('requirements',4000)]:
        item=v.get(key)
        if not isinstance(item,str) or not item.strip() or len(item)>limit or any(ord(c)<32 and c not in '\n\t' for c in item):
            raise ValueError(key+' is required and bounded')
        v[key]=item.strip()
    version=v['version']
    if not isinstance(version,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2}',version) or len(version)>32:
        raise ValueError('version must be an explicit X.Y.Z release')
    try:
        if str(Version(version))!=version:
            raise ValueError('version must use canonical literal form')
    except InvalidVersion as exc:
        raise ValueError('invalid version') from exc
    if v['mode']=='new' and Version(version)<=Version('0.0.1'):
        raise ValueError('new package release must follow the 0.0.1 registration placeholder')
    if v['python_min'] not in ('3.10','3.11','3.12') or v['license'] not in ('MIT','Apache-2.0','BSD-3-Clause') or v['docs'] not in ('bilingual_mkdocs','none'):
        raise ValueError('unsupported package choice')
    return {key:v[key] for key in FIELDS}

def fingerprint(spec):
    return hashlib.sha256(json.dumps({'revision':REVISION,'spec':spec},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def policy(spec, *, sources=None, artifact_digests=None):
    result={'project':spec['package_name'],'repository':spec['owner']+'/'+spec['repository'],
            'version':spec['version'],'description':spec['description'],'mode':spec['mode'],
            'release_branch':'release/'+spec['version'],'tag':'v'+spec['version']}
    if sources is not None:
        result['sources']=sources
        result['artifact_digests']=artifact_digests or {}
        result['actions']=(['placeholder','repository','source','publisher','pr','merge','tag']
                           if spec['mode']=='new' else ['source','pr','merge','tag'])
    return result

def prepare_instructions(spec):
    serial=json.dumps(spec,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    branches=(['main','release/'+spec['version']] if spec['mode']=='new' else ['release/'+spec['version']])
    return (
        'PREPARE ONLY. Use /workspace/task_tools.py /tools/catalog and only read-only scoped routes. '
        'No repository, registry, publisher, PR, tag or release mutation. Exact canonical spec: '+serial+'. '
        'Inspect live package/registry/publisher/repository/status facts. Refuse name/version collisions or unclear outcomes. '
        'Existing mode: fetch exact main snapshot via /github/api/repo/snapshot; never recreate project or placeholder. '
        'New mode: build standard Python package skeleton with private repository intent, MIT or selected license, '
        'bilingual MkDocs if selected, standard CI and OIDC publish workflow. Do not infer business behavior from the name; '
        'implement only explicit requirements. Build a 0.0.1 registration placeholder wheel and sdist, '
        'initial main source at 0.0.1, and release source at requested version. '
        'Run actual tests, build wheel+sdist, inspect metadata and distribution contents. '
        'Use exact .github/workflows/ci.yml named CI with a literal test check on push and pull_request. '
        'Use exact .github/workflows/publish.yml with only on: push: tags: ["v*"], id-token: write, and one uniquely named pypa/gh-action-pypi-publish@release/v1 step. '
        'For selected bilingual MkDocs provide mkdocs.yml and docs/en/ plus docs/zh/ sources. '
        'Registration artifacts will be independently rebuilt from the approved main source; do not add binary payloads or .data/scripts outside that source. '
        'Write /workspace/result/package-plan.json as strict JSON with keys spec, sources, placeholder_artifacts, validation. '
        'Sources keys exactly '+json.dumps(branches)+'. Each branch has base_sha (null for new, exact snapshot SHA for existing) '
        'and files: [{path,content_base64,sha256}] covering full intended branch tree, at most 100 files per branch, 200000 bytes per file and 2000000 bytes total per branch. '
        'Placeholder artifacts [{filename,content_base64,sha256}] only for new mode; existing uses []. '
        'Validation must include checks with factual completed receipts for tools, tests, build, metadata and target, '
        'each as {status:\"passed\", evidence:<nonempty factual string>}; failures must be reported, never marked passed. '
        'Also put readable preflight report in /workspace/result/README.md. Do not include secrets. '
        'Do not publish or perform write tool calls.'
    )

def publish_instructions(spec,plan_hash):
    return (
        'PUBLISH ONLY from verified /workspace/input/previous-result.tar.gz. '
        'The server has approved the exact package plan SHA256 '+plan_hash+'. '
        'Read package-plan.json and use its exact bytes, files and hashes; do not edit or regenerate sources/artifacts. '
        'Use only tool catalog routes and exact approved target. For new mode register the approved 0.0.1 placeholder through the scoped registry upload API '
        'before creating the private repository; then create repository, main source, Publisher, release branch and PR. The final release uses tag-triggered OIDC, not the registration placeholder. '
        'For existing mode do not upload placeholder, create repository or add publisher unless policy explicitly permits it. '
        'Check exact branch HEAD and CI, merge exact PR head via squash, check main CI and merged tree, then create v'
        +spec['version']+' tag only on merged tree. Do not force or move tags. Trigger actual OIDC publication and '
        'read back registry metadata; perform a clean install. Unknown HTTP outcomes require read-back using same '
        'target/key before any retry. Provider errors are blockers, never success. Put factual receipts/status in '
        '/workspace/result/publish-report.json and readable /workspace/result/README.md. '
        'Never request upstream credentials or use fallback credentials.'
    )
