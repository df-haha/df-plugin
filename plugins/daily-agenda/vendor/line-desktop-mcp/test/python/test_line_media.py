import base64
import hashlib
import hmac
import io
import json
import struct
import sys
import tempfile
import uuid
from pathlib import Path
import unittest
from types import SimpleNamespace
import warnings
import wave
import zlib
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from PIL import Image
from unittest.mock import patch
PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
sys.path.insert(0,str(PYTHON))
TMP_ROOT = Path(tempfile.gettempdir())
from line_media import (MAX_PREVIEW_BYTES,binary_details,decrypt_e2ee_media,find_original,
                        image_details,inspect_attachment,media_details,preview_info)


class MediaTests(unittest.TestCase):
    @staticmethod
    def encrypt(plain):
        key = bytes(range(32))
        # Independent HMAC-based HKDF expansion per RFC5869, not the library used by reader.
        prk = hmac.digest(bytes(32),key,'sha256')
        expanded, previous = b'',b''
        for counter in range(1,4):
            previous = hmac.digest(prk,previous+b'FileEncryption'+bytes([counter]),'sha256')
            expanded += previous
        op = Cipher(algorithms.AES(expanded[:32]),modes.CTR(expanded[64:76]+bytes(4))).encryptor()
        ciphertext = op.update(plain)+op.finalize()
        encrypted = ciphertext+hmac.digest(expanded[32:64],ciphertext,'sha256')
        return base64.b64encode(key).decode(),plain,encrypted

    @staticmethod
    def fixture():
        plain = b'\x89PNG\r\n\x1a\nsynthetic binary\x00'+bytes(range(256))*3
        return MediaTests.encrypt(plain)

    @staticmethod
    def png(width,height):
        with io.BytesIO() as stream:
            Image.new('RGB',(width,height),(10,20,30)).save(stream,format='PNG')
            return stream.getvalue()

    @staticmethod
    def jpeg(width,height):
        with io.BytesIO() as stream:
            Image.new('RGB',(width,height),(10,20,30)).save(stream,format='JPEG')
            return stream.getvalue()

    @staticmethod
    def apng(width,height,default_image=False):
        first = Image.new('RGBA',(width,height),(10,20,30,255))
        frames = [Image.new('RGBA',(width,height),(40,50,60,255)),
                  Image.new('RGBA',(width,height),(70,80,90,255))]
        with io.BytesIO() as stream:
            first.save(stream,format='PNG',save_all=True,append_images=frames,
                       duration=100,loop=0,default_image=default_image)
            return stream.getvalue()

    @staticmethod
    def gif(width,height):
        with io.BytesIO() as stream:
            Image.new('RGBA',(width,height),(10,20,30,120)).save(stream,format='GIF')
            return stream.getvalue()

    @staticmethod
    def webp(width,height):
        with io.BytesIO() as stream:
            Image.new('RGBA',(width,height),(10,20,30,120)).save(stream,format='WEBP')
            return stream.getvalue()

    @staticmethod
    def wav(frames=b'\x00\x10\x20\x30'):
        with io.BytesIO() as stream:
            with wave.open(stream,'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(1)
                writer.setframerate(8_000)
                writer.writeframes(frames)
            return stream.getvalue()

    @staticmethod
    def mp3():
        # Valid ID3v2 header followed by a valid MPEG frame header; this is
        # sufficient for the intentionally metadata-only MP3 recognizer.
        return b'ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x64' + b'\x00'*16

    @staticmethod
    def decompression_bomb_png():
        def chunk(kind,payload):
            return (struct.pack('>I',len(payload))+kind+payload+
                    struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff))
        return (b'\x89PNG\r\n\x1a\n'+
                chunk(b'IHDR',struct.pack('>II',50_000,50_000)+b'\x08\x02\x00\x00\x00')+
                chunk(b'IEND',b''))

    def test_independent_hkdf_ctr_hmac_round_trip(self):
        key,plain,encrypted = self.fixture()
        self.assertEqual(decrypt_e2ee_media(encrypted,key),plain)

    def test_ciphertext_tag_truncation_and_wrong_key_fail_closed(self):
        key,_,encrypted = self.fixture()
        for index in (0,len(encrypted)-1):
            corrupt = bytearray(encrypted)
            corrupt[index] ^= 1
            with self.assertRaisesRegex(ValueError,'AUTHENTICATION_FAILED'):
                decrypt_e2ee_media(bytes(corrupt),key)
        for data,material in [(encrypted[:-1],key),(encrypted,base64.b64encode(bytes(32)).decode()),
                              (b'short',key),(encrypted,'invalid!'),(encrypted,base64.b64encode(bytes(16)).decode())]:
            with self.assertRaises(ValueError):
                decrypt_e2ee_media(data,material)

    def test_find_original_skips_bad_authenticated_image_candidates(self):
        valid = self.png(1,1)
        dimension_mismatch = self.png(2,1)
        malformed = self.decompression_bomb_png()
        declared_bytes = max(len(valid),len(dimension_mismatch),len(malformed))

        def pad(data):
            return data+b'\x00'*(declared_bytes-len(data))

        valid = pad(valid)
        dimension_mismatch = pad(dimension_mismatch)
        malformed = pad(malformed)
        material,_,valid_encrypted = self.encrypt(valid)
        _,_,malformed_encrypted = self.encrypt(malformed)
        _,_,dimension_mismatch_encrypted = self.encrypt(dimension_mismatch)
        bad_mac = bytearray(valid_encrypted)
        bad_mac[-1] ^= 1

        self.assertEqual(decrypt_e2ee_media(malformed_encrypted,material),malformed)
        self.assertEqual(decrypt_e2ee_media(dimension_mismatch_encrypted,material),dimension_mismatch)
        with self.assertRaisesRegex(ValueError,'AUTHENTICATION_FAILED'):
            decrypt_e2ee_media(bytes(bad_mac),material)
        # Inherit workspace ACLs like the other Windows engine/snapshot tests;
        # tempfile's private 0700 directory can be inaccessible in this sandbox.
        temporary_directory = Path(tempfile.gettempdir()) / ('media-test-'+uuid.uuid4().hex)
        temporary_directory.mkdir()
        self.addCleanup(temporary_directory.rmdir)
        bucket = temporary_directory/'bucket'
        bucket.mkdir()
        self.addCleanup(bucket.rmdir)
        for name,data in [('00-malformed',malformed_encrypted),('01-bad-mac',bad_mac),
                          ('02-dimension-mismatch',dimension_mismatch_encrypted),('03-valid',valid_encrypted)]:
            file = bucket/name
            self.addCleanup(file.unlink,missing_ok=True)
            file.write_bytes(data)
        with patch.object(Image,'MAX_IMAGE_PIXELS',1):
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',Image.DecompressionBombWarning)
                with self.assertRaises(Image.DecompressionBombError):
                    Image.open(io.BytesIO(malformed))
                result = find_original(temporary_directory,material,declared_bytes,(1,1))

        self.assertEqual(result['state'],'decoded')
        self.assertEqual((result['width'],result['height']),(1,1))
        self.assertEqual(result['authentication'],'HMAC-SHA256-verified')
        self.assertEqual(result['sizeCandidatesExamined'],4)

    def test_returned_original_preview_metadata_describes_its_bytes_not_thumbnail(self):
        thumb, original = self.png(2, 1), self.png(11, 7)
        root = TMP_ROOT / 'synthetic-cache'
        path = root / 'profile' / 'thumb.eimg'
        info = json.dumps({'thumbPath': str(path), 'fileName': 'synthetic.png', 'keyMaterial': 'fake'})
        original_result = {**image_details(original), 'state': 'decoded', 'authentication': 'HMAC-SHA256-verified'}
        with patch('line_media._assert_no_reparse_components'), \
             patch('line_media._read_file', return_value=SimpleNamespace(data=thumb)), \
             patch('line_media.find_original', return_value=original_result):
            result = inspect_attachment(1, None, info, 'message:synthetic', root, 'synthetic-chat')
        self.assertEqual((result['width'], result['height']), (2, 1))
        self.assertEqual((result['previewInfo']['width'], result['previewInfo']['height']), (11, 7))
        actual = base64.b64decode(result['preview']['data'])
        self.assertEqual(actual, original)
        self.assertEqual(result['previewInfo']['decodedSha256'], hashlib.sha256(actual).hexdigest())

    def test_binary_details_strict_signatures_truncation_and_preview_boundaries(self):
        mp3 = self.mp3()
        audio = binary_details(mp3)
        self.assertEqual((audio['mediaType'],audio['format'],audio['mimeType']),('audio','MP3','audio/mpeg'))
        self.assertTrue(audio['playbackUnverified'])
        self.assertNotIn('preview',audio)
        wav_bytes = self.wav()
        wav = binary_details(wav_bytes)
        self.assertEqual((wav['mediaType'],wav['format']),('audio','WAV'))
        self.assertTrue(wav['playbackUnverified'])
        self.assertEqual(base64.b64decode(wav['preview']['data']),wav_bytes)
        self.assertNotIn('preview',binary_details(self.wav(b'\x00'*(MAX_PREVIEW_BYTES+1))))
        video = binary_details(b'\x00\x00\x00\x10ftypisom\x00\x00\x00\x00')
        self.assertEqual((video['mediaType'],video['mimeType']),('video','video/mp4'))
        self.assertTrue(video['playbackUnverified'])
        for payload,expected in ((b'%PDF-1.7','PDF'),(b'PK\x03\x04data','ZIP'),
                                 (b'ID3','BINARY'),(b'RIFFWAVE','BINARY')):
            with self.subTest(expected=expected,payload=payload):
                details = binary_details(payload)
                self.assertEqual(details['format'],expected)
                self.assertNotIn('preview',details)

    def test_authenticated_audio_original_is_resolved_without_image_dimensions(self):
        plain = self.mp3()
        material,_,encrypted = self.encrypt(plain)
        root = TMP_ROOT / ('media-audio-'+uuid.uuid4().hex)
        bucket = root/'bucket'
        root.mkdir(mode=0o777,parents=True)
        bucket.mkdir(mode=0o777)
        file = bucket/'audio'
        file.write_bytes(encrypted)
        self.addCleanup(root.rmdir)
        self.addCleanup(bucket.rmdir)
        self.addCleanup(file.unlink,missing_ok=True)
        result = find_original(root,material,len(plain),None)
        self.assertEqual(result['state'],'authenticated_sniffed')
        self.assertEqual((result['mediaType'],result['format']),('audio','MP3'))
        self.assertTrue(result['playbackUnverified'])
        self.assertNotIn('preview',result)
        image_only = find_original(root,material,len(plain),None,require_image=True)
        self.assertEqual(image_only['state'],'not_cached')

    def test_unknown_type_uses_authenticated_sniff_not_numeric_guess(self):
        plain = self.mp3()
        material,_,encrypted = self.encrypt(plain)
        root = TMP_ROOT / ('synthetic-audio-cache-'+uuid.uuid4().hex)
        path = root/'profile'/'thumb.eimg'
        info = json.dumps({'thumbPath':str(path),'fileName':'clip.bin','keyMaterial':material})
        metadata = json.dumps({'FILE_SIZE':len(plain)})
        original = {**binary_details(plain),'state':'authenticated_sniffed',
                    'authentication':'HMAC-SHA256-verified'}
        with patch('line_media._assert_no_reparse_components'), \
             patch('line_media._read_file',return_value=SimpleNamespace(data=encrypted)), \
             patch('line_media.find_original',return_value=original):
            result = inspect_attachment(14,metadata,info,'message:synthetic',root,'synthetic-chat')
        self.assertEqual(result['mediaType'],'audio')
        self.assertEqual(result['thumbnail']['mediaType'],'audio')
        self.assertEqual(result['original']['mediaType'],'audio')
        self.assertEqual(result['state'],'authenticated_sniffed')
        self.assertTrue(result['playbackUnverified'])
        self.assertNotIn('preview',result)
        unresolved = inspect_attachment(14,metadata,None,'message:synthetic',root,'synthetic-chat')
        self.assertEqual((unresolved['state'],unresolved['mediaType']),('unsupported_content_type','unknown'))

    def test_gif_webp_previews_are_new_bounded_first_frames(self):
        for source,expected in ((self.gif(4,2),'GIF'),(self.webp(4,2),'WEBP')):
            with self.subTest(expected=expected):
                details = media_details(source)
                preview = base64.b64decode(details['preview']['data'])
                self.assertEqual(details['format'],expected)
                self.assertNotEqual(preview,source)
                self.assertLessEqual(len(preview),MAX_PREVIEW_BYTES)
                with Image.open(io.BytesIO(preview)) as image:
                    self.assertIn(image.format,('PNG','JPEG'))
                    self.assertEqual((image.width,image.height),(4,2))
                info = preview_info(details)
                self.assertEqual(info['decodedSha256'],hashlib.sha256(preview).hexdigest())
                self.assertEqual(info['decodedBytes'],len(preview))
                self.assertNotEqual(info['decodedSha256'],details['decodedSha256'])

        large = media_details(self.gif(4096,1))
        with Image.open(io.BytesIO(base64.b64decode(large['preview']['data']))) as image:
            self.assertEqual((image.width,image.height),(2048,1))
        self.assertEqual((large['width'],large['height']),(4096,1))

        root = TMP_ROOT / 'synthetic-gif-cache'
        path = root/'profile'/'thumb.gif'
        info = json.dumps({'thumbPath':str(path),'fileName':'synthetic.gif','keyMaterial':'fake'})
        with patch('line_media._assert_no_reparse_components'), \
             patch('line_media._read_file',return_value=SimpleNamespace(data=self.gif(4,2))), \
             patch('line_media.find_original',return_value={'state':'not_cached'}):
            result = inspect_attachment(1,None,info,'message:synthetic',root,'synthetic-chat')
        preview = base64.b64decode(result['preview']['data'])
        self.assertEqual(result['previewRole'],'derived_safe_preview')
        self.assertEqual(result['previewInfo']['decodedSha256'],hashlib.sha256(preview).hexdigest())
        self.assertNotEqual(result['previewInfo']['decodedSha256'],result['decodedSha256'])

    def test_animated_png_previews_are_derived_static_first_frames(self):
        for default_image in (False,True):
            source = self.apng(4,2,default_image)
            with self.subTest(default_image=default_image):
                with Image.open(io.BytesIO(source)) as image:
                    self.assertTrue(image.is_animated)
                    self.assertGreater(image.n_frames,1)
                details = image_details(source)
                preview = base64.b64decode(details['preview']['data'])
                self.assertEqual((details['format'],details['mimeType']),('PNG','image/png'))
                self.assertEqual(details['decodedSha256'],hashlib.sha256(source).hexdigest())
                self.assertEqual(details['decodedBytes'],len(source))
                self.assertEqual(details['_previewRole'],'derived_safe_preview')
                self.assertNotEqual(preview,source)
                with Image.open(io.BytesIO(preview)) as image:
                    self.assertIn(image.format,('PNG','JPEG'))
                    self.assertFalse(getattr(image,'is_animated',False))
                    self.assertEqual(getattr(image,'n_frames',1),1)
                    self.assertEqual((image.width,image.height),(4,2))
                info = preview_info(details)
                self.assertEqual(info['decodedSha256'],hashlib.sha256(preview).hexdigest())
                self.assertEqual(info['decodedBytes'],len(preview))
                self.assertNotEqual(info['decodedSha256'],details['decodedSha256'])

    def test_static_png_jpeg_previews_preserve_source_bytes(self):
        for source,expected,mime_type in ((self.png(4,2),'PNG','image/png'),
                                          (self.jpeg(4,2),'JPEG','image/jpeg')):
            with self.subTest(expected=expected):
                details = image_details(source)
                self.assertEqual((details['format'],details['mimeType']),(expected,mime_type))
                self.assertEqual(details['_previewRole'],'source_bytes')
                self.assertEqual(base64.b64decode(details['preview']['data']),source)
                self.assertEqual(preview_info(details)['decodedSha256'],details['decodedSha256'])

    def test_kind_one_rejects_authenticated_non_image_and_bad_image_bytes(self):
        root = TMP_ROOT / 'synthetic-kind-one-cache'
        path = root/'profile'/'thumb.eimg'
        audio_key,_,audio_encrypted = self.encrypt(self.mp3())
        info = json.dumps({'thumbPath':str(path),'fileName':'synthetic.jpg','keyMaterial':audio_key})
        with patch('line_media._assert_no_reparse_components'), \
             patch('line_media._read_file',return_value=SimpleNamespace(data=audio_encrypted)), \
             patch('line_media.find_original',return_value={'state':'not_cached'}):
            mismatch = inspect_attachment(1,None,info,'message:synthetic',root,'synthetic-chat')
        self.assertEqual((mismatch['state'],mismatch['reason'],mismatch['mediaType']),
                         ('opaque_media','IMAGE_DECODE_FAILED','image'))
        self.assertNotIn('preview',mismatch)
        tampered = bytearray(audio_encrypted)
        tampered[-1] ^= 1
        with patch('line_media._assert_no_reparse_components'), \
             patch('line_media._read_file',return_value=SimpleNamespace(data=bytes(tampered))), \
             patch('line_media.find_original',return_value={'state':'not_cached'}):
            unauthenticated = inspect_attachment(1,None,info,'message:synthetic',root,'synthetic-chat')
        self.assertEqual((unauthenticated['state'],unauthenticated['reason']),
                         ('opaque_media','MEDIA_AUTHENTICATION_FAILED'))
        self.assertNotIn('preview',unauthenticated)

        malformed = b'RIFF\x0c\x00\x00\x00WEBPVP8 '
        with self.assertRaises(ValueError):
            image_details(malformed)
        oversized_gif = (b'GIF89a'+b'\xff\xff\xff\xff'+b'\x80\x00\x00'+
                         b'\x00\x00\x00\xff\xff\xff;')
        with self.assertRaises(ValueError):
            image_details(oversized_gif)
        bad_key,_,bad_encrypted = self.encrypt(malformed)
        bad_info = json.dumps({'thumbPath':str(path),'fileName':'synthetic.webp','keyMaterial':bad_key})
        with patch('line_media._assert_no_reparse_components'), \
             patch('line_media._read_file',return_value=SimpleNamespace(data=bad_encrypted)), \
             patch('line_media.find_original',return_value={'state':'not_cached'}):
            bad = inspect_attachment(14,None,bad_info,'message:synthetic',root,'synthetic-chat')
        self.assertEqual((bad['state'],bad['reason']),('opaque_media','MEDIA_DECODE_FAILED'))
        self.assertNotIn('preview',bad)

    def test_sticker_metadata_exposes_only_presence_flags_without_cache_scan(self):
        metadata = json.dumps({'STKPKGID':'private-package','STKID':'private-sticker',
                               'STKVER':'1','STKTXT':'private-text'})
        with patch('line_media._read_file') as read_file:
            result = inspect_attachment(7,metadata,None,'message:synthetic',
                                       TMP_ROOT,'synthetic-chat')
        read_file.assert_not_called()
        self.assertEqual((result['state'],result['mediaType']),('metadata_only','sticker'))
        self.assertEqual(result['sticker'],{'hasPackageId':True,'hasStickerId':True,
                                            'hasVersion':True,'hasText':True})
        self.assertNotIn('private-package',json.dumps(result))
        self.assertNotIn('private-text',json.dumps(result))

    def test_attachment_name_exposes_only_a_safe_stored_basename(self):
        root = TMP_ROOT / 'synthetic-forward-name-cache'
        result = inspect_attachment(14,None,json.dumps({'fileName':'報價單 9月.pdf'}),
                                    'message:synthetic',root,'synthetic-chat')
        self.assertEqual(result['fileName'],'報價單 9月.pdf')
        self.assertEqual(result['state'],'layout_unavailable')
        for unsafe in ('','.', '..','../secret.pdf','..\\secret.pdf','C:\\secret.pdf',
                       '/secret.pdf','folder/secret.pdf','folder\\secret.pdf',
                       'file:stream','bad\x00name','bad\nname','bad\x7fname',
                       ' trailing.pdf ', 'a'*256):
            result = inspect_attachment(14,None,json.dumps({'fileName':unsafe}),
                                        'message:synthetic',root,'synthetic-chat')
            self.assertNotIn('fileName',result,unsafe)


if __name__ == '__main__':
    unittest.main()
