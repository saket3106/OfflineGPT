"""Independent deterministic integration criteria; not native vision evidence."""
import asyncio,hashlib,json,sqlite3,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from agent_mcp import WorkspaceTools,TOOL_FIELDS,ToolContractError,validate_arguments,capability_catalog
from agent_permissions import GrantAuthority
from agent_workspace import WorkspaceAccess
from agent_resources import AttachmentRegistry,AgentSpecialists,inspect_workspace
from agent_runtime import AgentRuntime

class Client:
    calls=[];results={}
    def __init__(self,*a,**k):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def call(self,name,args,**kw):self.calls.append((name,args));return self.results.get(name,{'text':'actual permitted content'})

class OrchestrationTests(unittest.TestCase):
    def test_single_image_text_observation_is_not_treated_as_batch_records(self):
        task,_,run=self.runtime([{'tool':'inspect_image','arguments':{'source':'attachment:known','question':'Describe pixels'}},
                                {'final':'The supplied image contains a blue cube.'}],'Describe this image')
        task.resources=[{'id':'attachment:known','mediaType':'image/png'}]
        task.specialists=SimpleNamespace(previews=[],call=lambda *args:{'source':'attachment:known','pixelInferencePerformed':True,'provider':'fixture','observations':'A blue cube.'})
        run();self.assertEqual(task.state,'COMPLETED');self.assertEqual(task.evidence[0]['visualSources'],[])
        self.assertTrue(task.evidence[0]['pixelInferencePerformed']);self.assertIn('blue cube',task.final)
    def test_context_compaction_preserves_values_sources_and_detailed_hashes(self):
        from agent_runtime import compact_agent_observations
        observations=[{'tool':'inspect_workspace','result':{'inspected':[{'source':'sample.csv','sha256':'a'*64,'excerpt':'quantity=47; price=19','truncated':True}],'coverageComplete':False}},
                      {'tool':'calculate','result':{'value':893,'expression':'47*19'}},
                      {'tool':'inspect_source_code','result':{'path':'logic.py','sha256':'b'*64,'source':'return candidate'}}]
        messages=[{'role':'system','content':'policy'},{'role':'user','content':'original request'},
                  {'role':'user','content':'Completion evidence missing. Review code.'}]+[{'role':'user','content':'UNTRUSTED TOOL OBSERVATION: '+json.dumps(o)} for o in observations]
        fitted,removed=compact_agent_observations(messages)
        self.assertEqual(removed,1);self.assertEqual(fitted[:2],messages[:2]);self.assertEqual(len(messages),6)
        body=json.dumps(fitted);self.assertIn('quantity=47; price=19',body);self.assertIn('sample.csv',body)
        self.assertIn('893',body);self.assertIn('b'*64,body);self.assertIn('coverageComplete',body)
        self.assertNotIn('a'*64,body);self.assertNotIn('Completion evidence missing.',body)
        batch={'tool':'search_images','result':{'observations':[{'source':'workspace:example.jpg','name':'example.jpg','provider':'actual vision','sha256':'c'*64,'observations':'A green bicycle.','pixelInferencePerformed':True}],'coverageComplete':False}}
        projected,removed=compact_agent_observations(messages[:2]+[{'role':'user','content':'UNTRUSTED TOOL OBSERVATION: '+json.dumps(batch)}])
        body=json.dumps(projected);self.assertEqual(removed,1);self.assertIn('A green bicycle.',body);self.assertIn('workspace:example.jpg',body)
        self.assertIn('actual vision',body);self.assertIn('pixelInferencePerformed',body);self.assertNotIn('c'*64,body)
    def test_registered_attachment_name_resolves_without_folder_expansion(self):
        image=self.root/'photo.png';Image.new('RGB',(12,12),'blue').save(image)
        registry=AttachmentRegistry();record=registry.register([str(image)],'chat')[0]
        access=WorkspaceAccess(self.root)
        try:
            service=AgentSpecialists(None,access,registry,workspace_authorized=False)
            self.assertEqual(service.read_image('photo.png')[1]['id'],record['id'])
            with self.assertRaises(PermissionError):service.read_image('../photo.png')
            other=self.root/'other';other.mkdir();Image.new('RGB',(12,12),'red').save(other/'photo.png')
            registry.register([str(other/'photo.png')],'chat')
            with self.assertRaisesRegex(PermissionError,'Ambiguous'):registry.read('photo.png')
            self.assertEqual(registry.read(record['id'])[1]['sha256'],record['sha256'])
        finally:access.close()
    def test_reconciliation_and_code_review_require_runtime_evidence(self):
        Client.results={'inspect_workspace':{'inspected':[{'source':'calc.py'}],'relevantFiles':1,'coverageComplete':True},
                        'calculate':{'value':188,'expression':'31*6+2'},'inspect_source_code':{'source':'return value','executed':False}}
        task,events,run=self.runtime([{'tool':'inspect_workspace','arguments':{}},{'final':'Everything matches.'},
            {'tool':'calculate','arguments':{'expression':'31*6+2'}},{'tool':'inspect_source_code','arguments':{'path':'calc.py'}},
            {'final':'The calculated total is 188; static code review is separate from execution.'}],
            'Analyze the folder, reconcile pricing and review the Python script.')
        run();self.assertEqual(task.state,'COMPLETED');self.assertTrue(any(e['state']=='VERIFYING' for e in events))
        self.assertEqual([n for n,_ in Client.calls],['inspect_workspace','calculate','inspect_source_code'])
    def test_missing_requested_verification_never_counts_as_complete(self):
        task,_,run=self.runtime([{'final':'Pricing reconciles.'},{'final':'Pricing reconciles.'}],'Reconcile invoice pricing')
        run();self.assertEqual(task.state,'PARTIAL');self.assertIn('verification is incomplete',task.final)
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);Client.calls=[];Client.results={}
    def tearDown(self):self.temp.cleanup()
    def runtime(self,plans,goal,**kwargs):
        values=iter(plans);events=[];task=AgentRuntime(lambda *_:json.dumps(next(values)),self.root,None,'chat',events.append,client_factory=Client,**kwargs)
        return task,events,lambda:asyncio.run(task.run(goal))
    def test_missing_argument_has_specific_diagnostic(self):
        with self.assertRaises(ToolContractError) as error:validate_arguments('read_local_file',{})
        self.assertEqual(error.exception.code,'ARGUMENT_FIELDS');self.assertIn('path',str(error.exception))
    def test_unknown_tool_does_not_execute_and_can_be_corrected_once(self):
        task,events,run=self.runtime([{'tool':'unregistered_shell','arguments':{}},{'tool':'read_local_file','arguments':{'path':'evidence.txt'}},{'final':'Evidence inspected.'}],'Read evidence.txt')
        run();self.assertEqual(Client.calls,[('read_local_file',{'path':'evidence.txt'})]);self.assertEqual(task.state,'COMPLETED')
        self.assertEqual(next(e for e in events if e['state']=='REPAIRING_FORMAT')['contract']['code'],'UNKNOWN_TOOL')
    def test_missing_path_repair_never_guesses_or_replays(self):
        task,_,run=self.runtime([{'tool':'read_local_file','arguments':{}},{'tool':'read_local_file','arguments':{'path':'data.txt'}},{'final':'Data inspected.'}],'Read data.txt')
        run();self.assertEqual(Client.calls,[('read_local_file',{'path':'data.txt'})])
    def test_repeated_bad_contract_stops_without_dispatch(self):
        task,_,run=self.runtime([{'tool':'read_local_file','arguments':{}},{'tool':'read_local_file','arguments':{'unexpected':'x'}}],'Read the selected file')
        run();self.assertEqual(task.state,'FAILED');self.assertFalse(Client.calls)
    def test_wrong_types_optional_invention_and_duplicate_json_stay_rejected(self):
        for tool,args in [('read_local_file',{'path':12}),('list_workspace_files',{'recursive':'true'}),('inspect_image',{'source':'x'})]:
            with self.assertRaises(ToolContractError):validate_arguments(tool,args)
        from agent_runtime import parse_plan
        with self.assertRaises(ValueError):parse_plan('{"final":"a","final":"b"}')
    def test_directory_listing_alone_never_becomes_completed_analysis(self):
        task,_,run=self.runtime([{'tool':'list_workspace_files','arguments':{}},{'final':'Entire folder analyzed.'},{'final':'Entire folder analyzed.'}],'Analyze this folder')
        run();self.assertEqual(task.state,'PARTIAL');self.assertIn('only inventory',task.final)
    def test_folder_feedback_requests_real_content_without_executing_a_fixed_plan(self):
        Client.results={'inspect_workspace':{'inspected':[{'source':'a.txt','excerpt':'Grounded statement'}],'relevantFiles':1,'coverageComplete':True}}
        task,_,run=self.runtime([{'tool':'list_workspace_files','arguments':{}},{'final':'Folder analyzed.'},{'tool':'inspect_workspace','arguments':{}},{'final':'a.txt contains the grounded statement.'}],'Analyze this folder')
        run();self.assertEqual(task.state,'COMPLETED');self.assertEqual([n for n,_ in Client.calls],['list_workspace_files','inspect_workspace']);self.assertIn('1 files inspected',task.final)
    def test_visual_goal_cannot_retrieve_history_or_claim_unseen_pixels(self):
        task,_,run=self.runtime([{'tool':'search_conversation_history','arguments':{'query':'image'}}],'Analyze the attached image')
        calls=[];task.history=lambda *a:calls.append(a)
        run();self.assertEqual(task.state,'FAILED');self.assertFalse(calls);self.assertFalse(Client.calls)
    def test_fake_visual_final_remains_partial(self):
        task,_,run=self.runtime([{'final':'The car is green.'},{'final':'The car is green.'}],'Describe this image')
        task.resources=[{'id':'attachment:known','mediaType':'image/png'}]
        run();self.assertEqual(task.state,'PARTIAL');self.assertIn('no image inference',task.final)
    def test_real_mixed_inspection_reports_contents_and_does_not_run_python(self):
        (self.root/'notes.txt').write_text('The order contains 37 boxes.');(self.root/'broken.py').write_text('def maximum(items):\n    return 0\n')
        (self.root/'bad.json').write_text('{not valid json}');(self.root/'counts.csv').write_text('name,count\na,37\n')
        access=WorkspaceAccess(self.root)
        try:value=inspect_workspace(access)
        finally:access.close()
        self.assertTrue(value['notExecuted']);self.assertIn('37',json.dumps(value));self.assertTrue(any(r['source']=='bad.json' for r in value['unreadable']));self.assertTrue(any(r.get('functions')==['maximum'] for r in value['inspected']))
    def test_folder_sampling_is_bounded_and_honest(self):
        for i in range(25):(self.root/f'{i:02}.txt').write_text(f'Observed record {i}')
        access=WorkspaceAccess(self.root)
        try:value=inspect_workspace(access)
        finally:access.close()
        self.assertFalse(value['coverageComplete']);self.assertLessEqual(len(value['inspected']),8);self.assertTrue(value['uninspected'])
    def test_large_image_directory_is_metadata_bounded(self):
        for i in range(300):(self.root/f'candidate{i}.png').write_bytes(b'not yet decoded')
        access=WorkspaceAccess(self.root)
        try:value=access.files()
        finally:access.close()
        self.assertEqual(len(value['files']),256);self.assertTrue(value['truncated'])
    def test_attachment_is_independent_of_workspace_and_pinned_to_bytes(self):
        with tempfile.TemporaryDirectory() as outside:
            path=Path(outside)/'selected.png';Image.new('RGB',(20,20),'red').save(path);registry=AttachmentRegistry();row=registry.register([str(path)],'chat')[0]
            data,observed=registry.read(row['id']);self.assertEqual(observed['sha256'],hashlib.sha256(data).hexdigest())
            self.assertNotIn('path',registry.public()[0]);Image.new('RGB',(20,20),'blue').save(path)
            with self.assertRaisesRegex(ValueError,'changed after intake'):registry.read(row['id'])
            with self.assertRaises(PermissionError):registry.read('attachment:invented')
    def test_vision_unavailable_identifies_existing_attachment(self):
        path=self.root/'photo.png';Image.new('RGB',(20,20)).save(path);registry=AttachmentRegistry();row=registry.register([str(path)],'c')[0];access=WorkspaceAccess(self.root)
        try:
            engine=SimpleNamespace(vision_model_status=lambda:{'visionAvailable':False,'visionMessage':'Projector is missing'})
            service=AgentSpecialists(engine,access,registry)
            with self.assertRaisesRegex(RuntimeError,'attachment exists; Projector'):service.inspect_image(row['id'],'Describe',threading.Event())
        finally:access.close()
    def test_visual_batch_reuses_one_lifecycle_and_cached_evidence(self):
        calls=[]
        for name in ('a.png','b.png','misleading-porsche.png'):Image.new('RGB',(20,20),'blue').save(self.root/name)
        def infer(requests,**kw):calls.append(requests);return ['A blue square; no visible vehicle evidence.']*len(requests)
        engine=SimpleNamespace(vision_model_status=lambda:{'visionAvailable':True,'visionModel':'fixture'},vision_completions=infer);access=WorkspaceAccess(self.root)
        try:
            service=AgentSpecialists(engine,access,AttachmentRegistry());value=service.search_images('black car',threading.Event())
            self.assertEqual(len(calls),1);self.assertEqual(value['examined'],3);self.assertTrue(value['coverageComplete']);self.assertFalse(value.get('matches'))
            service.search_images('black car',threading.Event());self.assertEqual(len(calls),1)
        finally:access.close()
    def test_host_document_requires_preview_approval_and_preserves_original(self):
        access=WorkspaceAccess(self.root);service=AgentSpecialists(None,access,AttachmentRegistry());args={'destination':'report.pdf','markdown':'# Independent report\n\nThese observations are supplied by an actual local evidence source.'}
        try:
            with self.assertRaises(PermissionError):service.call('create_workspace_document',args,threading.Event())
            preview=service.preview_document(args);self.assertIn('renderedSha256',preview)
            result=service.call('create_workspace_document',args,threading.Event(),True)
            self.assertEqual((self.root/'report.pdf').read_bytes()[:5],b'%PDF-');self.assertFalse(result['originalOverwritten'])
            with self.assertRaises(OSError):service.call('create_workspace_document',args,threading.Event(),True)
            with self.assertRaises(PermissionError):service.preview_document({**args,'destination':'../escape.pdf'})
        finally:access.close()
    def test_registry_schema_is_the_actual_argument_contract(self):
        catalog=capability_catalog({'visionAvailable':False})
        self.assertEqual(set(catalog),set(TOOL_FIELDS))
        for name,item in catalog.items():self.assertEqual(set(item['inputSchema']['required']),set(TOOL_FIELDS[name]))
        self.assertEqual(catalog['inspect_image']['availability'],'unavailable')
    def test_capability_question_without_an_image_does_not_invent_intake(self):
        task,_,run=self.runtime([{'final':'Attach a supported image to request visual analysis.'}],'If I give you an image, can you analyze it?')
        run();self.assertEqual(task.state,'ANSWERED');self.assertFalse(Client.calls)
    def test_filename_only_search_does_not_claim_visual_matching(self):
        task,_,run=self.runtime([{'tool':'list_workspace_files','arguments':{}},{'final':'The file named portrait.png is listed; pixels were not inspected.'}],'Find the image named portrait.png')
        run();self.assertEqual(task.state,'COMPLETED')
    def test_semantic_image_search_cannot_finish_from_filenames_only(self):
        task,_,run=self.runtime([{'tool':'list_workspace_files','arguments':{}},{'final':'I found the requested image.'},{'final':'I found it.'}],'Find a shiny blue vehicle image')
        run();self.assertEqual(task.state,'PARTIAL');self.assertIn('no image inference',task.final)
    def test_idle_voice_cancel_preserves_completed_receipt(self):
        import app
        for status in ('success','recognizing'):
            calls=[];profile={'status':status};fake=SimpleNamespace(voice_turn_id='original',voice_profiles={'original':profile},store=SimpleNamespace(workspace=SimpleNamespace(finish=lambda *a:calls.append(a))),voice_lock=threading.RLock(),voice_audio=bytearray(),voice_recording=False,voice_worker=None,emit_frontend=lambda _:None,_save_voice_profile=lambda _:None)
            app.App.cancel_voice_input(fake)
            self.assertEqual(calls,[] if status=='success' else [('original','cancelled')]);self.assertEqual(profile['status'],'success' if status=='success' else 'cancelled')
