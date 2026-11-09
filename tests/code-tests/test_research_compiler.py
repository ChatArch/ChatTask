"""Synthetic Docker contract tests; no container or original service is used."""
from pathlib import Path
import hashlib
import io
import tarfile
from types import SimpleNamespace

import pytest

from chattask.research import DockerOverleafBridge, ResearchPublisher, ResearchUnavailable
from chattask.research_compiler import IsolatedResearchCompiler

TASK = 'a' * 32
RUN = 'b' * 32
MAIN = r'\documentclass{ctexart}\begin{document}Safe text\end{document}'
IMAGE = 'sha256:' + 'c' * 64


class FakeDocker:
    def __init__(self, tex_root, pdf=b'%PDF-1.7\n' + b'x' * 30):
        self.tex_root, self.pdf = tex_root, pdf
        self.created = []
        self.active = []
        self.original = SimpleNamespace(attrs={
            'Image': IMAGE,
            'Mounts': [{'Type':'bind','Destination':'/usr/local/texlive', 'Source':str(tex_root)}],
        })
        self.images = SimpleNamespace(get=lambda image: SimpleNamespace(id=IMAGE))
        self.containers = self

    def get(self, name):
        assert name == 'original-service'
        return self.original

    def list(self, **kwargs): return self.active

    def create(self, **kwargs):
        self.created.append(kwargs)
        docker = self
        class Container:
            id = 'd' * 64
            def start(self): pass
            def wait(self, timeout=None):
                assert timeout is not None and timeout <= 300
                return {'StatusCode':0}
            def get_archive(self, path):
                assert path == '/export/main.pdf'
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w') as archive:
                    member = tarfile.TarInfo('main.pdf'); member.size = len(docker.pdf)
                    archive.addfile(member, io.BytesIO(docker.pdf))
                return [stream.getvalue()], {}
            def remove(self, force=False):
                assert force is False
        return Container()


def test_pdf_export_survives_tmpfs_teardown(tmp_path):
    """Docker unmounts tmpfs at exit; export the PDF before the process exits."""
    tex = tmp_path/'public-tex'; tex.mkdir()
    engine = tex/'version'/'bin'/'arch'/'xelatex'; engine.parent.mkdir(parents=True); engine.write_bytes(b'synthetic')
    client = FakeDocker(tex)
    compiler = IsolatedResearchCompiler(client, tmp_path/'private', 'original-service')
    pdf, _ = compiler.compile(TASK, RUN, {'main.tex':MAIN}, compiler.prepare())
    options = client.created[0]
    assert any(value == {'bind':'/export','mode':'rw'} for value in options['volumes'].values())
    assert 'cp -- /output/main.pdf /export/main.pdf' in options['command'][0]
    assert '/usr/bin/timeout' in options['entrypoint']
    assert pdf.startswith(b'%PDF-')


def test_isolated_compiler_options_and_pdf_bound(tmp_path):
    tex = tmp_path/'public-tex'; tex.mkdir()
    engine = tex/'version'/'bin'/'arch'/'xelatex'; engine.parent.mkdir(parents=True); engine.write_bytes(b'synthetic')
    client = FakeDocker(tex)
    compiler = IsolatedResearchCompiler(client, tmp_path/'private', 'original-service')
    plan = compiler.prepare()
    pdf, receipt = compiler.compile(TASK, RUN, {'main.tex': MAIN}, plan)
    assert pdf.startswith(b'%PDF-') and receipt['image_id'] == IMAGE
    assert len(client.created) == 1
    for options in client.created:
        assert options['image'] == IMAGE
        assert options['network_disabled'] is True and options['read_only'] is True
        assert options['privileged'] is False and options['cap_drop'] == ['ALL']
        assert options['security_opt'] == ['no-new-privileges:true']
        assert options['user'] not in ('0', '0:0', 'root')
        assert options['environment'] == {}
        assert options['mem_limit'] <= 512 * 1024 * 1024 and options['pids_limit'] <= 64
        assert options['entrypoint'][:2] == ['/usr/bin/env','-i']
        assert options['entrypoint'][-2:] == ['/bin/sh','-c']
        assert options['command'][0].count('xelatex') == 2
        assert '-no-shell-escape' in options['command'][0]
        mounts = options['volumes']
        assert len(mounts) == 3
        assert {mount['bind'] for mount in mounts.values()} == {'/input','/usr/local/texlive','/export'}
        assert '/output' in options['tmpfs']
        assert all('app' not in path and 'docker.sock' not in path for path in mounts)


