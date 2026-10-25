import asyncio
import tempfile
import os
import unittest
from chattask.agent_tasks import AgentTaskService
from chattask.workflow import TaskStore

class RecoveryTests(unittest.TestCase):
 def test_restart_marks_orphaned_runs_without_replaying_external_work(self):
  with tempfile.TemporaryDirectory(dir=os.environ['CHATTASK_TEST_SCRATCH']) as root:
   store=TaskStore(root)
   for ident,status,engine in [('a'*32,'running','codex'),('b'*32,'queued','codex'),('c'*32,'completed','codex'),('d'*32,'running','template')]:
    store.put('runs',ident,{'id':ident,'owner':'owner','engine':engine,'status':status,'cleanup':None})
   service=AgentTaskService(store)
   asyncio.run(service.recover())
   self.assertEqual(store.get('runs','a'*32)['status'],'interrupted')
   self.assertEqual(store.get('runs','b'*32)['status'],'interrupted')
   self.assertEqual(store.get('runs','c'*32)['status'],'completed')
   self.assertEqual(store.get('runs','d'*32)['status'],'running')
   self.assertIsNone(store.get('runs','a'*32)['cleanup'])
   self.assertEqual(service.tasks,{})
