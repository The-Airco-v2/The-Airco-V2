from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

from .codec import PersonEmbeddingCodec


class PersistentEmbeddingGallery:
    def __init__(self, storage_dir: str, max_viewpoints: int = 25):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_viewpoints = max_viewpoints
        self.use_protobuf = True
        self._write_queue: "queue.Queue[tuple[int, bytes]]" = queue.Queue(maxsize=512)
        self._stop_event = threading.Event()
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def _path_for_id(self, global_id: int) -> Path:
        return self.storage_dir / f"identity_{global_id}.pb"

    def _write_identity_file(self, global_id: int, payload: bytes) -> None:
        with open(self._path_for_id(global_id), "wb") as f:
            f.write(payload)

    def _writer_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                global_id, payload = self._write_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._write_identity_file(global_id, payload)
            except Exception as exc:
                print(f"[WARN] Failed to persist identity {global_id}: {exc}")
            finally:
                self._write_queue.task_done()

    def save_identity(self, identity) -> None:
        try:
            payload = PersonEmbeddingCodec.encode_identity(identity)
            try:
                self._write_queue.put_nowait((identity.global_id, payload))
            except queue.Full:
                self._write_identity_file(identity.global_id, payload)
        except Exception as exc:
            print(f"[WARN] Failed to persist identity {identity.global_id}: {exc}")

    def flush(self) -> None:
        self._write_queue.join()

    def load_identity(self, global_id: int):
        pb_path = self._path_for_id(global_id)
        try:
            if not pb_path.exists():
                return None
            with open(pb_path, "rb") as f:
                raw = f.read()
            return PersonEmbeddingCodec.decode_identity(raw, max_viewpoints=self.max_viewpoints)
        except Exception as exc:
            print(f"[WARN] Failed to load identity {global_id}: {exc}")
        return None

    def load_all_identities(self):
        identities = {}
        for pb_file in self.storage_dir.glob("identity_*.pb"):
            try:
                gid = int(pb_file.stem.split("_")[-1])
                data = self.load_identity(gid)
                if data:
                    identities[gid] = data
            except Exception as exc:
                print(f"[WARN] Failed to read {pb_file.name}: {exc}")
        return identities

    def delete_identity(self, global_id: int) -> bool:
        path = self._path_for_id(global_id)
        if path.exists():
            os.remove(path)
            return True
        return False

    def stop(self) -> None:
        self._stop_event.set()
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=1.0)

