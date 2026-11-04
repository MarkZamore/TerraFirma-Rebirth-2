import copy,importlib.util,io,json,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('native_metadata_prepare',Path(__file__).with_name('prepare.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

def native(version='1.21.1',protocol=767,world=3955):
    return json.dumps({'id':version,'protocol_version':protocol,'world_version':world},separators=(',',':')).encode()
def client(root,raw,version='1.21.1',duplicate=False):
    path=root/'client.jar'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('native/Unrelated.class',b'not copied into projection')
        if raw is not None:
            archive.writestr('version.json',raw)
            if duplicate:archive.writestr('version.json',raw)
    (root/'portable-pack.json').write_text(json.dumps({'schemaVersion':1,'minecraftVersion':version,'clientJar':'client.jar','loader':{'type':'neoforge','version':'21.1.248'}}))
    lock_path=root/'tools/upstream-lock.json'
    lock=json.loads(lock_path.read_text()) if lock_path.exists() else {'schemaVersion':1,'authorFilesChanged':[],'supportFiles':[],'nativeAuthorField':'preserved'}
    lock['supportFiles']=[row for row in lock['supportFiles'] if row['path']!='client.jar']
    lock['supportFiles'].insert(0,{'path':'client.jar','sizeBytes':path.stat().st_size,'sha256':p.digest(path)})
    lock_path.parent.mkdir(parents=True,exist_ok=True);lock_path.write_bytes(p.json_bytes(lock))
    return lock
def fixture(root,raw=None):
    root.mkdir(parents=True,exist_ok=True)
    lock=client(root,native() if raw is None else raw)
    (root/'launcher').mkdir();(root/p.FLAGS).write_bytes(b'\xef\xbb\xbf# author flags\r\n-Xbootclasspath/a:author-resource.jar\r\n-Dauthor.flag=keep\r\n')
    (root/p.OWNED).write_bytes(b'# author ownership\r\nconfig/author.toml\r\n')
    return lock
def snapshot(root):return {path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}

class SourceMetadataTests(unittest.TestCase):
    def test_exact_metadata_only_preserves_author_prefixes_and_source_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);lock=fixture(root);before=snapshot(root)
            plan,report=p.prepare(root);p.apply(root,plan)
            with zipfile.ZipFile(root/p.OUTPUT) as archive:
                self.assertEqual(archive.namelist(),['version.json']);self.assertEqual(archive.read('version.json'),native())
            self.assertTrue((root/p.FLAGS).read_bytes().startswith(before[p.FLAGS]))
            self.assertTrue((root/p.OWNED).read_bytes().startswith(before[p.OWNED]))
            self.assertEqual((root/'client.jar').read_bytes(),before['client.jar'])
            updated=json.loads((root/'tools/upstream-lock.json').read_text())
            self.assertEqual(updated['nativeAuthorField'],lock['nativeAuthorField']);self.assertEqual(updated['authorFilesChanged'],[])
            self.assertEqual(report['classEntries'],0);self.assertFalse(report['futureVersionTableOrJarCountGate'])
    def test_retry_is_byte_identical_and_check_requires_no_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);plan,_=p.prepare(root);p.apply(root,plan);before=snapshot(root)
            retry,report=p.prepare(root);p.apply(root,retry)
            self.assertEqual(before,snapshot(root));self.assertEqual(report['changedPaths'],[])
            run=subprocess.run([sys.executable,'-B',str(Path(p.__file__).resolve()),'--pack',str(root),'--check'],capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(before,snapshot(root))
    def test_refreshed_pinned_native_metadata_is_copied_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);plan,_=p.prepare(root);p.apply(root,plan)
            # Synthetic repinned bytes test copying, not a native protocol claim.
            refreshed=native(protocol=768,world=3956)
            client(root,refreshed);plan,report=p.prepare(root);p.apply(root,plan)
            self.assertEqual(report['minecraftVersion'],'1.21.1');self.assertEqual(report['protocolVersion'],768)
            with zipfile.ZipFile(root/p.OUTPUT) as archive:self.assertEqual(archive.read('version.json'),refreshed)
    def test_unverified_source_and_wrong_native_id_are_rejected(self):
        for wrong in ('hash','id','boolean','duplicate-property','oversize'):
            with self.subTest(wrong=wrong),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);fixture(root)
                if wrong=='hash':(root/'client.jar').write_bytes(b'changed after verification')
                else:
                    raw={'id':native('wrong'),'boolean':native(protocol=True),'duplicate-property':b'{"id":"wrong","id":"1.21.1","protocol_version":767,"world_version":3955}',
                         'oversize':b' '*65537}[wrong]
                    client(root,raw)
                before=snapshot(root)
                with self.assertRaises(ValueError):p.prepare(root)
                self.assertEqual(before,snapshot(root))
    def test_duplicate_zip_metadata_is_not_chosen_arbitrarily(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root)
            with self.assertWarns(UserWarning):client(root,native(),duplicate=True)
            with self.assertRaises(ValueError):p.prepare(root)
    def test_unowned_or_edited_source_projection_is_preserved_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);(root/'config').mkdir();(root/p.OUTPUT).write_bytes(b'author data collision')
            with self.assertRaises(ValueError):p.prepare(root)
            self.assertEqual((root/p.OUTPUT).read_bytes(),b'author data collision')
    def test_missing_native_metadata_retires_only_owned_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);plan,_=p.prepare(root);p.apply(root,plan)
            client(root,None);plan,report=p.prepare(root);p.apply(root,plan)
            self.assertEqual(report['status'],'NO_NATIVE_METADATA');self.assertFalse((root/p.OUTPUT).exists())
            self.assertNotIn(p.FLAG,(root/p.FLAGS).read_text(encoding='utf-8-sig'))
            self.assertIn('-Xbootclasspath/a:author-resource.jar',(root/p.FLAGS).read_text(encoding='utf-8-sig'))
            self.assertIn('config/author.toml',(root/p.OWNED).read_text())
    def test_failure_rolls_back_all_already_written_source_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);plan,_=p.prepare(root);before=snapshot(root);real=p.atomic;calls=0
            def fail_once(path,value):
                nonlocal calls
                calls+=1
                if calls==2:raise OSError('controlled second-write failure')
                return real(path,value)
            with patch.object(p,'atomic',fail_once),self.assertRaises(OSError):p.apply(root,plan)
            self.assertEqual(before,snapshot(root))
    def test_game_tree_and_escaped_client_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);(root/'saves').mkdir()
            with self.assertRaises(ValueError):p.prepare(root)
            (root/'saves').rmdir();descriptor=json.loads((root/'portable-pack.json').read_text());descriptor['clientJar']='../outside.jar'
            (root/'portable-pack.json').write_text(json.dumps(descriptor))
            with self.assertRaises(ValueError):p.prepare(root)
    def test_bad_report_path_fails_before_apply_and_stale_check_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);before=snapshot(root);script=str(Path(p.__file__).resolve())
            run=subprocess.run([sys.executable,'-B',script,'--pack',str(root),'--apply','--report','config/not-source-only.json'],capture_output=True)
            self.assertNotEqual(run.returncode,0);self.assertEqual(before,snapshot(root))
            run=subprocess.run([sys.executable,'-B',script,'--pack',str(root),'--check'],capture_output=True)
            self.assertNotEqual(run.returncode,0);self.assertEqual(before,snapshot(root))

if __name__=='__main__':unittest.main()
