"""Read-only native task page must expose the real persisted analysis result."""
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def test_native_analysis_tab_and_authenticated_deep_link_are_wired():
    html=(ROOT/'src/chattask/web_static/index.html').read_text()
    js=(ROOT/'src/chattask/web_static/app.js').read_text()
    for ident in ('workspace-tab','workspace-view','workspace-picker','workspace-refresh',
                  'workspace-state','workspace-detail','workspace-runs','workspace-report'):
        assert f'id="{ident}"' in html
    assert "get('workspace_task')" in js
    assert 'await initializeWorkspace(' in js
    clear=re.search(r'function clearTemplateState\(\) \{([\s\S]*?)\n\}',js).group(1)
    assert 'clearWorkspaceState()' in clear
    assert "$('workspace-view').hidden = key !== 'workspace'" in js


def test_workspace_result_renderer_is_read_only_text_and_rejects_stale_responses():
    js=(ROOT/'src/chattask/web_static/app.js').read_text()
    assert '// Native Workspace material-analysis results' in js
    section=js.split('// Native Workspace material-analysis results',1)[1].split('\n',1)[1]
    assert 'innerHTML' not in section and '.textContent' in section
    assert not re.search(r"request\([^\n]+['\"](?:POST|PUT|DELETE|PATCH)['\"]",section)
    script=r'''
const vm=require('vm'),assert=require('assert');
const elements=new Map();
function element(){return {textContent:'',value:'',children:[],classList:{toggle(){}},
 append(...xs){this.children.push(...xs)},replaceChildren(...xs){this.children=xs},addEventListener(){}}}
const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
const document={createElement:()=>element()};
const option=(value,textContent)=>({value,textContent});
let calls=[],firstResolve;
async function request(path){calls.push(path);
 if(path.endsWith('/report'))return {text:'<script>not executed</script>',sha256:'a'.repeat(64)};
 const id=path.split('/').pop();
 if(id==='a'.repeat(32))return await new Promise(r=>firstResolve=r);
 return {task:{id,title:'real persisted task',version:1,status:'confirmed',source:{},source_digest:'d',
 instructions:'only provided facts',acceptance:'result',context:[],runs:[{id:'c'.repeat(32),status:'succeeded',model:'actual-model',report:{name:'report.md'}}]}};
}
const show=(id,text)=>{$(id).textContent=text};
let authenticated=true;
''' + section + r'''
(async()=>{
 let old=loadWorkspaceTask('a'.repeat(32));
 await loadWorkspaceTask('b'.repeat(32));
 firstResolve({task:{id:'a'.repeat(32),title:'STALE',runs:[],context:[],source:{}}});
 await old;
 assert(!$('workspace-detail').textContent.includes('STALE'));
 assert($('workspace-detail').textContent.includes('real persisted task'));
 await loadWorkspaceReport('b'.repeat(32),'c'.repeat(32));
 assert.strictEqual($('workspace-report').textContent,'<script>not executed</script>');
 clearWorkspaceState();
 assert.strictEqual($('workspace-report').textContent,'');
 assert(calls.every(x=>x.startsWith('/api/workspace-tasks/')));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result=subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
