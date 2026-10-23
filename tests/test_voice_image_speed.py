import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice_runtime import speech_chunks, voice_catalog, SPEEDS
from intelligence import is_standalone_greeting, analyze_request, build_system_prompt
import app


class VoiceImageSpeedTests(unittest.TestCase):
    def test_long_speech_keeps_words_and_bounds_chunks(self):
        text='Hello! Here is an answer with natural punctuation. '+('A longer paragraph retains every word and every sentence. '*35)
        chunks=speech_chunks(text)
        self.assertEqual(' '.join(chunks),' '.join(text.split()))
        self.assertGreater(len(chunks),3)
        self.assertLessEqual(len(chunks[0]),140)
        self.assertTrue(all(len(c)<=320 for c in chunks))

    def test_greeting_does_not_eat_a_substantive_sibling(self):
        for text in ['How are you?','Hello again!','Good morning.']:
            self.assertTrue(is_standalone_greeting(text))
        for text in ['How are you? Calculate 5*7.','Hello and summarize the PDF.']:
            self.assertFalse(is_standalone_greeting(text))

    def test_normal_chat_condenses_only_exact_shipped_policy(self):
        policy=Path(app.APP_DIR/'assistant_system_prompt.md').read_text(encoding='utf-8')
        analysis=analyze_request('Explain TCP.')
        result=build_system_prompt(policy,analysis)
        self.assertLess(len(result),1100)
        custom=build_system_prompt('CUSTOM POLICY: always use metric units.',analysis)
        self.assertIn('CUSTOM POLICY:',custom)
        self.assertIn('metric units',custom)

    def test_unsupported_claim_keeps_full_grounding_contract(self):
        analysis=analyze_request('What did Professor Flarzon announce yesterday?')
        result=build_system_prompt(Path(app.APP_DIR/'assistant_system_prompt.md').read_text(encoding='utf-8'),analysis)
        self.assertIn('CRITICAL Factual Grounding Directive',result)

    def test_voice_catalog_reports_only_existing_compatible_assets(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:self.assertEqual(voice_catalog(folder),[])
        assets=Path(app.APP_DIR).parent/'Offline GPT'/'kokoro'
        if assets.is_dir():
            names={v['id'] for v in voice_catalog(assets)}
            self.assertTrue({'af_heart','am_michael','bf_emma','bm_george'}.issubset(names))
        self.assertEqual(SPEEDS,(.75,1,1.25,1.5,1.75,2))

    def test_image_preview_resolver_agrees_with_real_dimensions(self):
        for text,expected in [('Generate 512x320 image',(512,320)),('4K image',(3840,2160)),('portrait 768×1024',(768,1024))]:
            self.assertEqual(app.requested_image_size(text,1024,576),expected)
        self.assertIsNone(app.image_progress_event('Loading image model')['percent'])
        event=app.image_progress_event('Generating image · step 6 of 12')
        self.assertEqual(event['percent'],50)
        self.assertEqual(app.image_progress_event('Generating image · step 12 of 12')['percent'],99)

    def test_spoken_code_and_reasoning_remain_filtered(self):
        text=app.spoken_response_text('**Hello**.\n```python\nsecret=1\n```\nMETHOD: TOOL')
        self.assertNotIn('secret=1',text)
        self.assertNotIn('METHOD:',text)


class SpeechRepairTests(unittest.TestCase):
    def tearDown(self):app.stop_warm_whisper()

    def test_repeated_requests_reuse_same_installed_session(self):
        from unittest.mock import patch,Mock
        import tempfile,json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=root/'whisper-server.exe';model=root/'model.bin';server.touch();model.touch()
            cfg={'voice_server_path':str(server),'voice_model_path':str(model),'voice_language':'en'}
            process=Mock(pid=123);process.poll.return_value=None
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{"text":"A complete final word."}'
            with patch('integrated_runtime.available_cuda_memory',return_value=3*1024**3),patch('app.physical_memory_status',return_value={'available':4*1024**3}),patch('app.subprocess.Popen',return_value=process) as launch,patch('app.make_whisper_loopback_opener',return_value=Mock(open=Mock(return_value=response))):
                workers=[]
                for _ in range(5):
                    worker=app.WhisperTranscriptionWorker(b'\0\0'*16000,16000,cfg);worker.run();workers.append(worker)
                self.assertEqual(launch.call_count,1);self.assertNotIn('--no-gpu',launch.call_args.args[0]);self.assertTrue(all(w.succeeded for w in workers));self.assertTrue(all(w.metrics['warmSession'] for w in workers[1:]));self.assertEqual(workers[1].metrics['stages']['STT_MODEL_LOAD_MS'],0)

    def test_low_vram_uses_cpu_and_preserves_model(self):
        from unittest.mock import patch,Mock
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=root/'whisper-server.exe';model=root/'model.bin';server.touch();model.touch()
            process=Mock(pid=456);process.poll.return_value=None
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{"text":"Local CPU fallback."}'
            with patch('integrated_runtime.available_cuda_memory',return_value=0),patch('app.physical_memory_status',return_value={'available':4*1024**3}),patch('app.subprocess.Popen',return_value=process) as launch,patch('app.make_whisper_loopback_opener',return_value=Mock(open=Mock(return_value=response))):
                worker=app.WhisperTranscriptionWorker(b'\0'*32000,16000,{'voice_server_path':str(server),'voice_model_path':str(model)});worker.run()
            self.assertTrue(worker.succeeded);self.assertIn('--no-gpu',launch.call_args.args[0]);self.assertEqual(worker.metrics['executionProvider'],'cpu')

    def test_stt_rechecks_ram_after_managed_model_handoff(self):
        from unittest.mock import patch,Mock
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=root/'whisper-server.exe';model=root/'model.bin';server.touch();model.touch()
            process=Mock(pid=789);process.poll.return_value=None
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{"text":"Safe handoff."}'
            worker=app.WhisperTranscriptionWorker(b'\0'*32000,16000,{'voice_server_path':str(server),'voice_model_path':str(model)})
            manager=Mock();worker.engine=manager;manager.resource_lock.acquire.return_value=True
            values=iter([{'available':4*1024**3},{'available':100*1024**2},{'available':4*1024**3}])
            with patch('integrated_runtime.available_cuda_memory',return_value=3*1024**3),patch('app.physical_memory_status',side_effect=lambda:next(values)),patch('app.subprocess.Popen',return_value=process),patch('app.make_whisper_loopback_opener',return_value=Mock(open=Mock(return_value=response))):worker.run()
            self.assertTrue(worker.succeeded);manager._unload_chat.assert_called_once()

    def test_worklet_flush_retains_final_partial_packet_once(self):
        import subprocess,json
        target = Path(__file__).resolve().parent.parent / 'frontend-source' / 'src' / 'main.jsx'
        if not target.exists():
            target = Path('frontend-source/src/main.jsx')
        source = target.read_text(encoding='utf-8')
        worklet=source.split('const workletSource = `',1)[1].split('`;',1)[0]
        script="""let Processor; const messages=[];global.sampleRate=16000;global.AudioWorkletProcessor=class{constructor(){this.port={postMessage:m=>messages.push(m)}}};global.registerProcessor=(n,p)=>Processor=p;
"""+worklet+"""
const p=new Processor();p.process([[new Float32Array(128).fill(.25)]]);p.port.onmessage({data:{type:'flush'}});p.port.onmessage({data:{type:'flush'}});const packets=messages.filter(m=>m.pcm);if(packets.length!==1||new Int16Array(packets[0].pcm).length!==128||new Int16Array(packets[0].pcm)[127]!==8192)throw Error('Partial final packet lost or duplicated');console.log('PASS');"""
        import shutil,os
        node=os.environ.get('OFFLINEGPT_TEST_NODE') or shutil.which('node')
        if not node:self.skipTest('Node.js is required for AudioWorklet validation')
        result=subprocess.run([str(node),'-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_cancel_before_recognition_never_launches_process(self):
        from unittest.mock import patch
        worker=app.WhisperTranscriptionWorker(b'\0'*32000,16000,{})
        worker.cancel()
        with patch('app.subprocess.Popen') as launch:worker.run();launch.assert_not_called()
        self.assertFalse(worker.succeeded)

    def test_cancel_terminates_only_owned_whisper(self):
        from unittest.mock import Mock
        process=Mock();process.poll.return_value=None
        app._WARM_WHISPER.update(process=process,port=99,key=('s','m',4,'cuda'))
        unrelated=Mock();app.stop_warm_whisper(unrelated);process.terminate.assert_not_called()
        worker=app.WhisperTranscriptionWorker(b'',16000,{});worker.process=process;worker.cancel()
        process.terminate.assert_called_once();self.assertIsNone(app._WARM_WHISPER['process'])

    def test_resampling_keeps_audio_duration_and_last_samples(self):
        import wave,io,struct
        samples=[1000]*48000+[3200]*480
        data=app.pcm_to_wav_16k(struct.pack('<'+'h'*len(samples),*samples),48000)
        with wave.open(io.BytesIO(data),'rb') as audio:
            self.assertEqual(audio.getframerate(),16000);self.assertEqual(audio.getnframes(),16160)
            self.assertEqual(struct.unpack('<h',audio.readframes(audio.getnframes())[-2:])[0],3200)

    def test_color_only_route_requests_mask_without_vision_or_flux(self):
        from image_pipeline import recolor_intent
        from types import SimpleNamespace
        calls=[];backend=SimpleNamespace(prepare_fast_recolor=lambda *args:calls.append(args))
        app.App.route(backend,'Change only the black body paint to glossy red. Keep all other visual elements unchanged.','car.png')
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][2]['color'],'#ca151c')
        self.assertIsNone(recolor_intent('Put this red car on a mountain road'))
        self.assertIsNone(recolor_intent('Generative AI edit: change the car to red'))
        self.assertIsNone(recolor_intent('Change the car to red and put it on a mountain road, keep wheels unchanged'))
        self.assertIsNotNone(recolor_intent('Change the car to red. Keep the background unchanged.'))

    def test_constraints_preserved_and_contradictory_plan_discarded(self):
        from image_pipeline import preserve_image_constraints,extract_image_constraints
        original='Generate a glossy black modern Ford Mustang coupe, front three-quarter view, realistic automotive photograph, no other vehicles.'
        parsed=extract_image_constraints(original);self.assertEqual(parsed['color'],['black']);self.assertIn('modern',parsed['era']);self.assertEqual(parsed['prohibitions'],['no other vehicles'])
        for mode in ('smart_enhance','creative'):
            final,record=preserve_image_constraints(original,'A red vintage 1967 Ford Mustang on a rainy street.',mode)
            self.assertFalse(record['plannerAccepted']);self.assertIn(original,final);self.assertNotIn('1967',final);self.assertNotIn('red',final.lower());self.assertNotIn('rainy',final.lower())
        final,record=preserve_image_constraints(original,'A glossy black modern Ford Mustang coupe, front three-quarter view, no other vehicles. Soft studio reflections reveal the paint.')
        self.assertTrue(record['plannerAccepted']);self.assertIn('Soft studio reflections',final)

    def test_unspecified_era_is_not_invented_as_mandatory(self):
        from image_pipeline import extract_image_constraints
        self.assertEqual(extract_image_constraints('A glossy Mustang')['era'],[])

if __name__=='__main__':unittest.main()
