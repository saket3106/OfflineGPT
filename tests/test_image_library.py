import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from PIL import Image

import app as offline_app
from app import Store, ensure_thumbnail, ImagePromptWorker


class ImageLibraryTests(unittest.TestCase):
    def test_version_family_keeps_missing_parent_as_explicit_placeholder(self):
        from types import SimpleNamespace
        parent=self.root/'parent.png';child=self.root/'child.png'
        Image.new('RGB',(20,20),'black').save(parent);Image.new('RGB',(20,20),'red').save(child)
        pid=self.store.index_library_image(parent);cid=self.store.index_library_image(child)
        self.store.workspace.link_image(cid,pid,'recolor',{})
        parent.unlink()
        bridge=SimpleNamespace(backend=SimpleNamespace(store=self.store))
        result=json.loads(offline_app.DesktopBridge.getLibraryVersions(bridge,cid))
        self.assertTrue(result['ok']);by_id={i['id']:i for i in result['images']}
        self.assertEqual(set(by_id),{pid,cid});self.assertFalse(by_id[pid]['fileExists'])
        self.assertEqual(by_id[pid]['displayName'],'Original file missing');self.assertEqual(by_id[cid]['parentId'],pid)

    def test_hidden_missing_file_is_listed_with_original_metadata(self):
        path=self.root/'missing-later.png';Image.new('RGB',(40,30)).save(path)
        iid=self.store.index_library_image(path,prompt='Actual planned prompt',user_prompt='Keep this history',seed=73,steps=14)
        self.store.workspace.link_image(iid,None,'recolor',{'color':'#ca151c'})
        self.store.workspace.manage_library([iid],'hide');path.unlink()
        hidden=self.store.get_library_images(visibility='hidden')
        self.assertEqual(len(hidden),1);self.assertEqual(hidden[0]['id'],iid);self.assertFalse(hidden[0]['fileExists'])
        self.assertEqual(hidden[0]['userPrompt'],'Keep this history');self.assertEqual(str(hidden[0]['seed']),'73')
        self.assertEqual(hidden[0]['operation'],'recolor');self.assertEqual(hidden[0]['steps'],14)
        self.assertEqual(self.store.get_library_images(),[])

    def test_restore_preserves_versions_and_generation_metadata(self):
        path=self.root/'restorable.png';Image.new('RGB',(40,30)).save(path);before=path.read_bytes()
        iid=self.store.index_library_image(path,user_prompt='Restore me',seed=0,steps=14)
        self.store.workspace.link_image(iid,None,'recolor',{'promptMode':'original','durationSeconds':2.5})
        original=self.store.con.execute('SELECT * FROM image_versions WHERE image_id=?',(iid,)).fetchone()
        self.store.workspace.manage_library([iid],'hide');self.assertEqual(len(self.store.get_library_images(visibility='hidden')),1)
        self.assertEqual(self.store.workspace.manage_library([iid],'restore')['completed'],1)
        self.assertEqual(self.store.get_library_images(visibility='hidden'),[])
        item=self.store.get_library_images()[0];self.assertEqual(item['promptMode'],'original');self.assertEqual(item['generationDurationSeconds'],2.5)
        self.assertEqual(self.store.con.execute('SELECT * FROM image_versions WHERE image_id=?',(iid,)).fetchone(),original)
        self.assertEqual(path.read_bytes(),before)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.prev_app_dir = offline_app.APP_DIR
        self.prev_db_path = offline_app.DB_PATH
        offline_app.DB_PATH = self.root / 'isolated.sqlite3'
        # Copy assistant_system_prompt.md to root if it exists
        orig_prompt = self.prev_app_dir / "assistant_system_prompt.md"
        if orig_prompt.is_file():
            (self.root / "assistant_system_prompt.md").write_text(orig_prompt.read_text(encoding="utf-8"), encoding="utf-8")
        offline_app.APP_DIR = self.root
        self.store = Store()

    def tearDown(self):
        try:
            self.store.con.close()
        except Exception:
            pass
        offline_app.APP_DIR = self.prev_app_dir
        offline_app.DB_PATH = self.prev_db_path
        self.temp_dir.cleanup()

    def test_ensure_thumbnail_creates_webp_or_jpeg(self):
        img_dir = self.root / "images"
        img_dir.mkdir()
        thumb_dir = self.root / "thumbnails"
        test_img_path = img_dir / "sample.png"

        # Create a sample image
        im = Image.new("RGB", (800, 600), color=(128, 64, 32))
        im.save(test_img_path, format="PNG")

        thumb_path = ensure_thumbnail(test_img_path, thumb_dir)
        self.assertIsNotNone(thumb_path)
        self.assertTrue(Path(thumb_path).is_file())
        with Image.open(thumb_path) as thumb_im:
            self.assertLessEqual(thumb_im.width, 384)
            self.assertLessEqual(thumb_im.height, 384)

    def test_index_and_get_library_images(self):
        img_dir = self.root / "images"
        img_dir.mkdir()
        test_img_path = img_dir / "flux_test_image.png"

        im = Image.new("RGB", (1024, 768), color=(20, 20, 20))
        im.save(test_img_path, format="PNG")

        self.store.index_library_image(
            path=test_img_path,
            chat_id="chat-123",
            user_prompt="A sleek modern automobile",
            prompt="A sleek modern automobile, three-quarter tracking angle, rainy asphalt reflections",
            model="FLUX.2 Klein 9B",
            native_dimensions="1024x768",
            final_dimensions="1024x768",
            seed=42,
            steps=20,
            upscaled=0
        )

        images = self.store.get_library_images()
        self.assertEqual(len(images), 1)
        item = images[0]
        self.assertEqual(item["chatId"], "chat-123")
        self.assertEqual(item["userPrompt"], "A sleek modern automobile")
        self.assertIn("rainy asphalt reflections", item["prompt"])
        self.assertEqual(item["nativeDimensions"], "1024x768")
        self.assertEqual(str(item["seed"]), "42")
        self.assertEqual(item["steps"], 20)
        self.assertFalse(item["upscaled"])

    def test_delete_library_image_soft_and_hard(self):
        img_dir = self.root / "images"
        img_dir.mkdir()
        test_img_path = img_dir / "flux_delete_me.png"

        im = Image.new("RGB", (512, 512), color=(50, 50, 50))
        im.save(test_img_path, format="PNG")

        self.store.index_library_image(
            path=test_img_path,
            user_prompt="To be removed",
            prompt="To be removed"
        )

        images = self.store.get_library_images()
        self.assertEqual(len(images), 1)
        img_id = images[0]["id"]

        # Soft delete: marks deleted in DB, keeps file
        ok = self.store.delete_library_image(img_id, delete_file=False)
        self.assertTrue(ok)
        self.assertEqual(len(self.store.get_library_images()), 0)
        self.assertTrue(test_img_path.is_file())

        # Re-index must not resurrect Trash. Restore explicitly, then recycle.
        self.store.index_library_image(
            path=test_img_path,
            user_prompt="Hard delete test",
            prompt="Hard delete test"
        )
        self.assertEqual(self.store.get_library_images(), [])
        self.assertEqual(self.store.workspace.manage_library([img_id],"restore")["completed"],1)
        images2 = self.store.get_library_images()
        self.assertEqual(len(images2), 1)
        img_id2 = images2[0]["id"]

        ok2 = self.store.delete_library_image(img_id2, delete_file=True)
        self.assertTrue(ok2)
        self.assertEqual(len(self.store.get_library_images()), 0)
        self.assertFalse(test_img_path.is_file())

    def test_zero_seed_and_unknown_steps_are_preserved(self):
        path=self.root/'zero-seed.png'
        Image.new('RGB',(20,20)).save(path)
        self.store.index_library_image(path,seed=0)
        item=self.store.get_library_images()[0]
        self.assertEqual(str(item['seed']),'0')
        self.assertIsNone(item['steps'])

    def test_actual_generation_metadata_and_unknown_legacy_mode(self):
        path=self.root/'metadata.png';Image.new('RGB',(20,20)).save(path)
        cid=self.store.new_chat();mid=self.store.add(cid,'assistant','Generated image.',str(path),{'duration_seconds':12.75})
        image_id=self.store.index_library_image(path,chat_id=cid,message_id=mid,steps=14)
        legacy=self.store.get_library_images()[0];self.assertIsNone(legacy['promptMode']);self.assertEqual(legacy['generationDurationSeconds'],12.75)
        self.store.workspace.link_image(image_id,None,'generate_image',{'promptMode':'creative_direction','flux_quality_preset':'fast','durationSeconds':13.5})
        item=self.store.get_library_images()[0];self.assertEqual(item['promptMode'],'creative_direction');self.assertEqual(item['qualityPreset'],'fast');self.assertEqual(item['steps'],14);self.assertEqual(item['generationDurationSeconds'],13.5)

    def test_regenerated_message_does_not_assign_new_duration_to_old_image(self):
        path=self.root/'old.png';Image.new('RGB',(20,20)).save(path)
        cid=self.store.new_chat();mid=self.store.add(cid,'assistant','New response.',str(self.root/'new.png'),{'duration_seconds':99})
        self.store.index_library_image(path,chat_id=cid,message_id=mid)
        self.assertIsNone(self.store.get_library_images()[0]['generationDurationSeconds'])


class CreativeDirectorPromptTests(unittest.TestCase):
    def test_creative_director_mode_selection(self):
        # Original mode returns the prompt unchanged without needing an engine
        worker_orig = ImagePromptWorker("mock-model", "A red sports car", None, mode="original")
        emitted = []
        worker_orig.done.connect(lambda p: emitted.append(p))
        worker_orig.run()
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0], "A red sports car")


if __name__ == "__main__":
    unittest.main()
