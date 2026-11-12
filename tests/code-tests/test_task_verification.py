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


def test_ambiguous_or_nonfinite_scores_are_not_passing_evidence():
    parsed=module().criterion_results([{'id':'AC1','score':1.0},{'id':'AC1','score':0.0}],{'AC1'})
    assert parsed['AC1']['passed'] is None
    with pytest.raises(ValueError):module().strict_json('{"reward":NaN}')
    with pytest.raises(ValueError):module().strict_json('{"reward":0,"reward":1}')
