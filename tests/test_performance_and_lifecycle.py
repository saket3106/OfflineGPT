import unittest
from router import get_relevant_tools, route_request
from integrated_runtime import _local_tool_schemas, load_config, DEFAULT_CONFIG


class TestPerformanceAndLifecycle(unittest.TestCase):
    def test_relevant_tools_for_simple_chat(self):
        prompt = "Reply with exactly one word:\n\nREADY"
        analysis = route_request(prompt)
        tools = get_relevant_tools(analysis, prompt)
        self.assertEqual(tools, [])
        schemas = _local_tool_schemas(tools)
        self.assertEqual(schemas, [])

    def test_relevant_tools_for_math(self):
        prompt = "Calculate 123456 * 7890"
        analysis = route_request(prompt)
        tools = get_relevant_tools(analysis, prompt)
        self.assertEqual(tools, ["calculator"])
        schemas = _local_tool_schemas(tools)
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "calculator")

    def test_relevant_tools_for_python(self):
        prompt = "Run the following Python code:\nprint(2 + 2)"
        analysis = route_request(prompt)
        tools = get_relevant_tools(analysis, prompt)
        self.assertEqual(tools, ["python_runner"])
        schemas = _local_tool_schemas(tools)
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "python_runner")

    def test_relevant_tools_for_classification_only(self):
        prompt = "1.A Classify only: Calculate 123456 * 7890\n1.B Classify only: Run Python code"
        analysis = route_request(prompt)
        tools = get_relevant_tools(analysis, prompt)
        self.assertEqual(tools, [])
        schemas = _local_tool_schemas(tools)
        self.assertEqual(schemas, [])

    def test_relevant_tools_for_multitask_mixed(self):
        prompt = "1. Explain recursion\n2. Calculate 45 * 67\n3. Run Python code"
        analysis = route_request(prompt)
        tools = get_relevant_tools(analysis, prompt)
        self.assertIn("calculator", tools)
        self.assertIn("python_runner", tools)
        schemas = _local_tool_schemas(tools)
        self.assertEqual(len(schemas), 2)
        schema_names = {s["function"]["name"] for s in schemas}
        self.assertEqual(schema_names, {"calculator", "python_runner"})

    def test_default_lifecycle_configuration(self):
        cfg = load_config()
        self.assertIn(cfg["model_lifecycle_mode"], {"balanced", "low_ram", "fast"})
        self.assertEqual(cfg["model_lifecycle_mode"], "balanced")
        self.assertEqual(cfg["model_keepalive_seconds"], 90)
        self.assertEqual(cfg["emergency_ram_threshold_mb"], 256)


