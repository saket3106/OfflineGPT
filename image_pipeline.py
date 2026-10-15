"""Raster-image validation and normalization for the local FLUX/ESRGAN path."""

from __future__ import annotations

import os
import shutil
import struct
import time
import zlib
import json
import re
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader, QImageWriter


MIN_IMAGE_BYTES = 24
MAX_IMAGE_BYTES = 512 * 1024 * 1024
MAX_IMAGE_SIDE = 16_384
MAX_IMAGE_PIXELS = 50_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

def extract_image_constraints(prompt):
    """Conservative local extraction; the complete original remains authoritative."""
    text=str(prompt or '').strip();low=text.lower()
    terms={
        'color':r'\b(?:black|white|red|blue|green|yellow|silver|grey|gray|orange|purple|pink|brown|gold)\b',
        'finish':r'\b(?:glossy|gloss|matte|metallic|satin)\b',
        'era':r'\b(?:modern|vintage|classic|retro|contemporary|(?:19|20)\d{2})\b',
        'identity':r'\b(?:ford|mustang|porsche|911|ferrari|lamborghini|bmw|mercedes|toyota|coupe|sedan|suv|convertible|headphones|earbuds)\b',
        'count':r'\b(?:one|single|two|three|four|five|\d+)\s+(?:cars?|vehicles?|people|persons?|subjects?|products?|shirts?|objects?)\b',
        'perspective':r'\b(?:front|rear|side|three[- ]quarter|overhead|profile)\b',
        'resolution':r'\b(?:4k|2k|1080p|\d{3,4}\s*[x×]\s*\d{3,4}|\d{1,2}:\d{1,2})\b',
    }
    result={key:list(dict.fromkeys(re.findall(pattern,low))) for key,pattern in terms.items()}
    result['prohibitions']=re.findall(r'\b(?:no|without|do not|never)\s+[^,.;]+',text,re.I)
    result['original']=text
    return result

def preserve_image_constraints(original,planned,mode='smart_enhance'):
    constraints=extract_image_constraints(original);planned=str(planned or '').strip();low=planned.lower();conflicts=[]
    for key in ('color','finish','era','identity','count','perspective'):
        for term in constraints[key]:
            if term not in low:conflicts.append('missing '+key+': '+term)
    if 'modern' in constraints['era'] and re.search(r'\b(vintage|classic|retro|19\d{2})\b',low):conflicts.append('modern subject changed to vintage')
    if 'black' in constraints['color'] and re.search(r'\b(?:red|blue|green|yellow|white)\b.{0,24}\b(?:paint|finish|mustang|car|body)\b',low):conflicts.append('body color conflict')
    if constraints['prohibitions'] and any(p.lower() not in low for p in constraints['prohibitions']):conflicts.append('missing explicit prohibition')
    if not re.search(r'\b(rain|wet|snow|storm)\b',original,re.I) and re.search(r'\b(rain|rainy|rain.slicked|wet|snow|storm)\b',low):conflicts.append('unrequested weather')
    # No expensive self-review loop. A contradictory plan is discarded, never
    # sent alongside conflicting color/year instructions to diffusion.
    if conflicts or not planned:
        final=original.strip()+' Preserve every specified attribute. Use clear photographic lighting, natural material texture and coherent geometry.'
    else:final=original.strip()+' '+planned
    return final,{'mode':mode,'constraints':constraints,'plannerAccepted':not conflicts and bool(planned),'conflicts':conflicts}

from enum import Enum


class ImageOperation(str, Enum):
    TEXT_TO_IMAGE = "TEXT_TO_IMAGE"
    GENERATIVE_IMAGE_EDIT = "GENERATIVE_IMAGE_EDIT"
    FAST_RECOLOR = "FAST_RECOLOR"
    UPSCALE = "UPSCALE"
    RESIZE = "RESIZE"
    CROP = "CROP"


