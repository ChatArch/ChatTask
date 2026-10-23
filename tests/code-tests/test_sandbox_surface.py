"""The task prototype must become a real importable/servable package."""
import unittest
from click.testing import CliRunner
import chattask
from chattask.cli import main

class PackageSurfaceTests(unittest.TestCase):
    def test_importable_business_api_is_exported(self):
        self.assertTrue(hasattr(chattask, 'TaskService'), 'TaskService is not yet part of ChatTask')
        self.assertTrue(hasattr(chattask, 'TaskSettings'))
        self.assertTrue(hasattr(chattask, 'create_app'))
        self.assertTrue(hasattr(chattask, 'state_paths'))

    def test_serve_is_a_real_cli_entry(self):
        result = CliRunner().invoke(main, ['serve','--help'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('--host', result.output)
        self.assertIn('--port', result.output)
        self.assertIn('--env-profile', result.output)

if __name__=='__main__':
    unittest.main(verbosity=2)
