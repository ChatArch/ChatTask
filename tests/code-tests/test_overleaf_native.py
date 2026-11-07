"""Behavioral slices of the static helper, with synthetic original-service modules."""

import json
import subprocess
from pathlib import Path


HELPER = Path(__file__).resolve().parents[2] / "src/chattask/overleaf_native.mjs"


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


def test_null_clsi_server_id_is_valid_single_instance():
    source = HELPER.read_text()
    section = source[source.index("async function pdfFromCompile("):source.index("async function execute(")]
    script = """
const calls=[]
const MAX_PDF=20*1024*1024
const Settings={disablePerUserCompiles:true}
const ClsiManager={promises:{getOutputFileStream:async (...args)=>{calls.push(args);return (async function*(){yield Buffer.from('%PDF-1.7\\n'+ 'x'.repeat(30))})()}}}
function fail(code){throw Error(code)}
""" + section + "\nconst pdf=await pdfFromCompile('p','u',{status:'success',buildId:'build',clsiServerId:null,outputFiles:[{path:'output.pdf'}]});console.log(JSON.stringify({size:pdf.length,calls}))"
    done = subprocess.run(["node", "--input-type=module", "--eval", script], text=True, capture_output=True, timeout=5)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["calls"][0] == ["p", None, None, "build", "output.pdf"]