def recolor_intent(prompt):
    low = str(prompt or '').lower().strip()
    if not low:
        return None
    # Strip negative / prohibitive / comparative clauses first
    # e.g., 'rather than silently using a full generative edit', 'do not regenerate the scene', 'instead of generative edit'
    operative = re.sub(
        r'\b(?:rather\s+than|instead\s+of|do\s+not|don\'t|no|without|never|avoid|prevent)\s+[^,.;]+',
        '',
        low
    )

    # Strip full sentences that are explicit preservation directives
    # e.g., 'Preserve headlights, windows, grille, tires, wheels, wet road, buildings, lighting and camera perspective.'
    cleaned_sentences = []
    for sent in re.split(r'[.;]', operative):
        s = sent.strip()
        if re.match(r'^(?:preserve|keep|protect|maintain|leave\s+unchanged|leave\s+the\s+same)\b', s):
            continue
        cleaned_sentences.append(s)
    operative = ' '.join(cleaned_sentences)

    # Check if a generative edit was POSITIVELY requested in operative text
    if re.search(r'\b(generative edit|diffusion edit|ai edit|regenerate image|regenerate scene)\b', operative):
        return None
    if re.search(r'\b(remove object|add object|add person|put object|replace car|replace vehicle|resize image|crop image)\b', operative):
        return None
    if re.search(r'\b(?:into|to)\s+(?:a\s+)?(?:different|new|another|truck|airplane|boat|motorcycle|bus|van)\b', operative):
        return None
    if re.search(r'\b(cargo bed|different vehicle|different model|new model|change vehicle model)\b', operative):
        return None
    if re.search(r'\b(?:put|place|move)\b.{0,20}\b(?:on|in|into)\b', operative):
        return None

    # Color match:
    color_names_pattern = r'(red|black|white|blue|green|yellow|orange|purple|pink|silver|gold|crimson|ruby|burgundy|grey|gray)'
    finish_pattern = r'(?:(?:glossy|matte|metallic|dark|bright|deep|light|satin)\s+)*'

    match = re.search(
        r'\b(?:change|make|turn|recolou?r|repaint|paint)\b.{0,120}\b(?:to\s+|into\s+)?' + finish_pattern + color_names_pattern + r'\b',
        low
    )
    if not match:
        match = re.search(
            r'\b(?:to\s+|into\s+)' + finish_pattern + color_names_pattern + r'\b',
            low
        )
    if not match and ('recolor' in low or 'fast recolor' in low or 'recolour' in low):
        match = re.search(finish_pattern + color_names_pattern + r'\b', low)

    # Subject check
    has_subject = bool(re.search(r'\b(car|vehicle|mustang|porsche|lamborghini|ferrari|bmw|body|paint|shirt|product|finish|exterior|colou?r)\b', low))
    if not match or not has_subject:
        return None

    colors = {
        'red': '#ca151c', 'crimson': '#b8141b', 'ruby': '#9b111e', 'burgundy': '#800020',
        'black': '#151515', 'white': '#eeeeee', 'blue': '#185bc4', 'green': '#198044',
        'yellow': '#edcb20', 'orange': '#e87720', 'purple': '#7939b4', 'pink': '#dd649c',
        'silver': '#b9bec6', 'gold': '#d4af37', 'grey': '#808080', 'gray': '#808080'
    }
    target_color_name = match.group(1)
    finish = 'matte' if 'matte' in low else ('metallic' if 'metallic' in low else 'glossy')
    from_match = re.search(r'\b(?:from|the|only\s+the)\s+(black|white|red|blue|green|yellow|silver|grey|gray)\b.{0,40}\b(?:paint|body|car|vehicle|mustang|porsche|exterior)\b', low)
    from_color = from_match.group(1) if from_match else 'black'

    return {
        'color': colors.get(target_color_name, '#ca151c'),
        'color_name': target_color_name,
        'finish': finish,
        'from_color': from_color,
        'auto_mask': True,
        'requiresMask': False
    }