class ModelHealthIdentityRegressions(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, patch
        import integrated_runtime as runtime
        self.runtime_module = runtime
        self.tmp = tempfile.TemporaryDirectory(prefix='model-health-fixture-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        cfg = dict(runtime.DEFAULT_CONFIG, gpt_oss_gguf=str(self.root/'selected.gguf'))
        patches = [patch.object(runtime, 'APP_DIR', self.root), patch.object(runtime, 'load_config', return_value=cfg),
                   patch.object(runtime, 'physical_memory_status', return_value={'available':8*1024**3,'total':16*1024**3,'pagefile_available':8*1024**3}),
                   patch.object(runtime, 'available_cuda_memory', return_value=None)]
        for item in patches:item.start();self.addCleanup(item.stop)
        self.manager = runtime.EngineManager()
        self.manager.vision_model_status = Mock(return_value={})
        self.manager.lm_runtime._server_ready = Mock(return_value=False)
        self.manager.lm_runtime._lms_path = Mock(return_value=self.root/'absent-cli')
        self.manager.lm_runtime._bionic_path = Mock(return_value=self.root/'absent-external')

    def model(self, name, arch='qwen3', path=None):
        from test_model_inspection import fixture
        path = path or self.root/'selected.gguf'
        path.write_bytes(fixture([('general.name',8,name),('general.architecture',8,arch)]))
        return path

    def test_bundled_qwen_does_not_inherit_external_gpt_id(self):
        self.model('Qwen3 8B')
        result=self.manager.chat_status()
        self.assertEqual(result['modelName'],'Qwen3 8B')
        self.assertEqual(result['externalModelId'],'')
        self.assertEqual(result['modelIdentitySource'],'untrusted_gguf_metadata')
        self.assertFalse(result['modelInspection']['nativeCompatible'])
        self.assertFalse(result['loaded'])

    def test_gpt_oss_inspected_identity_and_restart(self):
        self.model('GPT-OSS 20B','gpt-oss')
        self.assertEqual(self.manager.chat_status()['modelName'],'GPT-OSS 20B')
        restarted=self.runtime_module.EngineManager()
        restarted.vision_model_status=self.manager.vision_model_status
        self.assertEqual(restarted.chat_status()['modelName'],'GPT-OSS 20B')

    def test_missing_model_overrides_old_passed_test(self):
        self.manager.chat_test_state='passed'
        self.manager.chat_load_state='ready'
        result=self.manager.chat_status()
        self.assertFalse(result['loaded'])
        self.assertEqual(result['loadState'],'missing-model')
        self.assertEqual(result['modelSelectionState'],'missing-model')
        self.assertIn('Settings',result['modelSelectionMessage'])

    def test_filename_cannot_grant_metadata_or_compatibility(self):
        path=self.root/'GPT-OSS-20B-certified.gguf';path.write_bytes(b'not a model')
        self.manager.cfg['gpt_oss_gguf']=str(path)
        result=self.manager.chat_status()
        self.assertEqual(result['modelSelectionState'],'unverified-file')
        self.assertEqual(result['modelIdentitySource'],'selected_filename_unverified')
        self.assertIsNone(result['modelInspection'])
        self.assertTrue(result['modelInspectionError'])
        self.assertFalse(result['loaded'])

    def test_external_model_does_not_require_bundled_file_or_inspection(self):
        from unittest.mock import patch
        self.manager.cfg.update(chat_backend='lmstudio',chat_model='owner/external-model')
        with patch('model_inspection.inspect_gguf',side_effect=AssertionError('Unrelated local model inspection')):
            result=self.manager.chat_status()
        self.assertEqual(result['modelName'],'owner/external-model')
        self.assertEqual(result['modelIdentitySource'],'external_configuration')
        self.assertEqual(result['modelPath'],'')
        self.assertTrue(result['modelExists'])

    def test_same_mtime_replaced_file_does_not_reuse_identity(self):
        import os
        path=self.model('Qwen A');stamp=path.stat()
        self.assertEqual(self.manager.chat_status()['modelName'],'Qwen A')
        replacement=self.model('OtherB',path=self.root/'new.gguf')
        os.utime(replacement,ns=(stamp.st_atime_ns,stamp.st_mtime_ns));os.replace(replacement,path)
        self.assertEqual(self.manager.chat_status()['modelName'],'OtherB')

    def test_reload_selection_resets_old_test_even_without_live_runtime(self):
        from unittest.mock import patch
        self.manager.chat_test_state='passed';self.manager.chat_test_answer='old answer'
        self.manager._model_inspection_cache['old']=(1,{'name':'old'})
        updated=dict(self.manager.cfg,gpt_oss_gguf=str(self.root/'another.gguf'))
        with patch.object(self.runtime_module,'load_config',return_value=updated):self.manager.reload()
        self.assertEqual(self.manager.chat_test_state,'not-tested')
        self.assertEqual(self.manager.chat_test_answer,'')
        self.assertEqual(self.manager._model_inspection_cache,{})

    def test_external_model_switch_unloads_old_selection_and_clears_test(self):
        from unittest.mock import Mock, patch
        self.manager.cfg.update(chat_backend='lmstudio',chat_model='old-model')
        self.manager.chat_test_state='passed';self.manager.lm_runtime.unload=Mock()
        updated=dict(self.manager.cfg,chat_model='new-model')
        with patch.object(self.runtime_module,'load_config',return_value=updated):self.manager.reload()
        self.manager.lm_runtime.unload.assert_called_once_with(stop_server=True)
        self.assertEqual(self.manager.chat_status()['modelName'],'new-model')
        self.assertEqual(self.manager.chat_test_state,'not-tested')

    def test_healthy_previous_runtime_cannot_label_new_file_loaded(self):
        from types import SimpleNamespace
        self.model('New Qwen')
        self.manager.runtime=SimpleNamespace(ready=True,model_path=str(self.root/'old.gguf'),projector_path='',_is_healthy=lambda:True)
        self.manager.chat_load_state='ready'
        result=self.manager.chat_status()
        self.assertFalse(result['loaded'])
        self.assertEqual(result['loadState'],'not-loaded')

    def test_metadata_name_controls_are_removed_without_execution(self):
        self.model('Qwen\n<script>not executable</script>')
        result=self.manager.chat_status()
        self.assertNotIn('\n',result['modelName'])
        self.assertFalse(result['modelInspection']['nativeCompatible'])


if __name__ == "__main__":
    unittest.main()


class StreamingPresentationRegressions(unittest.TestCase):
    def test_unseen_engineering_requirements_form_one_request(self):
        prompt = ("Design a safe reservation service for a shared bike fleet.\n"
                  "Requirements:\n1. Explain the double booking race.\n2. Provide SQL for a unique active reservation.\n"
                  "3. Discuss transaction boundaries.\n4. Give three concurrency tests.\n"
                  "5. Show retry handling.\n6. Preserve audit records.\n7. Describe monitoring.\n"
                  "8. Explain idempotency keys.\n9. Provide a migration.\n10. Include rollback instructions.")
        analysis = route_request(prompt)
        self.assertFalse(analysis['multi_task'])
        from grounding import decompose_request_tasks
        tasks=decompose_request_tasks(prompt)
        self.assertEqual(len(tasks),1)
        self.assertTrue(analysis['parser_coverage_valid'])
        self.assertIn('10. Include rollback',tasks[0]['text'])

    def test_explicit_independent_questions_override_list_heading(self):
        prompt = 'Answer each question independently.\nRequirements:\nQuestion 1: Explain DNS.\nQuestion 2: Define TCP.'
        analysis=route_request(prompt)
        self.assertTrue(analysis['multi_task'])
        self.assertEqual(analysis['expected_task_ids'],[1,2])

    def test_retry_stream_keeps_successful_siblings_and_honest_counts(self):
        from app import ChatWorker
        from intelligence import analyze_request
        prompt='1. Explain TCP in one sentence.\n2. Explain DNS in one sentence.'
        class Engine:
            calls=0
            def ensure_chat(self,progress,model,cancel_event):return model
            def chat_completion(self,messages,**kwargs):
                self.calls+=1
                answer='1. TCP delivers ordered data.' if self.calls==1 else '2. DNS resolves names.'
                kwargs['on_token'](answer)
                return answer
        engine=Engine();analysis=analyze_request(prompt)
        worker=ChatWorker('test',[{'role':'user','content':prompt}],engine,analysis)
        snapshots=[];activities=[];done=[];metrics=[];errors=[]
        worker.partial.connect(snapshots.append);worker.activity.connect(activities.append)
        worker.done.connect(done.append);worker.metrics.connect(metrics.append);worker.failed.connect(errors.append)
        worker.run()
        self.assertEqual(errors,[])
        self.assertEqual(engine.calls,2)
        first_seen=False
        for snapshot in snapshots:
            if 'TCP delivers ordered data.' in snapshot:first_seen=True
            if first_seen:self.assertIn('TCP delivers ordered data.',snapshot)
        generating=[e for e in activities if e['phase']=='Generating answer']
        self.assertEqual(generating[0]['completedIds'],[])
        self.assertEqual(activities[-1]['completedIds'],[1,2])
        self.assertEqual(snapshots[-1],done[0])
        self.assertIsNone(metrics[0]['lifecycle']['timestamps']['T9'])
        self.assertIsNone(metrics[0]['lifecycle']['timestamps']['T10'])

    def test_out_of_order_sections_join_by_id(self):
        from validators import merge_numbered_task_sections
        result=merge_numbered_task_sections('1. keep alpha','3. gamma\n2. beta',[1,2,3],[2,3])
        self.assertEqual(result,'1. keep alpha\n\n2. beta\n\n3. gamma')

    def test_markdown_id_heading_does_not_force_model_retry(self):
        from validators import parse_numbered_response_sections
        sections=parse_numbered_response_sections('## 2.A\nTCP carries data.\n\n2.B. DIRECT',['2.A','2.B'])
        self.assertEqual(list(sections),['2.A','2.B'])
        self.assertEqual(sections['2.A']['body'],'TCP carries data.')

    def test_paragraph_requirements_keep_shared_answer(self):
        from grounding import decompose_request_tasks
        prompt='Explain the inventory race.\n\nProvide SQL for the fix.\n\nGive two tests for that fix.'
        self.assertEqual(len(decompose_request_tasks(prompt)),1)
        self.assertEqual(len(decompose_request_tasks('What is TCP?\n\nWhat is DNS?')),2)

    def test_fast_mode_reuses_healthy_loaded_runtime(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import Mock
        from integrated_runtime import EngineManager, _LocalLlamaServer
        with TemporaryDirectory() as directory:
            model=Path(directory)/'local-model.gguf';model.write_bytes(b'fixture')
            manager=EngineManager();manager.cfg['chat_backend']='bundled';manager.cfg['gpt_oss_gguf']=str(model)
            manager.model_lifecycle_mode='fast'
            server=Mock();server.model_path=str(model.resolve());server.projector_path='';server._is_healthy.return_value=True
            server.loaded_gpu_layers='auto';server.model_file_observation=_LocalLlamaServer.file_observation(model)
            manager.runtime=server
            manager.ensure_chat();manager.ensure_chat()
            server.start.assert_not_called()
            self.assertIs(manager.runtime,server)
            self.assertEqual(manager.chat_load_state,'ready')
