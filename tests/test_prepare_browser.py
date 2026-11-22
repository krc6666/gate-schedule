import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prepare_browser', ROOT / 'scripts' / 'prepare-browser.py')
PREPARE_BROWSER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE_BROWSER)


class CoiServiceWorkerPatchTests(unittest.TestCase):
    def test_no_content_statuses_bypass_response_reconstruction(self):
        source = '''                    if (response.status === 0) {
                        return response;
                    }
                    return new Response(response.body, {'''

        patched = PREPARE_BROWSER.patch_coi_serviceworker(source)

        guard = 'if ([0, 204, 205, 304].includes(response.status))'
        self.assertIn(guard, patched)
        self.assertLess(patched.index(guard), patched.index('return new Response'))

    def test_unrecognized_dependency_source_stops_build(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported coi-serviceworker'):
            PREPARE_BROWSER.patch_coi_serviceworker('changed upstream source')


if __name__ == '__main__':
    unittest.main()
