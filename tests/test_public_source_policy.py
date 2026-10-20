import importlib.util,unittest,json,copy
from pathlib import Path
from unittest.mock import Mock
spec=importlib.util.spec_from_file_location('reviewed_public_source',Path(__file__).resolve().parents[1]/'scripts/auto_sync.py')
auto=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto)
POLICY={'repositoryId':1421297083,'visibility':'public','scope':'source-only'}
VALID={'id':1421297083,'full_name':'saket3106/OfflineGPT','private':False,'archived':False,'disabled':False,'visibility':'public','permissions':{'push':True}}
class PublicSourcePolicy(unittest.TestCase):
    def manager(self,policy=None,repository=None):
        m=auto.Manager.__new__(auto.Manager);m.local_fixture=False
        m.cfg={'remoteUrl':'https://github.com/saket3106/OfflineGPT.git'}
        m.publication_policy=policy;m.github_api=Mock(return_value=copy.deepcopy(VALID if repository is None else repository));return m
    def test_legacy_configuration_remains_private_only(self):
        self.assertIsNone(auto.publication_policy({'version':1}))
        with self.assertRaises(auto.Error):self.manager().remote_policy()
    def test_private_legacy_policy_still_passes(self):
        repo={**VALID,'private':True,'visibility':'private'}
        result=self.manager(repository=repo).remote_policy()
        self.assertTrue(result['privateVerified']);self.assertNotIn('scope',result)
    def test_explicit_public_source_opt_in(self):
        result=self.manager(auto.publication_policy({'version':2,'publicationPolicy':POLICY})).remote_policy()
        self.assertFalse(result['privateVerified']);self.assertEqual(result['scope'],'source-only');self.assertEqual(result['repositoryId'],1421297083)
    def test_publication_config_requires_exact_schema(self):
        for value in [None,{},True,{'repositoryId':1421297083},{**POLICY,'extra':True},{**POLICY,'scope':'binary'},{**POLICY,'visibility':'private'},{**POLICY,'repositoryId':True},{**POLICY,'repositoryId':0},{**POLICY,'repositoryId':'1421297083'}]:
            with self.subTest(value=value),self.assertRaises(auto.Error):auto.publication_policy({'version':2,'publicationPolicy':value})
    def test_legacy_cannot_smuggle_public_policy(self):
        with self.assertRaises(auto.Error):auto.publication_policy({'version':1,'publicationPolicy':POLICY})
    def test_unknown_config_versions_fail(self):
        for version in [True,False,0,3,'2',None]:
            with self.subTest(version=version),self.assertRaises(auto.Error):auto.publication_policy({'version':version})
    def test_remote_identity_and_visibility_drift_fail_closed(self):
        changes=[{'id':99},{'id':True},{'id':'1421297083'},{'full_name':'other/OfflineGPT'},{'private':True},{'private':0},{'archived':True},{'archived':0},{'disabled':True},{'disabled':0},{'visibility':'private'},{'permissions':{'push':False}},{'permissions':{'push':1}},{'permissions':{}}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(auto.Error):self.manager(POLICY,{**VALID,**change}).remote_policy()
    def test_missing_remote_evidence_fails_closed(self):
        for key in VALID:
            repo=copy.deepcopy(VALID);del repo[key]
            with self.subTest(key=key),self.assertRaises(auto.Error):self.manager(POLICY,repo).remote_policy()
    def test_network_failure_does_not_become_approval(self):
        m=self.manager(POLICY);m.github_api.side_effect=auto.NetworkError('Synthetic offline failure')
        with self.assertRaises(auto.NetworkError):m.remote_policy()
    def test_policy_copy_does_not_alias_caller(self):
        p=copy.deepcopy(POLICY);result=auto.publication_policy({'version':2,'publicationPolicy':p});p['repositoryId']=10
        self.assertEqual(result['repositoryId'],1421297083)
if __name__=='__main__':unittest.main()