def select_image_operation(prompt: str, has_image: bool = False) -> tuple[ImageOperation | None, dict]:
    """Structured operation selector prioritizing non-generative deterministic tools."""
    low = str(prompt or '').lower().strip()
    if not low:
        return ImageOperation.TEXT_TO_IMAGE, {'action': 'generate_image'}

    operative = re.sub(
        r'\b(?:rather\s+than|instead\s+of|do\s+not|don\'t|no|without|never|avoid|prevent)\s+[^,.;]+',
        '',
        low
    )

    # 1. Informational queries about existing images route to vision/chat analysis, not image operations
    if has_image and (
        re.search(r'\b(analyze|describe|read|inspect|what is|tell me about)\b.*\b(image|photo|picture|this)\b', low)
        or any(k in low for k in ('what is in this', 'what is this image', 'describe this image', 'what color is'))
    ):
        return None, {}

    # 2. FAST_RECOLOR: Route simple, unambiguous color-only edits BEFORE expensive planning
    intent = recolor_intent(prompt)
    if intent:
        return ImageOperation.FAST_RECOLOR, intent

    # 3. CROP
    if re.search(r'\b(?:crop|trim|cut\s+out|cut\s+borders?)\b', operative):
        return ImageOperation.CROP, {'action': 'crop'}

    # 4. RESIZE (Pixel dimensions / aspect or resize command)
    if re.search(r'\b(?:resize|scale)\b', operative) and not re.search(r'\b(?:upscale|super[- ]resolution)\b', operative):
        return ImageOperation.RESIZE, {'action': 'resize'}

    # 5. UPSCALE (AI super-resolution / 4K enhancement)
    if re.search(r'\b(?:upscale|super[- ]resolution|4k\s+upscale|upscale\s+to\s+4k)\b', operative) or (
        has_image and re.search(r'\b(?:enhance|sharpen|denoise|restore)\b', operative)
    ):
        return ImageOperation.UPSCALE, {'action': 'upscale'}

    # 6. GENERATIVE_IMAGE_EDIT (Modifying scene, adding/removing objects, generative AI)
    if has_image:
        if (
            re.search(r'\b(generative edit|ai edit|diffusion edit|inpaint|outpaint|regenerate scene|regenerate image)\b', operative)
            or re.search(r'\b(add|remove|replace|insert|delete)\b.{1,40}\b(background|person|object|tree|building|wheel|car)\b', operative)
            or re.search(r'\b(change|modify|edit)\b.*\b(image|photo|picture|scene|background)\b', operative)
        ):
            return ImageOperation.GENERATIVE_IMAGE_EDIT, {'action': 'generative_edit'}

    # 7. TEXT_TO_IMAGE (Creating brand new image from prompt)
    image_format = r'\b(image|picture|photo|photograph|wallpaper|artwork|illustration|portrait|rendering|scene|visual|logo|poster|icon|banner|painting|sketch|diagram)\b'
    image_subject = r'\b(car|cars|vehicle|vehicles|automobile|automobiles|lamborghini|lamborgini|ferrari|porsche|bugatti|mustang|motorcycle|person|cat|dog|house|city|landscape|mountain|ocean|galaxy|robot)\b'
    is_t2i = (
        bool(re.search(r'\b(generate|create|make|draw|render|produce|design|paint|illustrate)\b.*' + image_format, operative))
        or bool(re.search(r'\b(image|picture|photo|photograph|wallpaper|artwork|illustration|portrait|rendering|scene)\b.*\b(of|for|showing|featuring)\b', operative))
        or bool(re.search(r'^\s*(?:(?:please|can you|could you)\s+)*(?:generate|create|make|draw|render|produce|design|paint|illustrate)\b.*' + image_subject, operative))
    )
    if is_t2i:
        return ImageOperation.TEXT_TO_IMAGE, {'action': 'generate_image'}

    return None, {}


