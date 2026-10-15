import queue
import threading
import time

from streamtv import mirror


class FakeStdin:
    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data += b

    def close(self):
        pass


class FakeProc:
    def __init__(self):
        self.stdin = FakeStdin()

    def poll(self):
        return None


def test_writer_fills_silence_in_realtime():
    proc, stop, q = FakeProc(), threading.Event(), queue.Queue()
    q.put(b"\x01\x00" * 2 * 480)  # 10 ms echte Daten
    t = threading.Thread(target=mirror.writer_loop, args=(proc, stop, q))
    t.start()
    time.sleep(0.5)
    stop.set()
    t.join(2)
    seconds = len(proc.stdin.data) / (mirror.SAMPLE_RATE * mirror.BYTES_PER_FRAME)
    assert 0.35 < seconds < 0.6
    assert len(proc.stdin.data) % mirror.BYTES_PER_FRAME == 0
    assert proc.stdin.data[:4] == b"\x01\x00\x01\x00"  # echte Daten zuerst
