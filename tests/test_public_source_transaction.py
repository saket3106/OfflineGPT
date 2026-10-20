"""Explicit version-2 config through disposable real Git transaction, no network."""
import unittest,json
import test_auto_sync as fixtures
class PublicConfigTransaction(unittest.TestCase):
    def test_exact_config_reaches_real_local_transaction_without_relaxing_reviews(self):
        f=fixtures.AutoSyncTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        f.cfg.update(version=2,publicationPolicy={'repositoryId':1421297083,'visibility':'public','scope':'source-only'})
        f.config.write_text(json.dumps(f.cfg))
        f.manager=fixtures.auto.Manager(f.config,local_fixture=True)
        batch=f.prepare();self.assertEqual(batch['status'],'AWAITING_REVIEW')
        with self.assertRaises(fixtures.auto.Error):f.manager.accept(batch['id'],batch['snapshotDigest'],{**fixtures.REVIEWS,'content':False})
        f.accept(batch);result=f.manager.once()
        self.assertEqual(result['status'],'COMPLETE');self.assertEqual(f.manager.remote_head(),result['commit'])
        self.assertEqual(result['binaryPublication'],'BLOCKED_NATIVE_PROVENANCE_AND_LICENSES')
        self.assertTrue(result['ci']['fixtureOnly']);self.assertFalse(f.g('status','--porcelain').strip())
    def test_v2_missing_opt_in_fails_before_state_mutation(self):
        f=fixtures.AutoSyncTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        f.cfg['version']=2;f.config.write_text(json.dumps(f.cfg));before=f.g('status','--porcelain')
        with self.assertRaises(fixtures.auto.Error):fixtures.auto.Manager(f.config,local_fixture=True)
        self.assertEqual(f.g('status','--porcelain'),before)
if __name__=='__main__':unittest.main()