class ImageRunProfile:
    """Measured CLI timings; absent backend measurements remain unavailable.

    Backend stage measurements overlap process wall time. They are not summed
    into a fabricated total or progress percentage.
    """
    FIELDS = ('REQUEST_RECEIVED_MS','PROMPT_PLANNING_MS','MODEL_UNLOAD_WAIT_MS',
              'FLUX_PROCESS_START_MS','FLUX_MODEL_LOAD_MS','MODEL_MEMORY_TRANSFER_MS',
              'TEXT_ENCODING_MS','IMAGE_ENCODING_MS','CONDITIONING_MS','SAMPLING_MS',
              'SAMPLING_STEP_COUNT','AVERAGE_STEP_MS','VAE_DECODE_MS',
              'IMAGE_EDIT_PREPROCESS_MS','IMAGE_EDIT_INFERENCE_MS','UPSCALE_MS',
              'OUTPUT_ENCODING_MS','OUTPUT_SAVE_MS','UI_RENDER_DELAY_MS','TOTAL_WALL_MS')

    def __init__(self, action, memory_probe=None):
        self.started=time.perf_counter();self.action=action;self.memory_probe=memory_probe
        self.values=dict.fromkeys(self.FIELDS);self.values['REQUEST_RECEIVED_MS']=0
        self.attempts=[];self.hardware=[];self.process_ids=[];self.stop_event=threading.Event()
        self.copy_seconds=0.;self.tensor_load_seconds=0.;self.last_sample=0.

    def milliseconds(self, key, seconds):self.values[key]=round(seconds*1000,2)

    def line(self, line):
        patterns={'SAMPLING_MS':r'sampling completed, taking ([\d.]+)s',
                  'TEXT_ENCODING_MS':r'get_learned_condition completed, taking ([\d.]+)s',
                  'VAE_DECODE_MS':r'decode_first_stage completed, taking ([\d.]+)s',
                  'IMAGE_ENCODING_MS':r'encode_first_stage completed, taking ([\d.]+)s',
                  'UPSCALE_MS':r'input_image_tensor upscaled, taking ([\d.]+)s'}
        for key,pattern in patterns.items():
            match=re.search(pattern,line)
            if match:self.milliseconds(key,float(match[1]))
        match=re.search(r'loading tensors completed, taking ([\d.]+)s.*copy_to_backend: ([\d.]+)s',line)
        if match:
            self.tensor_load_seconds+=float(match[1]);self.copy_seconds+=float(match[2])
            self.milliseconds('MODEL_MEMORY_TRANSFER_MS',self.copy_seconds)

    def start_sampling(self):
        # NVIDIA CLI polling is bounded and off the inference / UI threads.
        def sample():
            try:
                import psutil
            except ImportError:psutil=None
            while not self.stop_event.is_set():
                row={'elapsedMs':round((time.perf_counter()-self.started)*1000,2)}
                if self.memory_probe:
                    row['memory']=self.memory_probe()
                    memory=row['memory']
                    if memory.get('pagefile_total') is not None and memory.get('pagefile_available') is not None:
                        row['committedBytes']=memory['pagefile_total']-memory['pagefile_available']
                if psutil:row.update(cpuPercent=psutil.cpu_percent(),pagefileUsedBytes=psutil.swap_memory().used)
                try:
                    output=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu,clocks.sm,power.draw,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if output.returncode==0:
                        row['gpu']=dict(zip(('name','driver','dedicatedUsedMiB','totalMiB','utilizationPercent','clockMHz','powerW','temperatureC'),(v.strip() for v in output.stdout.splitlines()[0].split(','))))
                except (OSError,subprocess.TimeoutExpired,IndexError):pass
                if psutil:
                    owned=[]
                    for pid in tuple(self.process_ids):
                        try:
                            process=psutil.Process(pid);owned.append({'pid':pid,'rssBytes':process.memory_info().rss,'cpuSeconds':sum(process.cpu_times()[:2])})
                        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
                    row['ownedProcesses']=owned
                self.hardware.append(row)
                if len(self.hardware)>=3600:break
                self.stop_event.wait(1)
        self.sampler=threading.Thread(target=sample,daemon=True);self.sampler.start()

    def finish(self, folder, status='success'):
        self.stop_event.set()
        if getattr(self,'sampler',None):self.sampler.join(timeout=2.2)
        self.milliseconds('TOTAL_WALL_MS',time.perf_counter()-self.started)
        steps=self.values.get('SAMPLING_STEP_COUNT');sampling=self.values.get('SAMPLING_MS')
        if sampling is not None and steps:self.values['AVERAGE_STEP_MS']=round(sampling/steps,2)
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        import uuid
        path=folder/('image-profile-'+uuid.uuid4().hex[:12]+'.json')
        result={'version':1,'action':self.action,'status':status,'stages':self.values,
                'attempts':self.attempts,'processIds':self.process_ids,'hardware':self.hardware,
                'tensorLoadMs':round(self.tensor_load_seconds*1000,2),
                'notes':['Stage timers may overlap; only TOTAL_WALL_MS is end-to-end worker wall time.',
                         'Null means the installed backend did not expose a separable measurement.',
                         'Shared GPU memory, pagefile IO and thermal throttling are unavailable from this sampler.',
                         'One-shot CLI contexts reload per request; filesystem cache is not warm model residency.']}
        path.write_text(json.dumps(result,indent=2),encoding='utf-8')
        self.path=path
        return {'profilePath':str(path),'stages':dict(self.values),'attemptCount':len(self.attempts)}

