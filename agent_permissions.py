"""One-use, argument-bound approval tickets. Models cannot grant permissions."""
from __future__ import annotations
import hashlib
import hmac
import json
import secrets
import threading
import time


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def argument_digest(tool, arguments):
    return hashlib.sha256(canonical({'tool': tool, 'arguments': arguments}).encode()).hexdigest()


class ApprovalBroker:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.condition = threading.Condition()
        self.pending = {}

    def request(self, task, tool, arguments, preview, notify, cancel, timeout=90):
        # Freeze before presenting the request; no mutable caller-owned object survives.
        frozen = json.loads(canonical(arguments))
        digest = argument_digest(tool, frozen)
        rid = secrets.token_hex(16)
        deadline = self.clock() + min(90, max(0.01, timeout))
        with self.condition:
            self.pending[rid] = {'task': task, 'digest': digest, 'decision': None, 'deadline': deadline}
        try:
            notify({'requestId': rid, 'digest': digest, 'tool': tool,
                    'arguments': frozen, 'preview': preview, 'expiresInSeconds': max(0, deadline-self.clock())})
            with self.condition:
                while not cancel.is_set() and self.clock() < deadline:
                    entry = self.pending.get(rid)
                    if entry is None or entry['decision'] is False:
                        raise PermissionError('The proposed operation was denied or cancelled.')
                    if entry['decision'] is True:
                        return frozen
                    self.condition.wait(min(0.1, max(0, deadline-self.clock())))
            raise PermissionError('Approval expired or task cancelled; no operation was authorized.')
        finally:
            with self.condition:
                self.pending.pop(rid, None)

    def decide(self, task, rid, digest, allow):
        if type(allow) is not bool:
            return False
        with self.condition:
            entry = self.pending.get(rid)
            if not entry or entry['task'] != task or entry['digest'] != digest or entry['decision'] is not None or self.clock() >= entry['deadline']:
                return False
            entry['decision'] = allow
            self.condition.notify_all()
            return True

    def cancel(self, task):
        with self.condition:
            for entry in self.pending.values():
                if entry['task'] == task:
                    entry['decision'] = False
            self.condition.notify_all()


class GrantAuthority:
    """Per-process connection secret; grants never enter the model conversation."""
    def __init__(self, key, clock=time.time):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError('A 256-bit private connection key is required.')
        self.key, self.clock = key, clock
        self.used = set()
        self.lock = threading.Lock()

    def issue(self, tool, arguments):
        ticket = {'id': secrets.token_hex(16), 'expires': self.clock()+60,
                  'digest': argument_digest(tool, arguments)}
        ticket['signature'] = hmac.new(self.key, canonical(ticket).encode(), hashlib.sha256).hexdigest()
        return canonical(ticket)

    def consume(self, raw, tool, arguments):
        try:
            ticket = json.loads(raw)
            signature = ticket.pop('signature')
            expected = hmac.new(self.key, canonical(ticket).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected) or ticket['digest'] != argument_digest(tool, arguments):
                raise ValueError('Argument identity mismatch')
            now = self.clock()
            if not now < ticket['expires'] <= now+61:
                raise ValueError('Expired grant')
            with self.lock:
                if ticket['id'] in self.used or len(self.used) >= 64:
                    raise ValueError('Replayed or exhausted connection grant')
                self.used.add(ticket['id'])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise PermissionError('A valid, fresh approval for these exact arguments is required.') from exc
