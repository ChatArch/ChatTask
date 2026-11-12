"""Verifier input/output contracts; synthetic archives, no host execution of submissions."""
import importlib.util
import io
import tarfile
import pytest


def module():
    assert importlib.util.find_spec('chattask.task_verification') is not None,'Isolated package verifier is not implemented'
    from chattask import task_verification
    return task_verification


def tar(entries):
    data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w:gz') as archive:
        for name,body,kind in entries:
            member=tarfile.TarInfo(name);member.type=kind
            if kind==tarfile.REGTYPE:member.size=len(body);archive.addfile(member,io.BytesIO(body))
            else:member.linkname='/etc/passwd';archive.addfile(member)
    return data.getvalue()


def test_submission_unpacking_preserves_bytes_and_rejects_escaping_members():
    parsed=module().submission_files([{'name':'result.tar.gz','data':tar([('report.md',b'report',tarfile.REGTYPE)])}])
    assert parsed['report.md']==b'report'
    for name,kind in [('../escape',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('report.md',tarfile.SYMTYPE)]:
        with pytest.raises(ValueError):module().submission_files([{'name':'result.tar.gz','data':tar([(name,b'bad',kind)])}])


def test_criterion_mapping_does_not_average_away_required_failures_or_guess_missing_ids():
    parsed=module().criterion_results({'groups':[{'id':'AC1','score':1.0},{'id':'AC2','score':0.0}]},{'AC1','AC2','AC3'})
    assert parsed['AC1']['passed'] is True
    assert parsed['AC2']['passed'] is False
    assert 'AC3' not in parsed


def test_verifier_error_preserves_bounded_diagnostics_without_model_credentials(monkeypatch):
    import asyncio,json
    from types import SimpleNamespace as N
    from chattask.codex_tasks import CodexRuntime
    from chattask.settings import TaskSettings
    from chattask.task_packages import initial_package
    verifier_module=module();secret='PRIVATE-VERIFIER-CANARY';observed=[]
    class Commands:
        async def run(self,*args,**kwargs):observed.append(kwargs['opts'].envs);return N()
    class Service:
        docker=N(containers=N(list=lambda **kwargs:[]))
        async def create(self,**kwargs):return N(id='fixture',commands=Commands()),None,{}
        async def dispose(self,*args):pass
        def output(self,result):return {'exit_code':1,'error':None,'stdout':'','stderr':'Missing verifier dependency '+secret+' '+('x'*4000)}
    async def exercise():
        parent=N(lock=asyncio.Lock(),settings=TaskSettings('http://127.0.0.1:1','synthetic'),docker=Service.docker)
        monkeypatch.setattr(verifier_module,'TaskService',lambda *args,**kwargs:Service())
        verifier=verifier_module.SandboxPackageVerifier(parent,CodexRuntime('https://model.example/v1',secret,{'python':'image'}))
        return await verifier.evaluate(initial_package('Goal','image'),[],task={'image_profile':'python'},run={'evaluation':{'id':'a'*32}})
    result=asyncio.run(exercise())
    assert result['status']=='error' and 'reward' not in result
    assert 'Missing verifier dependency' in result.get('diagnostic',''),'Verifier discarded useful failure evidence'
    assert secret not in json.dumps(result) and len(result['diagnostic'])<=2000
    assert observed[0].get('LITELLM_LOCAL_MODEL_COST_MAP')=='true', 'Verifier imports must use the pinned local cost map, not unrelated network downloads'


def test_codex_judge_wrapper_binds_the_approved_runtime_without_embedded_keys():
    from chattask.codex_tasks import CodexRuntime
    runtime=CodexRuntime('https://gateway.example/sessions/approved/v1','PRIVATE-JUDGE-CANARY',{'python':'image'},model='gpt-6-sol')
    builder=getattr(module(),'judge_codex_wrapper',None)
    assert callable(builder),'Rewardkit resets CODEX_HOME, so the judge needs an explicit provider-bound entrypoint'
    text=builder(runtime,'/usr/local/bin/codex')
    assert 'model_provider="task"' in text and runtime.base_url in text and 'gpt-6-sol' in text
    assert 'PRIVATE-JUDGE-CANARY' not in text and '"$@"' in text


def test_judge_cannot_install_a_different_codex_version():
    from chattask.codex_tasks import CodexRuntime
    checker=module().SandboxPackageVerifier(None,CodexRuntime('https://model.example/v1','synthetic',{'python':'image'}))
    with pytest.raises(ValueError,match='image'):
        checker._judge_policy({'files':{'tests/quality.toml':'[judge]\njudge="codex"\nversion="0.999.0"\n'}})


def test_ambiguous_or_nonfinite_scores_are_not_passing_evidence():
    parsed=module().criterion_results([{'id':'AC1','score':1.0},{'id':'AC1','score':0.0}],{'AC1'})
    assert parsed['AC1']['passed'] is None
    with pytest.raises(ValueError):module().strict_json('{"reward":NaN}')
    with pytest.raises(ValueError):module().strict_json('{"reward":0,"reward":1}')
