from pathlib import Path
import subprocess,unittest

class PackageUIDraftTests(unittest.TestCase):
 def test_reopened_draft_equality_does_not_depend_on_json_key_order(self):
  root=Path(__file__).resolve().parents[2]
  script=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const text=fs.readFileSync('src/chattask/web_static/app.js','utf8');
const match=/function packageDirty\(\) \{[\s\S]*?\n\}/.exec(text);assert(match);
const fields={package_name:'ExamplePkg',mode:'existing'};
const scope={packageDraft:{spec:{mode:'existing',package_name:'ExamplePkg'}},packageFields:()=>fields};
assert.strictEqual(vm.runInNewContext(match[0]+';packageDirty()',scope),false);
fields.mode='new';assert.strictEqual(vm.runInNewContext(match[0]+';packageDirty()',scope),true);
scope.packageDraft=null;assert.strictEqual(vm.runInNewContext(match[0]+';packageDirty()',scope),false);'''
  result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
