"""Inspect only file paths referenced by messages already selected in scope."""
import base64
import hashlib
import hmac
import io
from pathlib import Path
import re
import unicodedata
import warnings
import wave
from PIL import Image
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from line_scoped_core import object_metadata, metadata_shape
from line_encrypted_snapshot import _assert_no_reparse_components, _read_file, _SourceChanged, SnapshotError

MAX_MEDIA_BYTES = 20*1024*1024
MAX_PREVIEW_BYTES = 256*1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_DERIVED_PREVIEW_DIMENSION = 2048
MAX_WAV_CHANNELS = 8
MAX_WAV_SAMPLE_RATE = 384_000

_IMAGE_MIME_TYPES = {
    'PNG': 'image/png',
    'JPEG': 'image/jpeg',
    'GIF': 'image/gif',
    'WEBP': 'image/webp',
}


def decrypt_e2ee_media(data, material):
    """LINE Encryption Overview v2.2, pp.9-10: authenticate before decrypting.

    https://www.lycorp.co.jp/en/privacy-security/line-encryption-whitepaper-ver2.2.pdf
    No algorithm guessing or unauthenticated fallback for opaque cache entries.
    """
    if not isinstance(material,str) or len(data) < 33:
        raise ValueError('MEDIA_KEY_UNAVAILABLE')
    try:
        key = base64.b64decode(material,validate=True)
    except ValueError:
        raise ValueError('MEDIA_KEY_UNAVAILABLE') from None
    if len(key) != 32:
        raise ValueError('MEDIA_KEY_UNAVAILABLE')
    derived = HKDF(algorithm=hashes.SHA256(),length=76,salt=None,info=b'FileEncryption').derive(key)
    ciphertext, tag = data[:-32],data[-32:]
    if not hmac.compare_digest(hmac.digest(derived[32:64],ciphertext,'sha256'),tag):
        raise ValueError('MEDIA_AUTHENTICATION_FAILED')
    op = Cipher(algorithms.AES(derived[:32]),modes.CTR(derived[64:]+bytes(4))).decryptor()
    return op.update(ciphertext)+op.finalize()


def key_shape(value):
    # Diagnostic shapes only. No key material values leave this module.
    result = {'type':type(value).__name__}
    if isinstance(value,str):
        result['length'] = len(value)
        if re.fullmatch(r'[a-fA-F0-9]+',value or '-') and len(value)%2 == 0:
            result['hexBytes'] = len(value)//2
        try:
            result['base64Bytes'] = len(base64.b64decode(value,validate=True))
        except ValueError:
            pass
    elif isinstance(value,dict):
        result['keys'] = [k for k in value if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,80}',k)]
    return result