_FORMATS = {
    "png": ("PNG", "image/png"),
    "jpeg": ("JPEG", "image/jpeg"),
    "webp": ("WebP", "image/webp"),
    "bmp": ("BMP", "image/bmp"),
}


class ImageValidationError(ValueError):
    """An image is not safe to pass to the next pipeline stage."""


@dataclass(frozen=True)
class ImageInfo:
    path: Path
    format: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    signature_hex: str
    extension_matches: bool


def _signature_format(prefix: bytes) -> tuple[str, str] | None:
    if prefix.startswith(PNG_SIGNATURE):
        return _FORMATS["png"]
    if prefix.startswith(b"\xff\xd8\xff"):
        return _FORMATS["jpeg"]
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return _FORMATS["webp"]
    if prefix.startswith(b"BM"):
        return _FORMATS["bmp"]
    return None


def _text_payload_kind(prefix: bytes) -> str:
    sample = prefix[:2048].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if sample.startswith(b"data:image/"):
        return "base64/data URL text"
    if b"<svg" in sample:
        return "SVG markup"
    if sample.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "HTML response"
    if sample.startswith((b"{", b"[")):
        return "JSON response"
    return "unknown/non-raster data"


def _validate_png_structure(path: Path, file_size: int) -> tuple[int, int]:
    """Check PNG chunk bounds and CRCs without loading the compressed image at once."""
    with path.open("rb") as stream:
        if stream.read(8) != PNG_SIGNATURE:
            raise ImageValidationError("PNG signature is invalid")

        width = height = 0
        chunk_number = 0
        found_iend = False
        while stream.tell() < file_size:
            header = stream.read(8)
            if len(header) != 8:
                raise ImageValidationError("PNG has a truncated chunk header")
            chunk_length, chunk_type = struct.unpack(">I4s", header)
            if chunk_length > file_size - stream.tell() - 4:
                raise ImageValidationError(f"PNG {chunk_type!r} chunk exceeds the file length")
            if chunk_number == 0:
                if chunk_type != b"IHDR" or chunk_length != 13:
                    raise ImageValidationError("PNG does not begin with a valid IHDR chunk")
                ihdr = stream.read(13)
                width, height = struct.unpack(">II", ihdr[:8])
                crc = zlib.crc32(chunk_type)
                crc = zlib.crc32(ihdr, crc)
                remaining = 0
            else:
                crc = zlib.crc32(chunk_type)
                remaining = chunk_length

            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    raise ImageValidationError(f"PNG {chunk_type!r} chunk is truncated")
                crc = zlib.crc32(block, crc)
                remaining -= len(block)

            stored_crc_raw = stream.read(4)
            if len(stored_crc_raw) != 4:
                raise ImageValidationError(f"PNG {chunk_type!r} chunk is missing its CRC")
            stored_crc = struct.unpack(">I", stored_crc_raw)[0]
            if (crc & 0xFFFFFFFF) != stored_crc:
                raise ImageValidationError(f"PNG {chunk_type.decode('ascii', 'replace')} chunk CRC mismatch")

            chunk_number += 1
            if chunk_type == b"IEND":
                if chunk_length != 0:
                    raise ImageValidationError("PNG IEND chunk is malformed")
                if stream.tell() != file_size:
                    raise ImageValidationError("PNG contains unexpected data after IEND")
                found_iend = True
                break

        if not found_iend:
            raise ImageValidationError("PNG is missing its IEND chunk")
        if width <= 0 or height <= 0:
            raise ImageValidationError("PNG dimensions are zero")
        return width, height


