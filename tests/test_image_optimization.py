import json,tempfile,unittest,hashlib,threading
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import numpy as np
from image_pipeline import ImageRunProfile
from workspace_services import basic_image_edit,masked_recolor

class ImageOptimizationTests(unittest.TestCase):
    def test_missing_reviewed_mask_never_falls_back_to_heuristic(self):
        with tempfile.TemporaryDirectory() as folder,patch('workspace_services.generate_subject_mask') as auto:
            with self.assertRaisesRegex(ValueError,'Automatic fallback was not used'):
                masked_recolor(Image.new('RGB',(128,96)),{'mask_path':str(Path(folder)/'missing.png'),'auto_mask':True})
            auto.assert_not_called()

    def test_recolor_rejects_lossy_export_without_writing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'original.png';Image.new('RGB',(128,96),(30,30,30)).save(source);before=source.read_bytes()
            for fmt in ('jpg','webp'):
                with self.assertRaisesRegex(ValueError,'Use PNG'):
                    basic_image_edit(source,'recolor',{'format':fmt,'strokes':[{'radius':.1,'points':[[.5,.5]]}]},Path(folder)/'output')
            self.assertEqual(source.read_bytes(),before);self.assertEqual(list((Path(folder)/'output').glob('*')),[])

    def test_malformed_mask_payloads_fail_before_transform(self):
        for strokes in ('bad',[1],[{'points':'bad'}],[{'points':[None]}],[{'points':[[True,.5]]}]):
            with self.subTest(strokes=strokes),self.assertRaises(ValueError):masked_recolor(Image.new('RGB',(64,64)),{'strokes':strokes})

    def test_unsupported_finish_is_not_silently_substituted(self):
        with self.assertRaisesRegex(ValueError,'glossy, matte or metallic'):
            masked_recolor(Image.new('RGB',(64,64)),{'finish':'mirrored fabric','strokes':[{'radius':.1,'points':[[.5,.5]]}]})

    def test_application_does_not_publish_an_unreviewed_auto_edit(self):
        from types import SimpleNamespace
        import sqlite3,app
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'source.png';Image.new('RGB',(256,144),(30,30,30)).save(path)
            db=sqlite3.connect(':memory:');db.execute('CREATE TABLE library_images(id TEXT,path TEXT)');db.execute('INSERT INTO library_images VALUES(?,?)',('source',str(path.resolve())))
            records=[];events=[]
            store=SimpleNamespace(con=db,lock=threading.RLock(),add=lambda *a,**kw:records.append(kw))
            backend=SimpleNamespace(store=store,current='isolated',on_worker_progress=lambda *a:None,set_busy=lambda *a:None,bubble=lambda *a:None,push_messages=lambda:None,emit_frontend=events.append)
            before=path.read_bytes()
            with patch.object(app,'DATA_DIR',root):app.App.prepare_fast_recolor(backend,'Make the car red',path,{'color':'#ca151c','auto_mask':True})
            self.assertEqual(records[-1]['metrics']['status'],'AWAITING_MASK_CORRECTION')
            self.assertEqual(len(list((root/'generated').glob('mask-proposal-*.png'))),1)
            self.assertEqual(list((root/'generated').glob('edit-*.png')),[])
            self.assertEqual(path.read_bytes(),before);self.assertEqual(events[-1]['type'],'recolorRequested')
            db.close()

    def test_mask_dimensions_must_match_before_recolor(self):
        with self.assertRaisesRegex(ValueError,'dimensions'):
            masked_recolor(Image.new('RGB',(128,96)),{'mask_image':Image.new('L',(64,96),255)})

    def test_erase_corrects_an_existing_proposal(self):
        base=Image.new('L',(128,128),255)
        source=Image.new('RGB',(128,128),(30,30,30))
        result,mask,_=masked_recolor(source,{'mask_image':base,'strokes':[{'erase':True,'radius':.1,'points':[[.5,.5]]}]})
        self.assertEqual(mask.getpixel((64,64)),0);self.assertEqual(result.getpixel((64,64)),source.getpixel((64,64)))
        self.assertEqual(mask.getpixel((10,10)),255)

    def test_auto_mask_is_not_certified_by_coverage(self):
        _,_,metrics=masked_recolor(Image.new('RGB',(256,144),(30,30,30)),{'auto_mask':True})
        self.assertTrue(metrics['maskRequiresReview']);self.assertIn('not verified',metrics['maskSemanticAccuracy'])

    def test_auto_closing_does_not_fill_excluded_highlights(self):
        from workspace_services import generate_subject_mask
        image=Image.new('RGB',(256,144),(30,30,30));image.putpixel((150,80),(255,255,255))
        self.assertEqual(generate_subject_mask(image).getpixel((150,80)),0)

    def test_stage_telemetry_does_not_invent_unmeasured_times(self):
        p=ImageRunProfile('edit_image');p.values['SAMPLING_STEP_COUNT']=4
        p.line('sampling completed, taking 8.24s');p.line('get_learned_condition completed, taking 1.10s')
        p.line('loading tensors completed, taking 0.09s (read: 0.02s, memcpy: 0.00s, convert: 0.00s, copy_to_backend: 0.01s)')
        with tempfile.TemporaryDirectory() as folder:
            value=p.finish(folder);saved=json.loads(Path(value['profilePath']).read_text())
        self.assertEqual(value['stages']['AVERAGE_STEP_MS'],2060)
        self.assertEqual(value['stages']['TEXT_ENCODING_MS'],1100)
        self.assertEqual(value['stages']['MODEL_MEMORY_TRANSFER_MS'],10)
        self.assertIsNone(value['stages']['FLUX_MODEL_LOAD_MS'])
        self.assertIsNone(value['stages']['UI_RENDER_DELAY_MS'])
        self.assertEqual(saved['processIds'],[])

    def test_recolor_requires_explicit_subject_mask(self):
        with self.assertRaisesRegex(ValueError,'Paint a subject mask'):
            masked_recolor(Image.new('RGB',(128,128),'black'),{})

    def test_masked_recolor_preserves_every_pixel_outside_mask_and_alpha(self):
        image=Image.new('RGBA',(128,128),(30,30,30,123));original=np.array(image)
        result,mask,metrics=masked_recolor(image,{'color':'#e00000','strokes':[{'radius':.1,'points':[[.3,.5],[.7,.5]]}]})
        array=np.array(result);selected=np.array(mask)>0
        self.assertTrue(np.array_equal(array[~selected],original[~selected]))
        self.assertTrue(np.array_equal(array[...,3],original[...,3]))
        self.assertGreater(int(array[64,64,0]),int(array[64,64,1]))
        self.assertEqual(metrics['modelCalls'],0);self.assertFalse(metrics['geometryResampled'])

    def test_erase_mask_excludes_windows_or_wheels(self):
        result,mask,_=masked_recolor(Image.new('RGB',(128,128),(30,30,30)),{'strokes':[{'radius':.2,'points':[[.2,.5],[.8,.5]]},{'erase':True,'radius':.05,'points':[[.5,.5]]}]})
        self.assertEqual(mask.getpixel((64,64)),0);self.assertEqual(result.getpixel((64,64)),(30,30,30))

    def test_recolor_rejects_outside_and_nonfinite_points(self):
        for point in [[2,.5],[float('nan'),.5]]:
            with self.assertRaises(ValueError):masked_recolor(Image.new('RGB',(64,64)),{'strokes':[{'radius':.1,'points':[point]}]})

    def test_recolor_output_and_mask_are_new_files_with_verified_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'original.png';Image.new('RGB',(128,96),(30,30,30)).save(source)
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            value=basic_image_edit(source,'recolor',{'strokes':[{'radius':.1,'points':[[.2,.5],[.8,.5]]}]},folder)
            self.assertNotEqual(source,Path(value['path']));self.assertTrue(Path(value['maskPath']).is_file())
            with Image.open(value['path']) as saved:self.assertEqual(saved.size,(128,96))
            self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())

    def test_resource_wait_for_image_is_cancellable(self):
        from integrated_runtime import EngineManager
        manager=EngineManager.__new__(EngineManager);manager.resource_lock=threading.Lock();manager.resource_lock.acquire()
        cancelled=threading.Event();cancelled.set()
        with self.assertRaises(RuntimeError):manager.prepare_image_engine(cancel_event=cancelled)
        manager.resource_lock.release()

    def test_plain_resize_routes_without_vision_or_gpt(self):
        from types import SimpleNamespace
        from app import App
        calls=[];backend=SimpleNamespace(image=lambda *args,**kwargs:calls.append(('image',args,kwargs)),analyze=lambda *args:calls.append(('vision',args)))
        App.route(backend,'Resize this image to 4K','test.png')
        self.assertEqual(calls[0][1][0],'resize_image');self.assertEqual(len(calls),1)

    def test_creative_edit_still_uses_vision(self):
        from types import SimpleNamespace
        from app import App
        calls=[];backend=SimpleNamespace(image=lambda *args,**kwargs:calls.append('image'),analyze=lambda *args:calls.append('vision'))
        App.route(backend,'Put this car on a mountain road','test.png')
        self.assertEqual(calls,['vision'])

if __name__=='__main__':unittest.main()
