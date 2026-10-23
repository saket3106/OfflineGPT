"""Local Kokoro CPU synthesis and bounded Qt playback. No network access."""

import hashlib

import json

import os

import re

import sys

import time

import uuid

import wave

from pathlib import Path



PREVIEW_TEXT = "Hello! I'm Offline GPT. I can read your answers aloud with a natural voice."

SPEEDS = (.75, 1., 1.25, 1.5, 1.75, 2.)



# Bounded audio cache configuration

MAX_CACHE_FILES = 200

MAX_CACHE_BYTES = 50 * 1024 * 1024  # 50 MB

TARGET_CACHE_BYTES = 35 * 1024 * 1024  # 35 MB





def sanitize_spoken_text(text):
    """Sanitize text before passing to speech: strip code blocks, tables, file paths, and URLs."""
    text = str(text or '')
    # Replace fenced code blocks with code block omitted
    text = re.sub(r'```[^\n]*\n.*?```', ' code block omitted ', text, flags=re.S)
    text = re.sub(r'```.*?```', ' code block omitted ', text, flags=re.S)
    # Strip image/link markdown syntax before stripping URLs so labels are preserved
    text = re.sub(r'!\[([^]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', text)
    # Strip markdown table lines
    text = re.sub(r'(?m)^\s*\|.*?\|\s*$', '', text)
    # Strip URLs
    text = re.sub(r'https?://\S+', '', text)
    # Strip absolute Windows/POSIX file paths
    text = re.sub(r'\b[A-Za-z]:\\[^\s"\'<>]+', '', text)
    text = re.sub(r'(?:/[a-zA-Z0-9_\-\.]+){3,}', '', text)
    # Strip markdown headers, blockquotes, list markers
    text = re.sub(r'(?m)^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+)', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[*_`~]+', '', text)
    return normalize_speech_text(text.strip())


def normalize_speech_text(text):

    """Clean markdown, strip emojis, and expand common acronyms and units for natural pronunciation."""

    text = str(text or '')

    # Strip emojis (Unicode ranges for emoticons, symbols, pictographs)

    emoji_pattern = re.compile('[\U00010000-\U0010ffff]', flags=re.UNICODE)

    text = emoji_pattern.sub('', text)



    # Common spoken abbreviations

    text = re.sub(r'\be\.g\.,?\s*', 'for example, ', text, flags=re.I)

    text = re.sub(r'\bi\.e\.,?\s*', 'that is, ', text, flags=re.I)

    text = re.sub(r'\betc\.', 'etcetera', text, flags=re.I)

    text = re.sub(r'\bvs\.\s*', 'versus ', text, flags=re.I)

    text = re.sub(r'\bvs\b', 'versus', text, flags=re.I)

    text = re.sub(r'\bw/\b', 'with', text, flags=re.I)

    text = re.sub(r'\bw/o\b', 'without', text, flags=re.I)



    # Currency ($50 -> 50 dollars)

    text = re.sub(r'\$(\d+(?:\.\d+)?)', r'\1 dollars', text)



    # Decimals: 1.5 -> 1 point 5 (prevents improper sentence splitting on decimal point)

    text = re.sub(r'(\d+)\.(\d+)', r'\1 point \2', text)



    # Units (after numbers)

    text = re.sub(r'(\d+)\s*(?:GB|gigabytes?)\b', r'\1 gigabytes', text, flags=re.I)

    text = re.sub(r'(\d+)\s*(?:MB|megabytes?)\b', r'\1 megabytes', text, flags=re.I)

    text = re.sub(r'(\d+)\s*(?:KB|kilobytes?)\b', r'\1 kilobytes', text, flags=re.I)

    text = re.sub(r'(\d+)\s*ms\b', r'\1 milliseconds', text, flags=re.I)

    text = re.sub(r'(\d+)\s*%', r'\1 percent', text)

    text = re.sub(r'(\d+)\s*°C\b', r'\1 degrees Celsius', text)

    text = re.sub(r'(\d+)\s*°F\b', r'\1 degrees Fahrenheit', text)



    # Hardware & architecture terms

    text = re.sub(r'\bCPU\b', 'C P U', text)

    text = re.sub(r'\bGPU\b', 'G P U', text)

    text = re.sub(r'\bVRAM\b', 'V-RAM', text)

    text = re.sub(r'(?<![vV]-)\bRAM\b', 'ram', text)



    # Clean redundant whitespace

    text = re.sub(r'\s+', ' ', text).strip()

    return text





def cache_key_for(text, voice, speed, model_version="v1.0"):

    """Deterministic hash key for content-addressable audio caching."""

    cleaned = text.strip().lower()

    raw = f"{model_version}:{voice}:{float(speed):.2f}:{cleaned}".encode('utf-8')

    return hashlib.sha256(raw).hexdigest()[:24]





def wav_duration_ms(path):

    """Fast WAV header reader for exact audio duration in milliseconds."""

    try:

        with wave.open(str(path), 'rb') as w:

            frames = w.getnframes()

            rate = w.getframerate()

            return round((frames * 1000.0) / max(1, rate), 2)

    except Exception:

        return 0.0





def prune_voice_cache(cache_dir, max_bytes=MAX_CACHE_BYTES, target_bytes=TARGET_CACHE_BYTES, max_files=MAX_CACHE_FILES, protected=()):

    """Evict oldest audio cache entries to keep disk usage strictly bounded."""

    try:

        cache_dir = Path(cache_dir)

        if not cache_dir.is_dir():

            return

        files = []

        protected = {str(Path(p).resolve()) for p in protected}

        total_size = 0

        for entry in cache_dir.iterdir():

            if not entry.is_file():

                continue

            # Keep standalone voice previews permanently

            if entry.name.startswith("preview-") and not entry.name.startswith("preview-chunk-"):

                continue

            if entry.suffix.lower() == ".wav":

                try:

                    stat = entry.stat()

                    files.append((stat.st_mtime, stat.st_size, entry))

                    total_size += stat.st_size

                except OSError:

                    pass

        if total_size > max_bytes or len(files) > max_files:

            files.sort(key=lambda x: x[0])  # Oldest first

            remaining = len(files)

            for _, size, path in files:

                if str(path.resolve()) in protected:

                    continue

                try:

                    path.unlink(missing_ok=True)

                    total_size -= size

                    remaining -= 1

                    if total_size <= target_bytes and remaining <= int(max_files * 0.75):

                        break

                except OSError:

                    pass

    except Exception:

        pass





def speech_chunks(text, first_limit=120, limit=120):

    """Split text into natural spoken chunks:

    - First chunk is short (~50-120 chars) so playback starts rapidly (< 1-2s)

    - Prefers splitting on sentence boundaries (. ! ?) and clause boundaries (; : - — ,)

    - Groups sentences naturally up to limit for fluid speech

    - Strictly preserves every word in order: ' '.join(chunks) == ' '.join(text.split())

    - Bounded: len(chunks[0]) <= 140 and all(len(c) <= 320 for c in chunks)

    """

    raw_text = str(text or '').strip()

    if not raw_text:

        return []



    words = raw_text.split()

    if not words:

        return []



    # First, split into sentence units (. ! ? \n)

    sentences = [p.strip() for p in re.split(r'(?<=[.!?])\s+|\n+', raw_text) if p.strip()]

    if not sentences:

        sentences = [raw_text]



    # Sub-split long sentences at clauses (; : - — ,) if they exceed threshold

    units = []

    first_budget = first_limit

    for s in sentences:

        s_words = s.split()

        s_len = sum(len(w) for w in s_words) + len(s_words) - 1

        threshold = first_budget if first_budget > 0 else limit

        if s_len <= threshold:

            units.append(s)

            first_budget -= (s_len + 1)

        else:

            clauses = [c.strip() for c in re.split(r'(?<=[;:—–])\s+', s) if c.strip()]

            if len(clauses) > 1:

                units.extend(clauses)

            else:

                comma_clauses = [c.strip() for c in re.split(r'(?<=[,])\s+', s) if c.strip()]

                if len(comma_clauses) > 1:

                    units.extend(comma_clauses)

                else:

                    units.append(s)

            first_budget = 0



    chunks = []

    current_words = []

    current_len = 0

    cap = first_limit



    def flush():

        nonlocal current_words, current_len, cap

        if current_words:

            chunks.append(' '.join(current_words))

            current_words = []

            current_len = 0

            cap = limit



    for u in units:

        u_words = u.split()

        if not u_words:

            continue

        u_len = sum(len(w) for w in u_words) + len(u_words) - 1



        if not current_words:

            if u_len <= cap:

                current_words.extend(u_words)

                current_len = u_len

                if current_len >= 50:

                    flush()

            else:

                for w in u_words:

                    if current_words and current_len + 1 + len(w) > cap:

                        flush()

                    current_words.append(w)

                    current_len += (1 if current_len > 0 else 0) + len(w)

                    if current_len >= 50:

                        flush()

        else:

            if current_len + 1 + u_len <= cap:

                current_words.extend(u_words)

                current_len += 1 + u_len

                if current_len >= 50:

                    flush()

            else:

                flush()

                if u_len <= cap:

                    current_words.extend(u_words)

                    current_len = u_len

                    if current_len >= 50:

                        flush()

                else:

                    for w in u_words:

                        if current_words and current_len + 1 + len(w) > cap:

                            flush()

                        current_words.append(w)

                        current_len += (1 if current_len > 0 else 0) + len(w)

                        if current_len >= 50:

                            flush()



    flush()

    return chunks





def voice_catalog(asset_dir):

    # NPZ member names are available without importing/loading ONNX or NumPy.

    import zipfile

    folder = Path(asset_dir)

    if not (folder / 'model_quantized.onnx').is_file() or not (folder / 'voices-v1.0.bin').is_file():

        return []

    with zipfile.ZipFile(folder / 'voices-v1.0.bin') as archive:

        names = sorted(Path(n).stem for n in archive.namelist())

    return [{'id': n, 'name': n[3:].replace('_', ' ').title(),

             'accent': 'American English' if n[0] == 'a' else 'British English',

             'gender': 'Female' if n[1] == 'f' else 'Male',

             'label': n[3:].replace('_', ' ').title() + ' · ' + ('American' if n[0] == 'a' else 'British') + ' · ' + ('Female' if n[1] == 'f' else 'Male')}

            for n in names if re.fullmatch(r'[ab][fm]_[a-z_]+', n)]





def worker_main(asset_dir):

    """Dedicated offline speech worker process; reuses ONNX session across requests."""

    import socket

    def deny_network(*_args, **_kwargs):

        raise RuntimeError('Networking is disabled in the offline speech worker.')

    socket.socket.connect = deny_network

    socket.create_connection = deny_network



    # Windowed PyInstaller deliberately sets stdio to None; use inherited handles

    if sys.stdin is None or sys.stdout is None:

        import ctypes, msvcrt

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)

        kernel.GetStdHandle.restype = ctypes.c_void_p

        if sys.stdin is None:

            fd = msvcrt.open_osfhandle(kernel.GetStdHandle(-10), os.O_RDONLY)

            sys.stdin = os.fdopen(fd, 'r', encoding='utf-8')

        if sys.stdout is None:

            fd = msvcrt.open_osfhandle(kernel.GetStdHandle(-11), os.O_WRONLY)

            sys.stdout = os.fdopen(fd, 'w', encoding='utf-8', buffering=1)

        if sys.stderr is None:

            sys.stderr = open(Path(asset_dir) / 'speech-worker.log', 'a', encoding='utf-8')



    import numpy as np

    import onnxruntime as ort

    ort.disable_telemetry_events()

    from kokoro_onnx import Kokoro



    session = None

    model_load_ms = 0

    assets = Path(asset_dir).resolve()



    def ensure_session():

        nonlocal session, model_load_ms

        if session is None:

            load_started = time.perf_counter()

            options = ort.SessionOptions()

            options.intra_op_num_threads = 4

            options.inter_op_num_threads = 1

            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            options.enable_cpu_mem_arena = True

            session = Kokoro.from_session(

                ort.InferenceSession(str(assets / 'model_quantized.onnx'), sess_options=options, providers=['CPUExecutionProvider']),

                str(assets / 'voices-v1.0.bin')

            )

            model_load_ms = (time.perf_counter() - load_started) * 1000



    for line in sys.stdin:

        if not line.strip():

            continue

        request = {}

        try:

            request = json.loads(line)

            action = request.get('action')



            # Warmup action

            if action == 'warmup':

                ensure_session()

                result = {'id': request.get('id'), 'action': 'warmup', 'status': 'warm', 'tts_model_load_ms': round(model_load_ms, 2)}

                model_load_ms = 0

                print(json.dumps(result), flush=True)

                continue



            # Unload action under memory pressure

            if action == 'unload':

                del session

                session = None

                import gc

                gc.collect()

                result = {'id': request.get('id'), 'action': 'unload', 'status': 'unloaded'}

                print(json.dumps(result), flush=True)

                continue



            output = Path(request['path']).resolve()

            if not output.is_relative_to((assets / 'cache').resolve()):

                raise ValueError('Speech output must stay in local voice cache.')



            started = time.perf_counter()

            ensure_session()



            if request['voice'] not in session.get_voices():

                raise ValueError('This Kokoro voice is not installed.')



            lang = 'en-gb' if str(request['voice']).startswith('b') else 'en-us'

            phoneme_started=time.perf_counter()

            phonemes=session.tokenizer.phonemize(request['text'],lang)

            phonemization_ms=(time.perf_counter()-phoneme_started)*1000

            synthesis_started=time.perf_counter()

            audio, sr = session.create(phonemes, request['voice'], speed=float(request['speed']), lang=lang, is_phonemes=True)

            inference_ms=(time.perf_counter()-synthesis_started)*1000



            # Very short 120-sample linear ramp avoids boundary clicks

            audio = np.asarray(audio, dtype=np.float32)

            ramp = min(120, len(audio) // 2)

            if ramp > 0:

                audio[:ramp] *= np.linspace(0, 1, ramp)

                audio[-ramp:] *= np.linspace(1, 0, ramp)



            pcm = (np.clip(audio, -1, 1) * 32767).astype('<i2')

            output.parent.mkdir(parents=True, exist_ok=True)

            temporary = output.with_suffix('.partial')

            with wave.open(str(temporary), 'wb') as wav:

                wav.setnchannels(1)

                wav.setsampwidth(2)

                wav.setframerate(sr)

                wav.writeframes(pcm.tobytes())

            temporary.replace(output)



            result = {

                'id': request['id'],

                'index': request['index'],

                'path': str(output),

                'speed': request['speed'],

                'audio_ms': round(len(audio) * 1000.0 / sr, 2),

                'chunk_synthesis_ms': round((time.perf_counter() - started) * 1000.0, 2),

                'phonemization_ms':round(phonemization_ms,2),'inference_ms':round(inference_ms,2),

                'tts_model_load_ms': round(model_load_ms, 2),

            }

            model_load_ms = 0

        except Exception as exc:

            result = {'id': request.get('id'), 'error': str(exc)}

        print(json.dumps(result), flush=True)





def make_controller(parent, asset_dir, config, save_config):

    """Create Qt-based SpeechController with stream pipelining, caching, and session reuse."""

    from PySide6.QtCore import QObject, Signal, QProcess, QProcessEnvironment, QTimer, QUrl

    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput



    class SpeechController(QObject):

        changed = Signal(dict)



        def __init__(self):

            super().__init__(parent)

            self.assets = Path(asset_dir)

            self.catalog = voice_catalog(self.assets)

            self.cfg = config

            self.save_config = save_config

            self.provider = self.cfg.get('speech_engine', 'kokoro' if self.catalog else 'sapi')

            self.voice = self.cfg.get('kokoro_voice', 'af_heart')

            self.speed = float(self.cfg.get('speech_speed', 1))

            self.state = 'ready'

            self.message_id = ''

            self.text = ''

            self.position = 0

            self.error = ''

            self.sapi = None

            self.process = None

            self.buffer = b''

            self.ready = []

            self.current = None

            self.inflight = False

            self.chunks = []

            self.next_index = 0

            self.token = ''

            self.started = 0

            self.first_audio = False

            self.metrics = {}

            self.last_request = None

            self.paused = False

            self.preview = False



            # Media player setup

            self.player = QMediaPlayer(self)

            self.output = QAudioOutput(self)

            self.player.setAudioOutput(self.output)

            self.player.positionChanged.connect(self._position)

            self.player.mediaStatusChanged.connect(self._media_status)

            self.player.playbackStateChanged.connect(self._playback_state_changed)

            self.player.errorOccurred.connect(lambda *_: self._fail(self.player.errorString()))



            # Idle timer: 5 minutes before safely unloading warm worker

            self.idle = QTimer(self)

            self.idle.setSingleShot(True)

            self.idle.timeout.connect(self._release)

            self.pressure_timer=QTimer(self);self.pressure_timer.setInterval(2000)

            self.pressure_timer.timeout.connect(self._memory_pressure);self.pressure_timer.start()



            if self.provider == 'sapi':

                self._sapi()



        def _memory_pressure(self):

            if not self.process:return

            from integrated_runtime import physical_memory_status

            memory=physical_memory_status();available=memory.get('available')

            if available is not None and available<192*1024**2:

                if self.token:self._fail('Speech stopped because available memory is critically low. Retry after memory is available.')

                self._release()



        def _sapi(self):

            if self.sapi:

                return

            from PySide6.QtTextToSpeech import QTextToSpeech

            self.sapi = QTextToSpeech('sapi', self)

            self.sapi.stateChanged.connect(lambda state: self._sapi_state(state))

            self.sapi.sayingWord.connect(lambda word, utterance, start, length: self._sapi_position(start + length))

            if not self.sapi.availableVoices():

                raise RuntimeError('No offline Windows voice is available.')

            for voice in self.sapi.availableVoices():

                if voice.name() == self.cfg.get('speech_voice'):

                    self.sapi.setVoice(voice)

                    break



        def _sapi_state(self, state):

            self.state = state.name.lower()

            self.emit()



        def _sapi_position(self, position):

            self.position = position

            self.emit()



        def snapshot(self):

            catalog = self.catalog if self.provider == 'kokoro' else [{'id': v.name(), 'label': v.name()} for v in self.sapi.availableVoices()] if self.sapi else []

            return {

                'state': self.state,

                'messageId': self.message_id,

                'operationId':self.token,

                'engine': 'Kokoro-82M · local CPU' if self.provider == 'kokoro' else 'Windows SAPI (offline)',

                'provider': self.provider,

                'position': self.position,

                'length': len(self.text),

                'progressAvailable': True,

                'progressKind': 'audio_duration_estimate' if self.provider == 'kokoro' else 'word_events',

                'pauseAvailable': True,

                'voices': [v['id'] for v in catalog],

                'voiceOptions': catalog,

                'voice': self.voice if self.provider == 'kokoro' else self.sapi.voice().name() if self.sapi else '',

                'defaultVoice': self.cfg.get('kokoro_voice', 'af_heart') if self.provider == 'kokoro' else self.cfg.get('speech_voice', ''),

                'speed': self.speed,

                'rate': (self.speed - 1) / 1.25,

                'error': self.error,

                'metrics': dict(self.metrics),

                'kokoroInstalled': bool(self.catalog)

            }



        def emit(self):

            self.changed.emit(self.snapshot())



        def settings(self, values):

            if set(values) == {'speed'}:

                speed = float(values['speed'])

                if speed not in SPEEDS:

                    raise ValueError('Choose one of the supported speech speeds.')

                self.speed = speed

                self.cfg['speech_speed'] = speed

                self.save_config(self.cfg)

                if self.current:

                    self.player.setPlaybackRate(speed / float(self.current.get('speed', 1)))

                if self.provider == 'sapi' and self.sapi:

                    self.sapi.setRate(max(-.8, min(.8, (speed - 1) / 1.25)))

                self.emit()

                return



            self.stop()

            provider = values.get('provider', self.provider)

            if provider not in {'kokoro', 'sapi'}:

                raise ValueError('Unknown voice engine.')

            if provider == 'kokoro' and not self.catalog:

                raise ValueError('Install compatible Kokoro v1.0 assets first.')

            voice = values.get('voice', self.voice)

            if provider == 'kokoro' and voice not in {v['id'] for v in self.catalog}:

                raise ValueError('Selected Kokoro voice is not installed.')

            speed = float(values.get('speed', self.speed))

            if speed not in SPEEDS:

                raise ValueError('Choose one of the supported speech speeds.')



            self.provider = provider

            self.speed = speed

            if provider == 'kokoro':

                self.voice = voice

                self.cfg['kokoro_voice'] = voice

            else:

                self._sapi()

                if 'voice' in values:

                    match = next((v for v in self.sapi.availableVoices() if v.name() == voice), None)

                    if match is None:

                        raise ValueError('Selected Windows voice is unavailable.')

                    self.sapi.setVoice(match)

                    self.cfg['speech_voice'] = voice

            self.cfg.update(speech_engine=provider, speech_speed=speed)

            self.save_config(self.cfg)

            self.emit()



        def warmup(self):

            """Pre-warms the Kokoro ONNX worker process so synthesis is instantaneous on the first turn."""

            if self.provider == 'kokoro' and self.catalog:

                try:

                    self._start_worker()

                    if self.process and self.process.state() == QProcess.ProcessState.Running:

                        req = {'action': 'warmup', 'id': str(uuid.uuid4())}

                        self.process.write((json.dumps(req) + '\n').encode())

                except Exception:

                    pass



        def speak(self, message_id, text, preview=False):

            if not preview:

                self.provider = self.cfg.get('speech_engine', 'kokoro' if self.catalog else 'sapi')

                self.voice = self.cfg.get('kokoro_voice', 'af_heart')

            # Reset state but keep worker warm

            self.stop()

            self.idle.stop()

            self.message_id = str(message_id)

            self.text = text

            self.position = 0

            self.error = ''

            self.last_request = (self.message_id, text, preview)

            self.preview = preview



            clean_text = normalize_speech_text(text)

            if not clean_text.strip():

                raise ValueError('This response has no readable text.')



            if self.provider == 'sapi':

                self._sapi()

                self.sapi.setRate(max(-.8, min(.8, (self.speed - 1) / 1.25)))

                self.sapi.say(clean_text)

                self.emit()

                return



            if not self.catalog:

                raise ValueError('Kokoro assets are unavailable; choose Windows voice in Settings.')



            self.token = str(uuid.uuid4())

            self.state = 'loading'

            self.paused = False

            self.streaming = False

            self.streaming_finished = True

            self.stream_buffer = ''

            self.started = time.perf_counter()

            self.first_audio = False

            # Smart sentence chunking: chunk 0 is short for instant playback start

            self.chunks = speech_chunks(clean_text, first_limit=120, limit=120)

            self.next_index = 0

            self.metrics = {

                'chunk_queue_depth': 0,

                'audio_underruns': 0,

                'tts_model_load_ms': 0,

                'chunk_synthesis_ms': [],

                'chunks_cached': 0,

                'total_chunks': len(self.chunks),

                'first_chunk_chars': len(self.chunks[0]) if self.chunks else 0,

                'time_to_first_audio_ms': None

            }

            self.emit()

            QTimer.singleShot(0, self._request_next)



        def start_stream(self, message_id, voice=None, speed=None):

            """Begin a new streaming speech turn. Text chunks are pushed incrementally as LLM generates."""

            self.stop()

            self.idle.stop()

            self.message_id = str(message_id)

            self.text = ''

            self.position = 0

            self.error = ''

            self.streaming = True

            self.streaming_finished = False

            self.stream_buffer = ''

            self.last_request = None

            self.preview = False



            if voice:

                self.voice = voice

            else:

                self.voice = self.cfg.get('kokoro_voice', 'af_heart')

            if speed:

                self.speed = float(speed)

            else:

                self.speed = float(self.cfg.get('speech_speed', 1))



            self.provider = self.cfg.get('speech_engine', 'kokoro' if self.catalog else 'sapi')

            if not self.catalog and self.provider == 'kokoro':

                self.provider = 'sapi'

            if self.provider == 'sapi':

                self._sapi()



            self.token = str(uuid.uuid4())

            self.state = 'loading'

            self.paused = False

            self.started = time.perf_counter()

            self.first_audio = False

            self.chunks = []

            self.next_index = 0

            self.in_code_block = False

            self.code_announced = False

            self.metrics = {

                'chunk_queue_depth': 0,

                'audio_underruns': 0,

                'tts_model_load_ms': 0,

                'chunk_synthesis_ms': [],

                'chunks_cached': 0,

                'total_chunks': 0,

                'first_chunk_chars': 0,

                'time_to_first_audio_ms': None,

                'streaming': True

            }

            self.emit()



        def push_stream_chunk(self, delta_text):
            """Process incremental text chunks; extract completed sentences/clauses for instant synthesis."""
            if not self.token or not getattr(self, 'streaming', False):
                return
            delta = str(delta_text or '')
            self.stream_buffer += delta
            self.text += delta

            # Sentence boundaries (. ! ? \n) or clause boundaries (; : , -) for early speech
            target_cap = 32 if not self.chunks else 80
            while True:
                buf = self.stream_buffer
                if not buf:
                    break

                # 1. Suppress raw code blocks and announce natural transition
                if getattr(self, 'in_code_block', False):
                    code_end = buf.find('```')
                    if code_end != -1:
                        self.stream_buffer = buf[code_end + 3:]
                        self.in_code_block = False
                        continue
                    else:
                        if len(buf) > 2:
                            self.stream_buffer = buf[-2:]
                        break

                code_start = buf.find('```')
                if code_start != -1:
                    pre_code = buf[:code_start].strip()
                    self.stream_buffer = buf[code_start + 3:]
                    self.in_code_block = True
                    if pre_code:
                        clean = normalize_speech_text(pre_code)
                        clean = re.sub(r'[*_`#~]+', '', clean).strip()
                        if clean:
                            if not self.chunks:
                                self.metrics['first_chunk_chars'] = len(clean)
                            self.chunks.append(clean)
                            self.metrics['total_chunks'] = len(self.chunks)
                            self._request_next()
                    if not getattr(self, 'code_announced', False):
                        announcement = "I've written the code for you in the chat window."
                        self.code_announced = True
                        if not self.chunks:
                            self.metrics['first_chunk_chars'] = len(announcement)
                        self.chunks.append(announcement)
                        self.metrics['total_chunks'] = len(self.chunks)
                        self._request_next()
                    continue

                # 2. Extract completed sentences or clauses
                m = re.search(r'([.!?\n]+)(?:\s+|$)', buf)
                if not m and len(buf) >= target_cap:
                    m = re.search(r'([;:,\u2014\u2013-]+)(?:\s+|$)', buf)
                if m:
                    idx_m = m.end()
                    raw_chunk = buf[:idx_m].strip()
                    self.stream_buffer = buf[idx_m:]
                    if raw_chunk:
                        # Strip markdown tables, URLs, raw file paths
                        clean_chunk = re.sub(r'(?m)^\s*\|.*?\|\s*$', '', raw_chunk)
                        clean_chunk = re.sub(r'https?://\S+', '', clean_chunk)
                        clean_chunk = re.sub(r'\b[A-Za-z]:\\[^\s"\'<>]+', '', clean_chunk)
                        clean = normalize_speech_text(clean_chunk)
                        clean = re.sub(r'[*_`#~]+', '', clean).strip()
                        if clean:
                            if not self.chunks:
                                self.metrics['first_chunk_chars'] = len(clean)
                            self.chunks.append(clean)
                            self.metrics['total_chunks'] = len(self.chunks)
                            self._request_next()
                else:
                    break

        def finish_stream(self):
            """Finalize the streaming speech turn. Flush any remaining text in the buffer."""
            if not self.token or not getattr(self, 'streaming', False):
                return
            self.streaming_finished = True
            rem = self.stream_buffer.strip()
            self.stream_buffer = ''
            if rem and not getattr(self, 'in_code_block', False):
                clean_chunk = re.sub(r'```.*?```', '', rem, flags=re.S)
                clean_chunk = re.sub(r'(?m)^\s*\|.*?\|\s*$', '', clean_chunk)
                clean_chunk = re.sub(r'https?://\S+', '', clean_chunk)
                clean_chunk = re.sub(r'\b[A-Za-z]:\\[^\s"\'<>]+', '', clean_chunk)
                clean = normalize_speech_text(clean_chunk)
                clean = re.sub(r'[*_`#~]+', '', clean).strip()
                if clean:
                    if not self.chunks:
                        self.metrics['first_chunk_chars'] = len(clean)
                    self.chunks.append(clean)
                    self.metrics['total_chunks'] = len(self.chunks)
            if not self.chunks:
                self.stop()
            else:
                self._request_next()

        def _start_worker(self):

            if self.process and self.process.state() != QProcess.ProcessState.NotRunning:

                return

            self.process = QProcess(self)

            self.buffer = b''

            if getattr(sys, 'frozen', False):

                program = sys.executable

                arguments = ['--speech-worker', str(self.assets)]

            else:

                program = getattr(sys, '_base_executable', sys.executable)

                arguments = [str(Path(__file__).with_name('app.py')), '--speech-worker', str(self.assets)]

                environment = QProcessEnvironment.systemEnvironment()

                environment.insert('PYTHONPATH', os.pathsep.join(p for p in sys.path if p))

                self.process.setProcessEnvironment(environment)

            process = self.process

            process.readyReadStandardOutput.connect(lambda: self._read(process))

            process.errorOccurred.connect(lambda error: self._process_error(process))

            process.finished.connect(lambda code, *_: self._process_exit(process, code))

            process.setProgram(program)

            process.setArguments(arguments)

            process.start()



        def _request_next(self):

            if not self.token or self.inflight or self.next_index >= len(self.chunks):

                return

            # Keep up to 3 chunks in flight/ready to prevent audio underruns

            if len(self.ready) + (1 if self.current else 0) >= 3:

                return



            i = self.next_index

            self.next_index += 1

            text = self.chunks[i]



            # Content-addressable cache check

            if self.preview:

                cache_name = f'preview-{self.voice}-{self.speed}.wav'

            else:

                key = cache_key_for(text, self.voice, self.speed)

                cache_name = f'preview-chunk-{key}.wav'



            cache_dir = (self.assets / 'cache').resolve()

            cache_dir.mkdir(parents=True, exist_ok=True)

            path = cache_dir / cache_name



            # Check if chunk is already cached

            if path.is_file() and path.stat().st_size > 44:

                duration = wav_duration_ms(path)

                if duration > 50:

                    self.metrics['chunks_cached'] += 1

                    chunk_res = {

                        'id': self.token,

                        'index': i,

                        'path': str(path),

                        'speed': self.speed,

                        'audio_ms': duration,

                        'chunk_synthesis_ms': 0.0,

                        'tts_model_load_ms': 0.0,

                        'cached': True

                    }

                    self._chunk(chunk_res)

                    return



            # Request synthesis from warm worker

            request = {

                'id': self.token,

                'index': i,

                'text': text,

                'voice': self.voice,

                'speed': self.speed,

                'path': str(path)

            }

            self.inflight = True

            try:self._start_worker()

            except Exception as exc:self._fail(str(exc));return

            self.process.write((json.dumps(request) + '\n').encode())

            self.emit()



        def _read(self, process):

            if process is not self.process:

                return

            self.buffer += bytes(process.readAllStandardOutput())

            while b'\n' in self.buffer:

                line, self.buffer = self.buffer.split(b'\n', 1)

                try:

                    result = json.loads(line)

                except ValueError:

                    continue

                if result.get('action') == 'warmup':

                    continue

                if result.get('id') != self.token:

                    # Ignore stale chunks from canceled or previous tokens

                    continue

                self.inflight = False

                if result.get('error'):

                    self._fail(result['error'])

                    return

                self._chunk(result)



        def _chunk(self, result):

            if result.get('id') != self.token:

                return

            self.metrics['tts_model_load_ms'] += result.get('tts_model_load_ms', 0)

            self.metrics['chunk_synthesis_ms'].append(result.get('chunk_synthesis_ms', 0))

            self.metrics.setdefault('phonemization_ms',[]).append(result.get('phonemization_ms',0))

            self.metrics.setdefault('inference_ms',[]).append(result.get('inference_ms',0))

            self.ready.append(result)

            self.metrics['chunk_queue_depth'] = len(self.ready)



            # Evict old cache entries if directory exceeds bound

            prune_voice_cache(self.assets / 'cache', protected=[c['path'] for c in self.ready] + ([self.current['path']] if self.current else []))



            can_start = bool(self.ready)

            if not self.current and not self.paused and can_start:

                self._play_next()

            self._request_next()



        def _play_next(self):

            if not self.ready:

                return

            self.current = self.ready.pop(0)

            self.state = 'speaking'

            self.player.setSource(QUrl.fromLocalFile(self.current['path']))

            self.player.setPlaybackRate(self.speed / float(self.current.get('speed', self.speed)))

            self.player.play()

            self.emit()



        def _playback_state_changed(self, state):

            if state == QMediaPlayer.PlaybackState.PlayingState and not self.first_audio and self.started > 0:

                self.first_audio = True

                self.metrics['time_to_first_audio_ms'] = round((time.perf_counter() - self.started) * 1000.0, 2)

                self.emit()



        def _position(self, ms):

            if not self.current or not self.token:

                return

            if ms > 0 and not self.first_audio:

                self.first_audio = True

                self.metrics['time_to_first_audio_ms'] = round((time.perf_counter() - self.started) * 1000.0, 2)

            i = self.current['index']

            fraction = min(1.0, ms / max(1.0, self.current['audio_ms']))

            self.position = min(len(self.text), sum(len(x) + 1 for x in self.chunks[:i]) + int(len(self.chunks[i]) * fraction))

            self.emit()



        def _media_status(self, status):

            if status != QMediaPlayer.MediaStatus.EndOfMedia or not self.current:

                return

            self.current = None

            if self.ready and not self.paused:

                self._play_next()

            elif self.next_index >= len(self.chunks) and not self.inflight and (not getattr(self, 'streaming', False) or getattr(self, 'streaming_finished', False)):

                self.position = len(self.text)

                self.state = 'ready'

                self.streaming = False

                self.idle.start(300000)  # Keep worker warm for 5 minutes of idle

                self.emit()

            elif not self.paused:

                self.state = 'loading'

                self.metrics['audio_underruns'] += 1

                self.emit()

            self._request_next()



        def control(self, action):

            if action == 'stop':

                self.stop()

            elif action == 'pause':

                self.paused = True

                self.player.pause()

                self.state = 'paused'

                if self.provider == 'sapi':

                    self.sapi.pause()

                self.emit()

            elif action == 'resume':

                self.paused = False

                if self.provider == 'sapi':

                    self.sapi.resume()

                elif self.current:

                    self.player.play()

                    self.state = 'speaking'

                else:

                    self._play_next()

                self.emit()

            elif action == 'replay' and self.last_request:

                self.speak(*self.last_request)

            else:

                raise ValueError('Unknown playback control.')



        def stop(self):

            """Immediately halt audio output and drop queued chunks while keeping worker warm in RAM."""

            self.player.stop()

            self.player.setSource(QUrl())

            if self.sapi:

                self.sapi.stop()

            self.ready = []

            self.current = None

            # Invalidating token drops any in-flight synthesis responses

            self.token = ''

            self.inflight = False

            self.paused = False

            self.streaming = False

            self.streaming_finished = False

            self.stream_buffer = ''

            self.state = 'ready'

            # Start 5-minute warm idle timer

            self.idle.start(300000)

            self.emit()



        def release_memory(self):

            """Unload Kokoro session under memory pressure."""

            self.stop()

            self.idle.stop()

            self._release()



        def _release(self):

            """Terminate worker process to free RAM."""

            if self.process:

                process = self.process

                self.process = None

                process.finished.connect(process.deleteLater)

                process.kill()



        def _process_error(self, process):

            if process is self.process and self.token:

                self._fail(process.errorString())



        def _process_exit(self, process, code):

            if process is self.process and self.token and self.inflight:

                self._fail('Local voice worker exited: ' + bytes(process.readAllStandardError()).decode('utf-8', 'replace')[-600:])



        def _fail(self, error):

            self.stop()

            self.error = str(error)

            self.state = 'error'

            self.emit()



        def shutdown(self):

            """Clean exit when closing Offline GPT window."""

            self.stop()

            self.idle.stop()

            self._release()



    return SpeechController()