def _log(log_path: str | os.PathLike | None, message: str) -> None:
    if not log_path:
        return
    try:
        target = Path(log_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", errors="replace") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _raw_file_summary(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            prefix = stream.read(32)
        return (
            f"bytes={path.stat().st_size}; content-type={(_signature_format(prefix) or ('unknown', 'application/octet-stream'))[1]}; "
            f"first32_hex={prefix.hex()}; first32_text={prefix[:32]!r}"
        )
    except OSError as exc:
        return f"raw-response-unavailable={exc}"


def validate_image_file(
    file_path: str | os.PathLike,
    *,
    stage: str,
    log_path: str | os.PathLike | None = None,
) -> tuple[QImage, ImageInfo]:
    """Signature-check, bound, and fully decode one local raster image."""
    path = Path(file_path)
    try:
        if not path.is_file():
            raise ImageValidationError("file is missing")
        size_bytes = path.stat().st_size
        if size_bytes < MIN_IMAGE_BYTES:
            with path.open("rb") as stream:
                short_prefix = stream.read(32)
            kind = _text_payload_kind(short_prefix)
            raise ImageValidationError(f"file is empty or too short ({size_bytes} bytes; {kind})")
        if size_bytes > MAX_IMAGE_BYTES:
            raise ImageValidationError(f"file exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MiB safety limit")
        with path.open("rb") as stream:
            prefix = stream.read(32)
        detected = _signature_format(prefix)
        if detected is None:
            raise ImageValidationError(f"not a supported raster image ({_text_payload_kind(prefix)})")
        display_format, mime_type = detected

        if display_format == "PNG":
            width, height = _validate_png_structure(path, size_bytes)
        else:
            width = height = 0

        reader = QImageReader(str(path))
        reader.setDecideFormatFromContent(True)
        reader.setAutoTransform(True)
        reader_size = reader.size()
        if reader_size.isValid():
            reader_width, reader_height = reader_size.width(), reader_size.height()
            if width and (width, height) != (reader_width, reader_height):
                raise ImageValidationError(
                    f"header dimensions {width}x{height} disagree with decoder dimensions {reader_width}x{reader_height}"
                )
            width, height = reader_width, reader_height
        if width <= 0 or height <= 0:
            raise ImageValidationError("decoder could not read positive image dimensions")
        if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
            raise ImageValidationError(f"dimensions {width}x{height} exceed the {MAX_IMAGE_SIDE}px side limit")
        pixels = width * height
        if pixels > MAX_IMAGE_PIXELS:
            raise ImageValidationError(
                f"dimensions {width}x{height} ({pixels:,} pixels) exceed the {MAX_IMAGE_PIXELS:,}-pixel decode limit"
            )

        decoded_format = bytes(reader.format()).decode("ascii", "replace").lower()
        expected_reader_format = "jpeg" if display_format == "JPEG" else display_format.lower()
        if decoded_format and decoded_format not in {expected_reader_format, "jpg" if expected_reader_format == "jpeg" else expected_reader_format}:
            raise ImageValidationError(
                f"file signature says {display_format}, but the image decoder reports {decoded_format}"
            )
        image = reader.read()
        if image.isNull():
            raise ImageValidationError(f"raster decoder rejected {display_format}: {reader.errorString()}")
        if image.width() <= 0 or image.height() <= 0 or image.sizeInBytes() <= 0:
            raise ImageValidationError("decoded raster has invalid dimensions or no pixel data")
        if image.width() * image.height() > MAX_IMAGE_PIXELS:
            raise ImageValidationError("decoded image exceeds the safe pixel limit")

        suffix = path.suffix.lower()
        extension_matches = suffix in {
            ".png" if display_format == "PNG" else ".jpg" if display_format == "JPEG" else ".webp" if display_format == "WebP" else ".bmp",
            ".jpeg" if display_format == "JPEG" else "",
        }
        if suffix and not extension_matches:
            raise ImageValidationError(
                f"file extension {suffix!r} does not match detected {mime_type} content"
            )
        info = ImageInfo(path, display_format, mime_type, image.width(), image.height(), size_bytes, prefix[:16].hex(), extension_matches)
        _log(
            log_path,
            f"VALID stage={stage!r} format={info.format} mime={info.mime_type} dimensions={info.width}x{info.height} "
            f"bytes={info.size_bytes} signature={info.signature_hex} extension_matches={info.extension_matches} path={str(path)!r}",
        )
        return image, info
    except Exception as exc:
        details = _raw_file_summary(path)
        _log(log_path, f"INVALID stage={stage!r} error={str(exc)!r} raw={details} path={str(path)!r}")
        if isinstance(exc, ImageValidationError):
            raise ImageValidationError(f"{stage}: {exc}") from exc
        raise ImageValidationError(f"{stage}: {exc}") from exc


def write_normalized_image(
    image: QImage,
    output_path: str | os.PathLike,
    *,
    size: tuple[int, int],
    output_format: str,
    quality: int = 96,
    log_path: str | os.PathLike | None = None,
) -> ImageInfo:
    """Encode, then re-open and validate the actual raster bytes on disk."""
    path = Path(output_path)
    width, height = size
    if width <= 0 or height <= 0 or width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
        raise ImageValidationError(f"requested output dimensions {width}x{height} exceed safe image limits")
    result = image
    if (result.width(), result.height()) != (width, height):
        result = result.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    fmt = "jpeg" if output_format.lower() in {"jpeg", "jpg"} else "png"
    if fmt == "jpeg":
        result = result.convertToFormat(QImage.Format_RGB32)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QImageWriter(str(path), fmt.encode("ascii"))
    writer.setQuality(max(70, min(100, int(quality))))
    if not writer.write(result):
        raise ImageValidationError(f"image encoder failed: {writer.errorString()}")
    _, info = validate_image_file(path, stage="final frontend image", log_path=log_path)
    expected_mime = "image/jpeg" if fmt == "jpeg" else "image/png"
    if info.mime_type != expected_mime:
        raise ImageValidationError(f"encoder wrote {info.mime_type}, expected {expected_mime}")
    if (info.width, info.height) != (width, height):
        raise ImageValidationError(
            f"encoder wrote {info.width}x{info.height}, expected {width}x{height}"
        )
    return info


def copy_validated_image(
    source_path: str | os.PathLike,
    output_path: str | os.PathLike,
    *,
    stage: str,
    log_path: str | os.PathLike | None = None,
) -> ImageInfo:
    """Copy an already validated image without re-encoding or altering pixels."""
    _, source_info = validate_image_file(source_path, stage=f"{stage} source", log_path=log_path)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_info.path, target)
    _, target_info = validate_image_file(target, stage=stage, log_path=log_path)
    if (source_info.mime_type, source_info.width, source_info.height) != (
        target_info.mime_type, target_info.width, target_info.height
    ):
        raise ImageValidationError(f"{stage}: copied raster metadata changed unexpectedly")
    return target_info

