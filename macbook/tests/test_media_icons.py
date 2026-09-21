"""Original Touch Bar icon data becomes appearance-only floating-menu patches."""
import base64
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
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
            self.assertEqual(patch['BTTMenuItemIconColor1'+suffix],'0, 0, 0, 255')
            self.assertEqual(patch['BTTMenuItemIconColor1Hover'+suffix],'0, 0, 0, 255')
            self.assertEqual(patch['BTTMenuItemIconPosition'+suffix],4)
        self.assertEqual(patch['BTTMenuElementTooltip'],'Stop playback')
        self.assertFalse(any(k for k in patch if any(w in k for w in
            ['Action','Script','MinWidth','MaxWidth','MinHeight','MaxHeight','Modifier','DisableDrag','Order'])))

    def test_five_minute_seek_keeps_distinct_caption_not_a_second_emoji(self):
        patch=icons.icon_patch(b'MM\x00*data','Back 5 minutes','5m')
        self.assertIn('5m',patch['BTTMenuAttributedText'])
        self.assertEqual(patch['BTTMenuItemIconPosition'],6)
        self.assertEqual(patch['BTTMenuItemImageHeight'],18)
        self.assertEqual(patch['BTTMenuItemImageWidth'],22)
        self.assertNotIn('5m',icons.icon_patch(b'MM\x00*data','Back 20 seconds','')['BTTMenuAttributedText'])

    def test_short_seeks_have_twenty_second_captions_below_black_icons(self):
        short = [item for item in icons.TARGETS if '20 seconds' in item[2]]
        self.assertEqual(len(short),2)
        for _,_,label,caption in short:
            self.assertEqual(caption,'20s')
            patch=icons.icon_patch(b'MM\x00*data',label,caption)
            for suffix in ('','Dark'):
                self.assertIn('20s',patch['BTTMenuAttributedText'+suffix])
                self.assertIn('\\red0\\green0\\blue0',patch['BTTMenuAttributedText'+suffix])
                self.assertEqual(patch['BTTMenuItemIconPosition'+suffix],6)
                self.assertEqual(patch['BTTMenuItemImageHeight'+suffix],18)

    def test_scope_is_eight_transport_buttons_not_status_volume_or_power(self):
        self.assertEqual(len(icons.TARGETS),8)
        self.assertEqual(len({target for target,_,_,_ in icons.TARGETS}),8)
        self.assertEqual([caption for _,_,_,caption in icons.TARGETS].count('5m'),2)
        self.assertEqual([caption for _,_,_,caption in icons.TARGETS].count('20s'),2)
        self.assertNotIn('BDFEAEF1-6961-4B05-9A62-E70C54332C4C',[x[0] for x in icons.TARGETS])

    def test_speaker_scope_is_only_the_four_main_controls(self):
        self.assertEqual([x[2] for x in icons.SPEAKER_TARGETS],
                         ['Join Sonos speakers','Unjoin Sonos speakers','Volume down','Volume up'])
        self.assertEqual(len({x[0] for x in icons.SPEAKER_TARGETS}),4)

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

    @unittest.skipUnless(sys.platform=='darwin','requires native macOS RTF support')
    def test_native_rtf_recolor_preserves_text_fonts_sizes_and_line_breaks(self):
        raw=(r'{\rtf1\ansi{\fonttbl{\f0 Helvetica;}}{\colortbl;\red255\green255\blue255;}'
             r'\pard\qc\f0\fs28\cf1 Recent\line Notes {\b bold} {\i italic} '
             r'\fs18 20s \uc1\u-10178?\u-8828?}')
        with tempfile.TemporaryDirectory(prefix='btt-text-color-test-') as directory:
            path=Path(directory)/'input.json'
            path.write_text(json.dumps({'items':[{'uuid':'test','type':774,
                'config':{'BTTMenuAttributedText':raw}}],'icons':[]}))
            changes=json.loads(icons.worker('main-style-plan',path))
            recolored=changes[0]['patch']['BTTMenuAttributedText']
            # Recoloring black text again must not keep reserializing its RTF.
            path.write_text(json.dumps({'items':[{'uuid':'test','type':774,
                'config':changes[0]['patch']}],'icons':[]}))
            self.assertEqual(json.loads(icons.worker('main-style-plan',path)),[])
        code=r'''ObjC.import('AppKit');
function run(argv) {
 function parse(raw) {return $.NSAttributedString.alloc.initWithRTFDocumentAttributes($(raw).dataUsingEncoding($.NSUTF8StringEncoding),null);}
 const a=parse(argv[0]),b=parse(argv[1]);
 if(ObjC.unwrap(a.string)!==ObjC.unwrap(b.string))throw Error('text changed');
 for(let i=0;i<Number(a.length);i++) {
  const fa=a.attributeAtIndexEffectiveRange($.NSFontAttributeName,i,null),fb=b.attributeAtIndexEffectiveRange($.NSFontAttributeName,i,null);
  if(ObjC.unwrap(fa.fontName)!==ObjC.unwrap(fb.fontName)||Number(fa.pointSize)!==Number(fb.pointSize))throw Error('font changed');
  const pa=a.attributeAtIndexEffectiveRange($.NSParagraphStyleAttributeName,i,null),pb=b.attributeAtIndexEffectiveRange($.NSParagraphStyleAttributeName,i,null);
  if(Number(pa.alignment)!==Number(pb.alignment))throw Error('alignment changed');
  const color=b.attributeAtIndexEffectiveRange($.NSForegroundColorAttributeName,i,null).colorUsingColorSpace($.NSColorSpace.genericRGBColorSpace);
  if(Number(color.redComponent)!==0||Number(color.greenComponent)!==0||Number(color.blueComponent)!==0||Number(color.alphaComponent)!==1)throw Error('not black');
 }
 return 'preserved';
}'''
        result=subprocess.run(['/usr/bin/osascript','-l','JavaScript','-e',code,raw,recolored],
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'preserved')

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
        self.assertNotEqual(original,same)
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
            with self.assertRaisesRegex(RuntimeError,'outside the verified preset bundle'):
                icons.worker('normalize-configs',plan)


if __name__=='__main__':unittest.main()
