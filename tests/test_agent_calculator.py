import tempfile,unittest
from agent_mcp import WorkspaceTools,validate_arguments
from agent_permissions import GrantAuthority
from agent_workspace import WorkspaceAccess

class AgentCalculatorTests(unittest.TestCase):
 def setUp(self):
  self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
  self.access=WorkspaceAccess(self.folder.name);self.addCleanup(self.access.close)
  self.tools=WorkspaceTools(self.access,GrantAuthority(b'x'*32))
 def test_independent_arithmetic_and_no_files(self):
  self.assertEqual(self.tools.call('calculate',{'expression':'(240 * 1.05) + 48'})['value'],300)
  self.assertEqual(self.tools.call('calculate',{'expression':'round(sqrt(81) / 4, 2)'})['value'],2.25)
  self.assertEqual(self.access.files()['files'],[])
 def test_unsafe_expressions_and_nonfinite_results_rejected(self):
  for expression in ['__import__("os").system("whoami")','open("x","w")','[1]*100000000','True+1','1e309','2**1000','sqrt(-1)','(-1)**0.5','1/0','round(1,ndigits=2)','9'*513]:
   with self.subTest(expression=expression):
    with self.assertRaises((ValueError,TypeError,ZeroDivisionError,OverflowError,SyntaxError)):self.tools.call('calculate',{'expression':expression})
 def test_tool_schema_is_exact(self):
  with self.assertRaises(ValueError):validate_arguments('calculate',{'expression':'2+2','code':'evil'})
