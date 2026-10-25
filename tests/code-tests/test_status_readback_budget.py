"""Aggregation latency is not a TLS failure; keep public-file reads shorter."""
import json,os,unittest
from unittest.mock import patch
from chattask.package_publication import _guest_tool,_bounded_get
class StatusReadbackBudgetTests(unittest.TestCase):
 def test_scoped_status_budget_covers_provider_aggregation(self):
  budgets=[];url='https://gateway.example.test/sessions/'+('a'*32)+'/github/api/status'
  class Response:
   status=200;headers={'content-length':'2'}
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def geturl(self):return url
   def read(self,limit):return b'{}'
  def fetch(request,timeout):
   budgets.append(timeout)
   if timeout<45:raise TimeoutError('simulated bounded provider aggregation')
   return Response()
  with patch.dict(os.environ,{'CHATTASK_TOOL_BASE_URL':url.removesuffix('/github/api/status'),'CHATTASK_TOOL_ACCESS_TOKEN':'example-tool-token'}),patch('urllib.request.urlopen',fetch):
   self.assertEqual(_guest_tool('/github/api/status'),{})
  self.assertEqual(budgets,[60])
 def test_public_file_budget_is_not_broadened(self):
  budgets=[];url='https://pypi.example.test/release.json'
  class Response:
   status=200;headers={'content-length':'2'}
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def geturl(self):return url
   def read(self,limit):return b'{}'
  def fetch(request,timeout):budgets.append(timeout);return Response()
  with patch('urllib.request.urlopen',fetch):self.assertEqual(_bounded_get(url,100),b'{}')
  self.assertEqual(budgets,[12])