def _looks_like_image(data):
    return (data.startswith((b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n',b'GIF87a',b'GIF89a')) or
            (len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP'))


def _add_preview(result, data, mime_type, format_name, *, width=None, height=None, role):
    """Attach only bounded, already-validated bytes and their own metadata."""
    if len(data) > MAX_PREVIEW_BYTES:
        return
    info = {
        'mediaType': 'image' if mime_type.startswith('image/') else 'audio',
        'format': format_name,
        'mimeType': mime_type,
        'decodedSha256': hashlib.sha256(data).hexdigest(),
        'decodedBytes': len(data),
    }
    if width is not None and height is not None:
        info.update({'width': width, 'height': height})
    result['preview'] = {'mimeType': mime_type, 'data': base64.b64encode(data).decode()}
    result['_previewInfo'] = info
    result['_previewRole'] = role


def _jpeg_source(frame):
    if frame.mode == 'RGBA':
        background = Image.new('RGBA', frame.size, (255,255,255,255))
        background.alpha_composite(frame)
        return background.convert('RGB')
    return frame.convert('RGB')


def _derived_first_frame(image):
    """Encode one validated first frame, never return raw GIF/WebP bytes."""
    # Bound pixels before the first encoder invocation. Source dimensions and
    # source hash are captured by `image_details` before this normalization.
    image.thumbnail((MAX_DERIVED_PREVIEW_DIMENSION,MAX_DERIVED_PREVIEW_DIMENSION),
                    Image.Resampling.LANCZOS)
    frame = image.copy()
    if frame.mode not in ('RGB','RGBA'):
        frame = frame.convert('RGBA' if 'transparency' in image.info else 'RGB')
    for _ in range(12):
        stream = io.BytesIO()
        frame.save(stream, format='PNG')
        encoded = stream.getvalue()
        if len(encoded) <= MAX_PREVIEW_BYTES:
            return encoded, 'image/png', 'PNG', frame.width, frame.height
        rgb = _jpeg_source(frame)
        for quality in (80,65,50,35):
            stream = io.BytesIO()
            rgb.save(stream, format='JPEG', quality=quality, optimize=True)
            encoded = stream.getvalue()
            if len(encoded) <= MAX_PREVIEW_BYTES:
                return encoded, 'image/jpeg', 'JPEG', frame.width, frame.height
        if frame.width == 1 and frame.height == 1:
            break
        frame = frame.resize((max(1,frame.width//2),max(1,frame.height//2)),Image.Resampling.LANCZOS)
    return None


def image_details(data):
    """Validate supported image bytes and expose only safe bounded previews."""
    if not isinstance(data,bytes) or not _looks_like_image(data):
        raise ValueError('IMAGE_DECODE_FAILED')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.seek(0)
                if image.format not in _IMAGE_MIME_TYPES:
                    raise ValueError('IMAGE_FORMAT_UNSUPPORTED')
                if not (1 <= image.width * image.height <= MAX_IMAGE_PIXELS):
                    raise ValueError('IMAGE_DIMENSIONS_REFUSED')
                image.load()
                result = {'mediaType':'image','format':image.format,
                          'mimeType':_IMAGE_MIME_TYPES[image.format],
                          'width':image.width,'height':image.height,
                          'decodedSha256':hashlib.sha256(data).hexdigest(),'decodedBytes':len(data),
                          'formatValidation':'Pillow_first_frame'}
                if (image.format in ('PNG','JPEG') and
                        not getattr(image,'is_animated',False) and
                        getattr(image,'n_frames',1) == 1):
                    _add_preview(result,data,result['mimeType'],image.format,
                                 width=image.width,height=image.height,role='source_bytes')
                else:
                    derived = _derived_first_frame(image)
                    if derived:
                        preview, mime_type, format_name, width, height = derived
                        _add_preview(result,preview,mime_type,format_name,
                                     width=width,height=height,role='derived_safe_preview')
                return result
    except (Image.DecompressionBombError,Image.DecompressionBombWarning,ValueError):
        raise
    except Exception as error:
        raise ValueError('IMAGE_DECODE_FAILED') from error


def _valid_mpeg_frame(data):
    if len(data) < 4 or data[0] != 0xff or data[1] & 0xe0 != 0xe0:
        return False
    version = (data[1] >> 3) & 0x03
    layer = (data[1] >> 1) & 0x03
    bitrate = (data[2] >> 4) & 0x0f
    sample_rate = (data[2] >> 2) & 0x03
    return version != 1 and layer != 0 and bitrate not in (0,15) and sample_rate != 3


def _valid_id3_mp3(data):
    if len(data) < 14 or data[:3] != b'ID3' or data[3] not in (2,3,4):
        return False
    if data[5] & 0x0f or any(value & 0x80 for value in data[6:10]):
        return False
    tag_bytes = ((data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9])
    return 10 + tag_bytes + 4 <= len(data) and _valid_mpeg_frame(data[10 + tag_bytes:])


def _wav_details(data):
    try:
        with wave.open(io.BytesIO(data),'rb') as reader:
            channels, sample_width = reader.getnchannels(), reader.getsampwidth()
            sample_rate, frame_count = reader.getframerate(), reader.getnframes()
            if (reader.getcomptype() != 'NONE' or not 1 <= channels <= MAX_WAV_CHANNELS or
                    not 1 <= sample_width <= 4 or not 1 <= sample_rate <= MAX_WAV_SAMPLE_RATE):
                return None
            expected = channels * sample_width * frame_count
            if expected > MAX_MEDIA_BYTES or len(reader.readframes(frame_count)) != expected:
                return None
    except (EOFError,OSError,wave.Error):
        return None
    result = {'mediaType':'audio','format':'WAV','mimeType':'audio/wav',
              'decodedSha256':hashlib.sha256(data).hexdigest(),'decodedBytes':len(data),
              'formatValidation':'wave_header_and_frames','playbackUnverified':True,
              'audioChannels':channels,'audioSampleRate':sample_rate,
              'audioSampleWidthBytes':sample_width,'audioFrames':frame_count}
    _add_preview(result,data,'audio/wav','WAV',role='source_bytes')
    return result


def _ogg_audio(data):
    if len(data) < 27 or data[:4] != b'OggS' or data[4] != 0 or data[5] & ~0x07:
        return False
    segments = data[26]
    header_end = 27 + segments
    if header_end > len(data):
        return False
    payload_end = header_end + sum(data[27:header_end])
    if payload_end > len(data):
        return False
    payload = data[header_end:payload_end]
    return payload.startswith((b'OpusHead',b'\x01vorbis',b'\x7fFLAC'))


def _mp4_details(data):
    if len(data) < 16 or data[4:8] != b'ftyp':
        return None
    size = int.from_bytes(data[:4],'big')
    if not 16 <= size <= len(data):
        return None
    brand = data[8:12]
    if brand in (b'M4A ',b'M4B ',b'M4P '):
        return 'audio','MP4','audio/mp4'
    if brand in (b'isom',b'iso2',b'mp41',b'mp42',b'avc1',b'hvc1',b'hev1',b'vp09',b'av01',b'dash'):
        return 'video','MP4','video/mp4'
    return 'file','MP4','application/mp4'


def binary_details(data):
    """Identify bounded non-image payloads without exposing unvalidated bytes."""
    if not isinstance(data,bytes) or not data:
        raise ValueError('MEDIA_DECODE_FAILED')
    wav = _wav_details(data) if data.startswith(b'RIFF') else None
    if wav:
        return wav
    media_type, format_name, mime_type, validation = 'unknown','BINARY','application/octet-stream','unrecognized'
    playback_unverified = False
    if _valid_id3_mp3(data) or _valid_mpeg_frame(data):
        media_type, format_name, mime_type, validation = 'audio','MP3','audio/mpeg','signature_only'
        playback_unverified = True
    elif _ogg_audio(data):
        media_type, format_name, mime_type, validation = 'audio','OGG','audio/ogg','container_header_only'
        playback_unverified = True
    elif len(data) >= 8 and data[:4] == b'fLaC' and 8 + int.from_bytes(data[5:8],'big') <= len(data):
        media_type, format_name, mime_type, validation = 'audio','FLAC','audio/flac','container_header_only'
        playback_unverified = True
    elif details := _mp4_details(data):
        media_type, format_name, mime_type = details
        validation = 'container_header_only'
        playback_unverified = media_type in ('audio','video')
    elif len(data) >= 8 and data[:5] == b'%PDF-':
        media_type, format_name, mime_type, validation = 'file','PDF','application/pdf','signature_only'
    elif len(data) >= 4 and data[:4] in (b'PK\x03\x04',b'PK\x05\x06',b'PK\x07\x08'):
        media_type, format_name, mime_type, validation = 'file','ZIP','application/zip','signature_only'
    result = {'mediaType':media_type,'format':format_name,'mimeType':mime_type,
              'decodedSha256':hashlib.sha256(data).hexdigest(),'decodedBytes':len(data),
              'formatValidation':validation}
    if playback_unverified:
        result['playbackUnverified'] = True
    return result


def media_details(data):
    """Decode images; otherwise return strict, non-playable binary metadata."""
    if _looks_like_image(data):
        return image_details(data)
    return binary_details(data)


def _public_details(details, *, include_preview=True):
    return {key:value for key,value in details.items()
            if key not in ('_previewInfo','_previewRole') and (include_preview or key != 'preview')}


def preview_info(details):
    info = details.get('_previewInfo')
    if isinstance(info,dict):
        return dict(info)
    return {key:details[key] for key in
            ('mediaType','format','mimeType','width','height','decodedSha256','decodedBytes')
            if key in details}


def _resolution_state(details):
    if details.get('mediaType') == 'unknown':
        return 'authenticated_unclassified'
    if details.get('formatValidation') in ('signature_only','container_header_only'):
        return 'authenticated_sniffed'
    return 'decoded'


def sticker_metadata(metadata):
    return {
        'hasPackageId': isinstance(metadata,dict) and 'STKPKGID' in metadata,
        'hasStickerId': isinstance(metadata,dict) and 'STKID' in metadata,
        'hasVersion': isinstance(metadata,dict) and 'STKVER' in metadata,
        'hasText': isinstance(metadata,dict) and 'STKTXT' in metadata,
    }


def find_original(directory, material, declared_bytes, declared_dimensions, *, require_image=False):
    """Match only size candidates, then authenticate with this scoped message's key.

    Directory enumeration reads file metadata. Unrelated candidate bytes are
    never decrypted or decoded unless their complete HMAC validates.
    """
    if type(declared_bytes) is not int or not 1 <= declared_bytes <= MAX_MEDIA_BYTES-32:
        return {'state':'source_size_unavailable'}
    candidates, examined = [],0
    try:
        _assert_no_reparse_components(directory,final_may_be_missing=False)
        paths = []
        for bucket in directory.iterdir():
            _assert_no_reparse_components(bucket,final_may_be_missing=False)
            if bucket.is_dir():
                paths.extend(bucket.iterdir())
            elif bucket.is_file():
                paths.append(bucket)
            if len(paths) > 10000:
                return {'state':'cache_inventory_limit'}
        for index,path in enumerate(paths):
            if index >= 10000:
                return {'state':'cache_inventory_limit'}
            if path.lstat().st_size == declared_bytes+32:
                candidates.append(path)
        if len(candidates) > 16:
            return {'state':'cache_candidates_ambiguous'}
        matches = {}
        for path in candidates:
            try:
                observed = _read_file(path,optional=False,keep_data=True,max_bytes=MAX_MEDIA_BYTES)
                data = observed.data
                examined += 1
                plain = decrypt_e2ee_media(data,material)
                details = image_details(plain) if require_image else media_details(plain)
                if declared_dimensions and (details.get('width'),details.get('height')) != declared_dimensions:
                    continue
                matches[details['decodedSha256']] = {**details,'state':_resolution_state(details),
                    'sourceRole':'original_as_available_in_LINE','authentication':'HMAC-SHA256-verified',
                    'sourceAssociation':'message_key_HMAC_and_declared_size_dimensions',
                    'sourceBytes':len(data),'sourceSha256':hashlib.sha256(data).hexdigest()}
            except Exception:
                continue
        if len(matches) == 1:
            return {**next(iter(matches.values())),'sizeCandidatesExamined':examined}
        return {'state':'not_cached' if not matches else 'multiple_authenticated_originals',
                'sizeCandidatesExamined':examined}
    except (OSError,SnapshotError):
        return {'state':'not_cached'}


def _safe_attachment_name(value):
    """Return only a bounded display basename from LINE's stored contentInfo."""
    if (not isinstance(value,str) or not 1 <= len(value) <= 255
            or value != value.strip() or value in ('.','..')
            or any(char in value for char in ('/','\\',':'))
            or any(unicodedata.category(char) == 'Cc' for char in value)):
        return None
    return value


def inspect_attachment(kind,metadata,info,source_ref,cache_root,chat_id):
    result = {'state':'not_applicable' if kind == 0 else 'not_resolved',
              'metadata':metadata_shape(metadata),'info':metadata_shape(info)}
    if kind == 0:
        return result
    meta_obj = object_metadata(metadata)
    if kind == 1:
        result['mediaType'] = 'image'
    elif kind == 7:
        result['mediaType'] = 'sticker'
        result['sticker'] = sticker_metadata(meta_obj)
        result['state'] = 'metadata_only'
        return result
    else:
        # No local enum proves other numeric content types.  A cache payload
        # can refine this only after HMAC authentication and strict sniffing.
        result['mediaType'] = 'unknown'
    obj = object_metadata(info)
    if not isinstance(obj,dict):
        if kind == 7:
            result['state'] = 'metadata_only'
        elif kind != 1:
            result['state'] = 'unsupported_content_type'
        return result
    result['keyMaterialShape'] = key_shape(obj.get('keyMaterial'))
    filename = obj.get('fileName')
    result['fileNameShape'] = {'type':type(filename).__name__,
                               'length':len(filename) if isinstance(filename,str) else None,
                               'isAbsolute':Path(filename).is_absolute() if isinstance(filename,str) else False,
                               'suffix':Path(filename).suffix if isinstance(filename,str) else None}
    safe_name = _safe_attachment_name(filename)
    if safe_name is not None:
        result['fileName'] = safe_name
    if isinstance(meta_obj,dict):
        try:
            result['declaredFileBytes'] = int(meta_obj.get('FILE_SIZE',0))
        except (ValueError,TypeError):
            pass
        content = object_metadata(meta_obj.get('MEDIA_CONTENT_INFO'))
        result['mediaContentInfoShape'] = metadata_shape(meta_obj.get('MEDIA_CONTENT_INFO'))
        if isinstance(content,dict) and all(type(content.get(key)) is int for key in ('width','height')):
            result['declaredDimensions'] = [content['width'],content['height']]
    value = obj.get('thumbPath')
    if not isinstance(value,str) or not value or '\0' in value:
        result['state'] = 'metadata_only' if kind == 7 else (
            'path_unavailable' if kind == 1 else 'layout_unavailable')
        return result
    path = Path(value)
    result['pathKind'] = 'absolute' if path.is_absolute() else 'relative'
    if not path.is_absolute():
        result['state'] = 'relative_path_unresolved'
        return result
    try:
        if not path.is_relative_to(cache_root) or '..' in path.parts:
            result['state'] = 'path_outside_cache'
            return result
        _assert_no_reparse_components(path,final_may_be_missing=False)
        observed = _read_file(path,optional=False,keep_data=True,max_bytes=MAX_MEDIA_BYTES)
        data = observed.data
        result.update({'sourceRole':'thumbnail','sourceAssociation':'message_contentInfo_thumbPath',
                       'fileRef':hashlib.sha256(str(path).encode()).hexdigest()[:24],
                       'sourceSuffix':path.suffix,'sourceBytes':len(data),'sourceSha256':hashlib.sha256(data).hexdigest()})
        thumbnail = {}
        try:
            # An unencrypted thumbnail is accepted only for the observed image
            # type. Other numeric content types require HMAC authentication
            # before cache bytes can refine their otherwise unknown type.
            encrypted = not (kind == 1 and _looks_like_image(data))
            if encrypted:
                data = decrypt_e2ee_media(data,obj.get('keyMaterial'))
            thumbnail = image_details(data) if kind == 1 else media_details(data)
            result.update({'state':_resolution_state(thumbnail),
                           'authentication':'HMAC-SHA256-verified' if encrypted else 'not_encrypted'})
            if kind == 1:
                result.update(_public_details(thumbnail))
            else:
                result['thumbnail'] = _public_details(thumbnail,include_preview=False)
                if thumbnail.get('mediaType') != 'unknown':
                    for key in ('mediaType','format','mimeType','width','height','decodedSha256','decodedBytes',
                                'formatValidation','playbackUnverified','audioChannels','audioSampleRate',
                                'audioSampleWidthBytes','audioFrames'):
                        if key in thumbnail:
                            result[key] = thumbnail[key]
        except ValueError as error:
            result['state'] = 'opaque_media'
            result['reason'] = str(error) if str(error) in ('MEDIA_KEY_UNAVAILABLE','MEDIA_AUTHENTICATION_FAILED') else (
                'IMAGE_DECODE_FAILED' if kind == 1 else 'MEDIA_DECODE_FAILED')
        except Exception:
            result['state'] = 'opaque_media'
            result['reason'] = 'IMAGE_DECODE_FAILED' if kind == 1 else 'MEDIA_DECODE_FAILED'
        if not isinstance(chat_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',chat_id):
            raise ValueError('INVALID_CHAT_ID')
        # LINE 26.4 uses the SHA-1 of the chat ID for this cache directory.
        # Location/size only select candidates; per-message HMAC proves association.
        chat_bucket = hashlib.sha1(chat_id.encode('utf-8')).hexdigest()
        original_root = cache_root/path.relative_to(cache_root).parts[0]/chat_bucket/'m'
        original = find_original(original_root,obj.get('keyMaterial'),result.get('declaredFileBytes'),
                                 tuple(result['declaredDimensions']) if result.get('declaredDimensions') else None,
                                 require_image=(kind == 1))
        result['original'] = _public_details(original,include_preview=False)
        original_resolved = original.get('state') in ('decoded','authenticated_sniffed','authenticated_unclassified')
        original_allowed = original_resolved and (kind != 1 or original.get('mediaType') == 'image')
        if original_allowed and kind != 1:
            for key in ('mediaType','format','mimeType','width','height','decodedSha256','decodedBytes'):
                if key in original:
                    result[key] = original[key]
            result['state'] = 'decoded'
            result['authentication'] = original['authentication']
            if original.get('state') != 'decoded':
                result['state'] = original['state']
        if original_allowed and original.get('preview'):
            result['preview'] = original['preview']
            result['previewRole'] = original.get('_previewRole','original_as_available_in_LINE')
            result['previewInfo'] = preview_info(original)
        elif thumbnail.get('preview'):
            result['preview'] = thumbnail['preview']
            result['previewRole'] = thumbnail.get('_previewRole','thumbnail')
            result['previewInfo'] = preview_info(thumbnail)
        if result.get('preview'):
            # `previewInfo` is set from the exact source above. In particular,
            # a GIF/WebP normalized first frame describes derived bytes rather
            # than the source animation's hash or size.
            pass
        return result
    except (OSError,SnapshotError,_SourceChanged,ValueError):
        result['state'] = 'media_unavailable'
        return result
