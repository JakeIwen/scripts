"""Original Touch Bar icon data becomes appearance-only floating-menu patches."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch

DIRECTORY=Path(__file__).resolve().parents[1]/'bettertouchtool'
sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('port_media_icons',DIRECTORY/'port_media_icons.py')
icons=importlib.util.module_from_spec(spec);spec.loader.exec_module(icons)
sys.path.pop(0)


class IconTests(unittest.TestCase):
    def test_coredata_prefix_is_removed_without_altering_image_bytes(self):
        for payload in [b'\x89PNG\r\n\x1a\ndata',b'MM\x00*tiff',b'II*\x00tiff']:
            self.assertEqual(icons.image_bytes(b'\x01'+payload),payload)
            self.assertEqual(icons.image_bytes(payload),payload)
        for bad in [None,b'',b'not an image',b'\x02external-reference']:
            with self.assertRaises(RuntimeError):icons.image_bytes(bad)

    def test_patch_reuses_artwork_and_tints_it_black_in_both_appearances(self):
        payload=b'\x89PNG\r\n\x1a\ndata'
        patch=icons.icon_patch(payload,'Stop playback','')
        for suffix in ['', 'Dark']:
            self.assertEqual(base64.b64decode(patch['BTTMenuItemImage'+suffix]),payload)
            self.assertEqual(patch['BTTMenuItemIconType'+suffix],1)
            self.assertEqual(patch['BTTMenuItemImageChangeColor'+suffix],1)
        self.assertFalse(any(k for k in patch if any(w in k for w in
            ['Action','Script','MinWidth','MaxWidth','MinHeight','MaxHeight','Modifier','DisableDrag','Order'])))

    def test_status_only_plan_does_not_revisit_speaker_icons_or_other_labels(self):
        captured=[]
        def worker(mode,path):
            captured.append(json.loads(Path(path).read_text()))
            self.assertEqual(mode,'main-style-plan')
            return '[]'
        saved={icons.STATUS_BUTTON:{'parent':icons.MEDIA,'ZGESTURETYPE':773,'config':{}},
               'another-button':{'parent':icons.MEDIA,'ZGESTURETYPE':773,'config':{}}}
        with patch.object(icons,'records',return_value=saved),patch.object(icons,'worker',side_effect=worker), \
                patch.object(icons,'build_changes',side_effect=AssertionError('must not rebuild speaker icons')):
            self.assertEqual(icons.build_main_style_changes(Path('/unused'),status_only=True),[])
        self.assertEqual([x['uuid'] for x in captured[0]['items']],[icons.STATUS_BUTTON])
        self.assertEqual(captured[0]['icons'],[])

    @unittest.skipUnless(sys.platform=='darwin','requires native macOS icon decoder')
    def test_native_image_verification_checks_pixels_not_png_encoding(self):
        def png(pixels,level):
            def chunk(kind,data):
                return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
            return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,1,8,6,0,0,0))+ \
                chunk(b'IDAT',zlib.compress(b'\0'+pixels,level))+chunk(b'IEND',b'')
        original=png(bytes([0,0,0,255,0,0,0,128]),0)
        same=png(bytes([0,0,0,255,0,0,0,128]),9)
        wrong=png(bytes([0,0,0,128,0,0,0,255]),9)
        different_color=png(bytes([255,0,0,255,0,255,0,128]),9)
        with tempfile.TemporaryDirectory(prefix='btt-image-match-test-') as directory:
            root=Path(directory);saved=root/'icon.png';plan=root/'plan.json'
            expected={'BTTMenuItemIconType':1,'BTTMenuItemImage':base64.b64encode(original).decode(),
                      'BTTMenuItemImageChangeColor':1}
            actual={'BTTMenuItemIconType':7,'BTTMenuItemIconPresetPath':'BTT_PRESET_PATH/icon.png',
                    'BTTMenuItemImageChangeColor':1}
            for image,matches in [(same,True),(wrong,False),(different_color,True)]:
                saved.write_bytes(image)
                plan.write_text(json.dumps({'presetPath':str(root),'pairs':[{'actual':actual,'expected':expected}]}))
                normalized=json.loads(icons.worker('normalize-configs',plan))[0]
                self.assertEqual(normalized,expected if matches else actual)
            # Untinted images cannot use alpha-only equivalence.
            expected['BTTMenuItemImageChangeColor']=0;actual['BTTMenuItemImageChangeColor']=0
            plan.write_text(json.dumps({'presetPath':str(root),'pairs':[{'actual':actual,'expected':expected}]}))
            self.assertEqual(json.loads(icons.worker('normalize-configs',plan))[0],actual)
            actual['BTTMenuItemIconPresetPath']=str(root.parent/'outside.png')
            plan.write_text(json.dumps({'presetPath':str(root),'pairs':[{'actual':actual,'expected':expected}]}))
            with self.assertRaises(RuntimeError):
                icons.worker('normalize-configs',plan)


if __name__=='__main__':unittest.main()