@pytest.mark.parametrize('bad_pdf', [b'not-a-pdf', b'%PDF-' + b'x' * (20 * 1024 * 1024)])
def test_invalid_pdf_fails_closed(tmp_path, bad_pdf):
    tex = tmp_path/'public-tex'; tex.mkdir()
    engine = tex/'version'/'bin'/'arch'/'xelatex'; engine.parent.mkdir(parents=True); engine.write_bytes(b'synthetic')
    compiler = IsolatedResearchCompiler(FakeDocker(tex, bad_pdf), tmp_path/'private', 'original-service')
    with pytest.raises(ResearchUnavailable):
        compiler.compile(TASK, RUN, {'main.tex': MAIN}, compiler.prepare())


def test_bridge_requires_compiler_before_native_publish(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('native write must not start')
    monkeypatch.setattr('chattask.research.subprocess.Popen', forbidden)
    bridge = DockerOverleafBridge('original-service', 'https://example.org')
    with pytest.raises(ResearchUnavailable):
        bridge({'action':'publish','task_id':TASK,'run_id':RUN,'files':{'main.tex':MAIN}})


def test_bridge_publishes_source_only_then_returns_isolated_pdf_receipt(tmp_path):
    actions = []
    class Compiler:
        def prepare(self):
            actions.append('preflight')
            return {'synthetic': True}
        def compile(self, task_id, run_id, files, plan):
            actions.append('isolated-compile')
            assert (task_id, run_id, files, plan) == (TASK, RUN, {'main.tex':MAIN}, {'synthetic':True})
            return b'%PDF-1.7\n' + b'x'*30, {
                'image_id':IMAGE, 'engine':'/usr/local/texlive/version/bin/arch/xelatex',
                'container_id':'d'*64, 'nonce':'e'*32, 'passes':2,
                'pdf_sha256':hashlib.sha256(b'%PDF-1.7\n' + b'x'*30).hexdigest(),
                'isolation':'no-network-read-only-rootfs-task-mounts'}
    bridge = DockerOverleafBridge('original-service','https://example.org',compiler=Compiler())
    def native(payload, *, on_start=None, on_exit=None):
        actions.append(payload['action'])
        assert payload['action'] == 'publish_source'
        if on_start: on_start(12345,'synthetic-process-identity')
        if on_exit: on_exit(12345,'synthetic-process-identity',0,{'status':'ok'})
        return {'status':'ok','owner_verified':True,'project_id':'c'*24,
                'root_doc_id':'d'*24,'previous_root_doc_id':None,
                'compiler':'xelatex', 'source_files':{f'r_{RUN}_main.tex':MAIN},
                'provenance':'original-service-document-readback'}
    bridge._native_call = native
    result = ResearchPublisher(tmp_path/'state',bridge).publish(TASK,RUN,{'main.tex':MAIN},'request-1')
    assert actions == ['preflight','publish_source','isolated-compile']
    assert result.receipt['isolated_compiler']['image_id'] == IMAGE
    assert result.pdf_bytes.startswith(b'%PDF-')


def test_compiler_cleanup_uncertainty_fails_closed(tmp_path):
    tex = tmp_path/'public-tex'; tex.mkdir()
    engine = tex/'version'/'bin'/'arch'/'xelatex'; engine.parent.mkdir(parents=True); engine.write_bytes(b'synthetic')
    client = FakeDocker(tex)
    original = client.create
    def uncertain(**kwargs):
        container = original(**kwargs)
        def fail_remove(force=False): raise RuntimeError('synthetic uncertain cleanup')
        container.remove = fail_remove
        return container
    client.create = uncertain
    compiler = IsolatedResearchCompiler(client,tmp_path/'private','original-service')
    with pytest.raises(ResearchUnavailable, match='cleanup uncertain'):
        compiler.compile(TASK,RUN,{'main.tex':MAIN},compiler.prepare())


def test_missing_compiler_preflight_makes_no_native_intent_or_write(tmp_path):
    class Missing:
        def prepare(self): raise ResearchUnavailable('synthetic image missing')
    bridge = DockerOverleafBridge('original-service','https://example.org',compiler=Missing())
    bridge._native_call = lambda *args, **kwargs: pytest.fail('native write must not start')
    publisher = ResearchPublisher(tmp_path/'state',bridge)
    with pytest.raises(ResearchUnavailable, match='synthetic image missing'):
        publisher.publish(TASK,RUN,{'main.tex':MAIN},'request-1')
    assert not (tmp_path/'state'/TASK/'state.json').exists()


def test_native_helper_has_source_only_action_and_no_compile_manager():
    source = (Path(__file__).resolve().parents[2] / 'src/chattask/overleaf_native.mjs').read_text()
    assert "'publish_source'" in source
    assert 'CompileManager.promises.compile' not in source
