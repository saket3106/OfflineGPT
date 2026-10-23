import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from model_runtime import ModelInfo,ModelBehavior,Family,TemplateContentStream,estimate_memory,EngineTextRuntime,GIB
from integrated_runtime import _LocalLlamaServer,load_config,EngineManager


def info(arch='qwen3',**overrides):
    metadata={'general.architecture':arch,'general.name':'Model metadata name',arch+'.context_length':8192,
              arch+'.block_count':28,arch+'.embedding_length':2048,arch+'.attention.head_count':16,
              arch+'.attention.head_count_kv':4,'tokenizer.chat_template':{'sha256':'a'*64,'characters':14},'general.file_type':15}
    metadata.update(overrides)
    return ModelInfo.from_inspection({'metadata':metadata,'fileSizeBytes':2*GIB,'headerMetadataSha256':'b'*64,'fileObservation':(1,2,3,4,5),'warnings':[]})


class ModelRuntimeTests(unittest.TestCase):
    def test_vision_preflight_releases_text_and_blocks_before_native_allocation(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'vision.gguf';projector=Path(folder)/'projector.gguf'
            model.write_bytes(b'model');projector.write_bytes(b'projector')
            engine=EngineManager.__new__(EngineManager);engine.lock=threading.RLock();engine.resource_lock=threading.RLock()
            engine.cfg={};engine.shutting_down=False;engine.runtime=Mock();engine._resolve_vision_model=lambda **_: (str(model),str(projector))
            released=[];engine._unload_chat_cancellable=lambda *args:released.append(True)
            with patch('integrated_runtime.physical_memory_status',return_value={'available':128*1024**2}):
                with self.assertRaisesRegex(RuntimeError,'VISION_RESOURCE_BLOCKED.*supplied image remains available'):
                    engine.vision_completions([[]])
            self.assertEqual(released,[True]);engine.runtime.start.assert_not_called()
    def test_vision_with_headroom_retains_sequential_lifecycle_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'vision.gguf';projector=Path(folder)/'projector.gguf'
            model.write_bytes(b'model');projector.write_bytes(b'projector')
            engine=EngineManager.__new__(EngineManager);engine.lock=threading.RLock();engine.resource_lock=threading.RLock()
            engine.cfg={};engine.shutting_down=False;engine.runtime=Mock();engine.chat_load_state='ready'
            engine._resolve_vision_model=lambda **_: (str(model),str(projector));engine._unload_chat_cancellable=lambda *args:None
            engine.runtime.complete_vision.side_effect=['first','second']
            with patch('integrated_runtime.physical_memory_status',return_value={'available':2*GIB}):
                self.assertEqual(engine.vision_completions([[],[]]),['first','second'])
            engine.runtime.start.assert_called_once();engine.runtime.stop.assert_called_once()
            self.assertEqual(engine.loaded_model,'');self.assertEqual(engine.runtime.complete_vision.call_count,2)

    def test_private_model_http_ignores_environment_and_global_proxy(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import os
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok"}')
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'received':data}).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'http_proxy':'http://127.0.0.1:1','no_proxy':''}):
                runtime=_LocalLlamaServer(directory);runtime.port=server.server_port
                runtime.process=Mock();runtime.process.poll.return_value=None
                with patch('urllib.request.urlopen',side_effect=AssertionError('Global opener must not own private traffic')):
                    self.assertTrue(runtime._is_healthy())
                    self.assertEqual(runtime._json_request('/echo',{'test':'local'}),{'received':{'test':'local'}})
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_metadata_never_becomes_execution_or_backend_evidence(self):
        for arch,family in [('gpt-oss',Family.GPT_OSS),('qwen3',Family.QWEN3),('qwen35',Family.QWEN35),('llama',Family.LLAMA),('gemma3',Family.GEMMA),('unknown',Family.OTHER)]:
            model=info(arch);self.assertEqual(model.family,family)
            report=model.capability_report();self.assertFalse(report['nativeCompatible'])
            self.assertEqual(report['executionVerified'],[]);self.assertIn('NOT_ESTABLISHED',report['backendSupported'])
        self.assertEqual(info('unknown',**{'general.name':'GPT-OSS Qwen3.5 vision tool expert'}).family,Family.OTHER)

    def test_gpt_harmony_options_preserved_and_not_sent_to_other_families(self):
        legacy=ModelBehavior();self.assertTrue(legacy.harmony)
        self.assertEqual(legacy.generation_options('high',600),{'chat_template_kwargs':{'reasoning_effort':'high'},'reasoning_budget_tokens':200})
        for arch in ['qwen3','qwen35','llama','gemma3','unknown']:
            behavior=ModelBehavior.from_model(info(arch))
            self.assertFalse(behavior.harmony);self.assertEqual(behavior.template_kwargs('high'),{})
            self.assertEqual(behavior.generation_options('high',600),{})

    def test_inspected_metadata_snapshot_cannot_be_mutated(self):
        model=info()
        with self.assertRaises(TypeError):model.metadata['general.architecture']='gpt-oss'
        with self.assertRaises(TypeError):model.metadata['tokenizer.chat_template']['sha256']='changed'

    def test_observed_qwen_thinking_control_keeps_low_effort_output_visible(self):
        template={'sha256':'a'*64,'characters':100,'enableThinkingBranchObserved':True}
        behavior=ModelBehavior.from_model(info('qwen3',**{'tokenizer.chat_template':template}))
        self.assertEqual(behavior.template_kwargs('low'),{'enable_thinking':False})
        self.assertEqual(behavior.generation_options('low',700),{'chat_template_kwargs':{'enable_thinking':False}})
        self.assertEqual(behavior.template_kwargs('high'),{'enable_thinking':True})
        self.assertEqual(behavior.template_kwargs('medium'),{'enable_thinking':False})
        for family in ['qwen35','llama','gemma3','unknown']:
            self.assertEqual(ModelBehavior.from_model(info(family,**{'tokenizer.chat_template':template})).template_kwargs('low'),{})
        self.assertEqual(ModelBehavior.from_model(info('qwen3')).template_kwargs('low'),{})

    def test_qwen_token_count_uses_the_same_thinking_control_as_generation(self):
        template={'sha256':'a'*64,'characters':100,'enableThinkingBranchObserved':True}
        runtime=_LocalLlamaServer(Path.cwd());runtime.behavior=ModelBehavior.from_model(info('qwen3',**{'tokenizer.chat_template':template}))
        seen=[]
        def request(endpoint,payload,timeout):
            seen.append((endpoint,payload));return {'prompt':'rendered template'} if endpoint=='/apply-template' else {'tokens':[1,2]}
        runtime._json_request=request
        self.assertEqual(runtime._prompt_tokens([]),2)
        self.assertEqual(seen[0][1]['chat_template_kwargs'],runtime.behavior.generation_options('low',700)['chat_template_kwargs'])

    def test_structured_agent_payload_is_bounded_copied_and_separate_from_native_tools(self):
        from integrated_runtime import structured_agent_format
        behavior=ModelBehavior(Family.QWEN3);schema={'type':'object','properties':{'final':{'type':'string'}}}
        output=structured_agent_format(schema,behavior,[])
        self.assertEqual(output,{'type':'json_object','schema':schema})
        output['schema']['properties'].clear();self.assertTrue(schema['properties'])
        for value in [[],{'description':'x'*17000},{'invalid':float('nan')}]:
            with self.assertRaises(ValueError):structured_agent_format(value,behavior,[])
        with self.assertRaises(ValueError):structured_agent_format(schema,ModelBehavior(),[])
        with self.assertRaises(ValueError):structured_agent_format(schema,behavior,[{'type':'function'}])
        self.assertIsNone(structured_agent_format(None,ModelBehavior(),[]))

    def test_header_marketing_quantization_never_overrides_gguf_identity(self):
        for header in ['Example model AWQ','Example model GPTQ']:
            model=info('qwen3',**{'general.name':header,'general.file_type':15})
            self.assertEqual(model.display_name,'Example model · GGUF Q4_K_M')
            self.assertEqual(model.capability_report()['declared']['name'],header)
            self.assertFalse(model.capability_report()['nativeCompatible'])
        self.assertEqual(info('gpt-oss',**{'general.name':'GPT-OSS 20B'}).display_name,'GPT-OSS 20B')

    def test_persisted_turn_uses_its_own_architecture_not_current_selection(self):
        from model_runtime import message_visible_text
        literal='Example: <|channel|>final<|message|> is a literal control-token example.'
        identity={'model_identity':{'architecture':'qwen3'}}
        self.assertEqual(message_visible_text(literal,identity),literal)
        self.assertEqual(message_visible_text(literal,json.dumps(identity)),literal)
        self.assertEqual(message_visible_text('<think>private</think>safe',identity),'safe')

    def test_template_token_count_uses_the_same_model_options(self):
        runtime=_LocalLlamaServer(Path.cwd());runtime.behavior=ModelBehavior.from_model(info())
        seen=[]
        def request(endpoint,payload,timeout):
            seen.append((endpoint,payload));return {'prompt':'rendered template'} if endpoint=='/apply-template' else {'tokens':[1,2,3]}
        runtime._json_request=request
        self.assertEqual(runtime._prompt_tokens([{'role':'user','content':'Hello'}]),3)
        self.assertNotIn('chat_template_kwargs',seen[0][1])
        runtime.behavior=ModelBehavior();runtime._active_template_effort='medium';seen.clear()
        runtime._prompt_tokens([]);self.assertEqual(seen[0][1]['chat_template_kwargs'],{'reasoning_effort':'medium'})

    def test_reasoning_filter_is_chunk_invariant_and_does_not_parse_harmony_literals(self):
        text='Visible <think>private <think>nested</think> analysis</think> answer <|channel|>final<|message|> literal.'
        expected='Visible  answer <|channel|>final<|message|> literal.'
        for size in range(1,25):
            stream=TemplateContentStream();answer=''.join(stream.feed(text[i:i+size]) for i in range(0,len(text),size))+stream.finish()
            self.assertEqual(answer,expected);self.assertTrue(stream.hidden)
        stream=TemplateContentStream();self.assertEqual(stream.feed('<think>private unfinished')+stream.finish(),'')
        stream=TemplateContentStream();self.assertEqual(stream.feed('safe <thi')+stream.finish(),'safe ')

    def test_large_content_chunk_has_bounded_work(self):
        stream=TemplateContentStream();text='one '*25000
        self.assertEqual(stream.feed(text),text)

    def test_memory_plan_separates_weights_kv_overhead_and_policy(self):
        result=estimate_memory(info(),4096,available_ram=8*GIB,available_vram=8*GIB,total_ram=16*GIB)
        self.assertGreater(result['cpuEnvelopeBytes'],2*GIB)
        self.assertEqual(result['kvEstimateBytes'],2*4096*28*4*256)
        self.assertFalse(result['automaticLoadAuthorized']);self.assertTrue(result['cpuLikelyFits'])
        self.assertEqual(estimate_memory(info(),8192,available_ram=GIB,profile='eco')['context'],2048)
        self.assertFalse(estimate_memory(info(),4096,available_ram=GIB)['cpuLikelyFits'])
        for arch in ['gpt-oss','qwen35','qwen3moe','unknown']:
            result=estimate_memory(info(arch),4096,available_ram=8*GIB)
            self.assertIsNone(result['kvEstimateBytes']);self.assertFalse(result['automaticLoadAuthorized'])
            self.assertEqual(result['status'],'INCOMPLETE_ALLOCATION_MODEL')

    def test_invalid_memory_observations_fail_closed(self):
        for value in [True,-1,1.5,float('nan')]:
            with self.assertRaises(ValueError):estimate_memory(info(),2048,available_ram=value)

    def test_cpu_setting_survives_configuration_loading_without_changing_default(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'config.json'
            for saved in [{'chat_gpu':'cpu'},{'chat_gpu_layers':0},{}]:
                config.write_text(json.dumps(saved),encoding='utf-8')
                with patch('integrated_runtime.CONFIG_PATH',config):cfg=load_config()
                self.assertEqual(cfg['chat_gpu_layers'],0 if saved else -1)
                self.assertEqual(cfg['chat_gpu'],'cpu' if saved else 'auto')

    def test_native_launcher_honors_cpu_zero_and_does_not_add_harmony_flags(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'misleading-gpt-oss.gguf';model.write_bytes(b'fixture')
            runtime=_LocalLlamaServer(root)
            def bind(_):runtime.behavior=ModelBehavior.from_model(info('qwen3'))
            process=Mock();process.poll.return_value=None
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.status=200
            with patch.object(runtime,'inspect_behavior',side_effect=bind),patch.object(runtime,'stop'),patch.object(runtime,'_server_binary',return_value=root/'llama-server.exe'),patch.object(runtime,'_check_runtime_files'),patch('integrated_runtime.subprocess.Popen',return_value=process) as launch,patch.object(runtime._local_opener,'open',return_value=response),patch('integrated_runtime.physical_memory_status',return_value={'available':4*GIB}):
                try:runtime.start(model,2048,0,lambda _:None,memory_profile={'batch':32})
                finally:
                    if runtime.log_handle:runtime.log_handle.close()
            args=launch.call_args.args[0]
            self.assertEqual(args[args.index('-ngl')+1],'0');self.assertEqual(args[args.index('-b')+1],'32')
            self.assertNotIn('--reasoning-effort',args)

    def test_invalid_gpu_offload_request_is_not_silently_changed(self):
        for value in [True,'all','999999',-2,1025,0.5]:
            with self.assertRaises(ValueError):_LocalLlamaServer.gpu_layer_argument(value)
        self.assertEqual(_LocalLlamaServer.gpu_layer_argument(-1),'auto')

    def test_facade_preserves_existing_manager_ownership_and_cancel_token(self):
        from contextlib import nullcontext
        manager=SimpleNamespace(ensure_chat=Mock(return_value='model'),chat_completion=Mock(return_value='answer'),
            chat_status=Mock(return_value={'loaded':False}),runtime=SimpleNamespace(ready=True,_prompt_tokens=Mock(return_value=7)),
            _unload_chat=Mock(),resource_lock=nullcontext(),lock=nullcontext(),_active_inference_count=0)
        api=EngineTextRuntime(manager);event=threading.Event()
        self.assertEqual(api.load(cancel_event=event),'model');self.assertIs(manager.ensure_chat.call_args.kwargs['cancel_event'],event)
        self.assertEqual(api.generate([{'role':'user','content':'Hello'}],cancel_event=event),'answer')
        self.assertEqual(api.count_tokens([]),7);api.cancel(event);self.assertTrue(event.is_set())
        api.unload();manager._unload_chat.assert_called_once()
        manager._active_inference_count=1
        with self.assertRaises(RuntimeError):api.unload()

    def test_file_observation_changes_and_unknown_paths_are_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'m.gguf';self.assertIsNone(_LocalLlamaServer.file_observation(model))
            model.write_bytes(b'first');before=_LocalLlamaServer.file_observation(model)
            model.write_bytes(b'second-longer');self.assertNotEqual(before,_LocalLlamaServer.file_observation(model))

    def test_changed_weight_observation_invalidates_warm_manager_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'m.gguf';model.write_bytes(b'first')
            manager=EngineManager();manager.cfg.update(chat_backend='bundled',gpt_oss_gguf=str(model))
            server=Mock();server.model_path=str(model.resolve());server.projector_path='';server.loaded_gpu_layers='auto'
            server.model_file_observation=_LocalLlamaServer.file_observation(model);server._is_healthy.return_value=True;server.model_descriptor=None
            manager.runtime=server
            model.write_bytes(b'changed bytes')
            manager.ensure_chat();server.start.assert_called_once()

    def test_response_model_identity_is_snapshotted_without_relabeling_old_turns(self):
        import app
        old={'backend':'bundled','modelName':'Earlier model','context':1024}
        backend=SimpleNamespace(engines=SimpleNamespace(last_completion_identity=old),operation_event_id='turn1',emit_frontend=Mock())
        app.App.on_chat_metrics(backend,{'model_calls':1})
        old['modelName']='Newly selected model'
        self.assertEqual(backend._response_metrics['turn1']['model_identity']['modelName'],'Earlier model')
        backend.operation_event_id='turn2';app.App.on_chat_metrics(backend,{'model_calls':0})
        self.assertNotIn('model_identity',backend._response_metrics['turn2'])

    def test_context_is_capped_by_selected_metadata_before_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'m.gguf';model.write_bytes(b'fixture');runtime=_LocalLlamaServer(root)
            def bind(_):runtime.model_descriptor=info('qwen3',**{'qwen3.context_length':1024});runtime.behavior=ModelBehavior.from_model(runtime.model_descriptor)
            process=Mock();process.poll.return_value=None;response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.status=200
            with patch.object(runtime,'inspect_behavior',side_effect=bind),patch.object(runtime,'stop'),patch.object(runtime,'_server_binary',return_value=root/'llama-server.exe'),patch.object(runtime,'_check_runtime_files'),patch('integrated_runtime.subprocess.Popen',return_value=process) as launch,patch.object(runtime._local_opener,'open',return_value=response),patch('integrated_runtime.physical_memory_status',return_value={'available':4*GIB}):
                try:runtime.start(model,4096,0,lambda _:None)
                finally:
                    if runtime.log_handle:runtime.log_handle.close()
            args=launch.call_args.args[0];self.assertEqual(args[args.index('-c')+1],'1024')
            self.assertEqual(runtime.context,1024)

    def test_status_reports_cpu_and_actual_mmap_profile_without_stale_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'m.gguf';model.write_bytes(b'fixture')
            manager=EngineManager();manager.cfg.update(chat_backend='bundled',gpt_oss_gguf=str(model),chat_gpu='cpu',chat_gpu_layers=0)
            manager.runtime=Mock(model_path=str(model.resolve()),projector_path='',context=1024)
            manager.runtime.ready=True;manager.runtime.behavior=ModelBehavior.from_model(info('qwen3'));manager.runtime.model_descriptor=info('qwen3')
            manager.chat_load_state='ready';manager.active_gpu_layers=0;manager.chat_memory_profile={'memory_mapped':False}
            memory={'available':4*GIB,'total':16*GIB,'pagefile_available':8*GIB}
            with patch('integrated_runtime.physical_memory_status',return_value=memory),patch('integrated_runtime.available_cuda_memory',return_value=5*GIB),patch.object(manager,'vision_model_status',return_value={}):
                status=manager.chat_status()
                self.assertEqual(status['requestedGpuLayers'],0);self.assertEqual(status['activeGpuLayers'],0)
                self.assertFalse(status['memoryMappedModel']);self.assertIn('CPU',status['inferenceMode'])
                self.assertEqual(status['modelRuntime']['responseFormat'],'qwen3')
                manager.chat_load_state='not-loaded'
                status=manager.chat_status();self.assertIsNone(status['activeGpuLayers']);self.assertIsNone(status['memoryMappedModel'])
                self.assertIsNone(status['modelRuntime']['metadataCapabilities']);self.assertEqual(status['modelRuntime']['responseFormat'],'not-loaded')

    def test_successful_inspection_clears_previous_failure_without_native_grant(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/'m.gguf';model.write_bytes(b'fixture');runtime=_LocalLlamaServer(Path(folder))
            runtime.inspect_behavior(model);self.assertTrue(runtime.behavior_inspection_error)
            result={'metadata':dict(info().metadata),'fileSizeBytes':2*GIB,'headerMetadataSha256':'b'*64,'fileObservation':(1,2,3,4,5)}
            with patch('model_inspection.inspect_gguf',return_value=result):runtime.inspect_behavior(model)
            self.assertEqual(runtime.behavior_inspection_error,'');self.assertEqual(runtime.behavior.family,Family.QWEN3)
            self.assertFalse(runtime.model_descriptor.capability_report()['nativeCompatible'])

    def test_real_loopback_template_and_stream_contract_without_model_inference(self):
        """Real HTTP/SSE transport with deterministic server; not native IQ."""
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        records=[];response_chunks=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                records.append((self.path,payload));self.send_response(200)
                if self.path=='/v1/chat/completions':
                    self.send_header('Content-Type','text/event-stream');self.end_headers()
                    for chunk in response_chunks:
                        self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':chunk,'reasoning_content':'separate private thought'}}]})+'\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
                else:
                    self.send_header('Content-Type','application/json');self.end_headers()
                    self.wfile.write(json.dumps({'prompt':'template output'} if self.path=='/apply-template' else {'tokens':[1,2,3]}).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                runtime=_LocalLlamaServer(Path(folder));runtime.port=server.server_address[1];runtime.api_key='synthetic-test-key'
                runtime._is_healthy=lambda:True;runtime.context=2048
                for arch in ['gpt-oss','qwen3','llama','gemma3']:
                    records.clear();emitted=[];runtime.behavior=ModelBehavior.from_model(info(arch))
                    if arch=='gpt-oss':
                        response_chunks[:]=['<|channel|>analysis<|message|>hidden','<|channel|>final<|message|>Answer 42.<|return|>'];expected='Answer 42.'
                    else:
                        response_chunks[:]=['<thi','nk>hidden reasoning</th','ink>Answer 42. Literal <|channel|>final<|message|> remains.'];expected='Answer 42. Literal <|channel|>final<|message|> remains.'
                    with self.subTest(architecture=arch):
                        answer=runtime.complete([{'role':'user','content':'A synthetic task'}],2048,200,.2,threading.Event(),emitted.append,lambda _:None,allowed_tools=[])
                        self.assertEqual(answer,expected);self.assertEqual(''.join(emitted),expected)
                        payload=next(p for endpoint,p in records if endpoint=='/v1/chat/completions')
                        template=next(p for endpoint,p in records if endpoint=='/apply-template')
                        self.assertEqual('chat_template_kwargs' in payload,arch=='gpt-oss')
                        self.assertEqual('chat_template_kwargs' in template,arch=='gpt-oss')
                        self.assertEqual(payload['model'],'Model metadata name')
                        self.assertNotIn('hidden reasoning',runtime.last_generation_diagnostics['raw_output_preview'])
        finally:server.shutdown();server.server_close();thread.join(2)
        self.assertFalse(thread.is_alive())


if __name__=='__main__':unittest.main()
