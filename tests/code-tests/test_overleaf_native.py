"""Behavioral slices of the static helper, with synthetic original-service modules."""

import json
import os
import subprocess
import zipfile
import hashlib
from pathlib import Path

import pytest

from chattask.research import ResearchError, _check_tex


HELPER = Path(__file__).resolve().parents[2] / "src/chattask/overleaf_native.mjs"


def _node_tex_outcomes(samples):
    source = HELPER.read_text()
    constants = source[source.index("const TEX_COMMANDS"):source.index("function fail(")]
    validator = source[source.index("function validateTex("):source.index("async function readInput(")]
    script = (constants + "\nfunction fail(code){throw Error(code)}\n" + validator +
              "\nconst samples=" + json.dumps(samples) + ";\n" +
              "console.log(JSON.stringify(samples.map(s=>{try{validateTex(s);return 'ok'}catch(e){return e.message}})))")
    done = subprocess.run(["node", "--input-type=module", "--eval", script], text=True, capture_output=True, timeout=5)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_rejects_hostile_tex_in_both_preflights():
    safe = r"\documentclass{ctexart}\begin{document}Body\end{document}"
    hostile = [safe.replace("Body", token) for token in (
        r"\openin0=/etc/passwd\read0 to\leak", r"\catcode`\@=11", r"\csname openin\endcsname",
        r"\directlua{os.execute('x')}", r"\write18{echo x}", r"^^5copenin0=/etc/passwd",
        r"\usepackage{evil}", r"\url{file:///etc/passwd}", "% unsafe comment",
        r"\begin{filecontents}{x}secret\end{filecontents}", "{unbalanced",
    )]
    assert _node_tex_outcomes([safe] + hostile)[0] == "ok"
    assert all(outcome != "ok" for outcome in _node_tex_outcomes(hostile))
    for tex in hostile:
        with pytest.raises(ResearchError):
            _check_tex(tex)


@pytest.mark.parametrize("env_name,expected_sha", [
    ("RESEARCH_FIXTURE_ZIP_FIRST", "7d197cc24dd37f6ed5e2e1760815a9dadb2ccf397f54b188a6002d6fe306438e"),
    ("RESEARCH_FIXTURE_ZIP_SECOND", "e20e92915d1b6ec00075e423e87fddf70beb75a4e1f2a591d6adb26b79a1002e"),
])
def test_published_task_owned_tex_fixture(env_name, expected_sha):
    path = os.environ.get(env_name)
    if not path:
        pytest.skip("read-only Task archive path supplied by supervisor/test command")
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith("_main.tex")]
        assert len(names) == 1
        tex = archive.read(names[0]).decode("utf-8")
    assert hashlib.sha256(tex.encode()).hexdigest() == expected_sha
    _check_tex(tex)
    assert _node_tex_outcomes([tex]) == ["ok"]


def _native_function(start, end, body, call):
    source = HELPER.read_text()
    section = source[source.index(start):source.index(end)]
    script = f"{body}\n{section}\n{call}\n"
    done = subprocess.run(["node", "--input-type=module", "--eval", script], text=True, capture_output=True, timeout=5)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_current_document_wins_over_stale_docstore():
    result = _native_function("async function contents(", "async function getRoundFiles(", """
const ProjectEntityHandler = { promises: { getDoc: async () => ({lines:['old submitted text']}) } }
const DocumentUpdaterHandler = { promises: { getDocument: async () => ({lines:['human edit']}) } }
function fail() { throw Error('failed') }
""", "console.log(JSON.stringify(await contents('project','doc')))")
    assert result == "human edit"


def test_human_edit_blocks_existing_doc_replay_before_compile():
    source = HELPER.read_text()
    contents = source[source.index("async function contents("):source.index("async function getRoundFiles(")]
    append = source[source.index("async function appendDocuments("):source.index("async function switchRoot(")]
    script = """
import crypto from 'node:crypto'
const task='a'.repeat(32), run='b'.repeat(32)
const project={_id:'project',owner_ref:'owner',description:`chattask-research:${task}`,rootFolder:[{_id:'folder',docs:[{_id:'doc',name:`r_${run}_main.tex`}]}]}
const Project={findById:()=>({exec:async()=>project})}
const ProjectEntityHandler={promises:{getDoc:async()=>({lines:['old submitted text']})}}
const DocumentUpdaterHandler={promises:{getDocument:async()=>({lines:['human edit']})}}
const ProjectEntityUpdateHandler={promises:{addDoc:async()=>{throw Error('unexpected write')}}}
function describe(id){return `chattask-research:${id}`}
function findFlatDoc(p,n){return p.rootFolder[0].docs.find(d=>d.name===n)}
function hash(x){return crypto.createHash('sha256').update(x).digest('hex')}
function fail(code){throw Error(code)}
""" + contents + append + """
let outcome='accepted'
try { await appendDocuments(project,'owner',{task_id:task,run_id:run,files:{'main.tex':'old submitted text'},file_sha256:{'main.tex':hash('old submitted text')}}) }
catch (error) { outcome=error.message }
console.log(JSON.stringify(outcome))
"""
    done = subprocess.run(["node", "--input-type=module", "--eval", script], text=True, capture_output=True, timeout=5)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == "document-conflict"


def test_native_source_publication_cannot_invoke_compiler():
    source = HELPER.read_text()
    assert "'publish_source'" in source
    assert 'CompileManager' not in source
    assert 'ClsiManager' not in source
    assert 'pdf_base64' not in source
