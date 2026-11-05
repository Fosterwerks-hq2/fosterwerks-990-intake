"""Read single files out of a remote zip with HTTP range requests.

The IRS publishes 990 e-file XML in batch zips of 100 MB and up. The server honours `Range`
(checked 2026-11-05), so `zipfile` can read the central directory and one member at a time
through a seekable file object that fetches only the bytes it needs.
"""
import io
import urllib.request
import zipfile

import zipfile_deflate64  # noqa: F401  (patches zipfile: some IRS batches use Deflate64)

USER_AGENT = "fosterwerks-990-intake/0.1 (public IRS 990 research)"
BLOCK = 256 * 1024


class HTTPRangeFile(io.RawIOBase):
    """Seekable, read-only view of a URL. Caches fixed-size blocks so zipfile's many small
    reads of the central directory turn into a few requests."""

    def __init__(self, url: str):
        self.url = url
        self.pos = 0
        self.blocks: dict[int, bytes] = {}
        self.requests = 0
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
            if r.headers.get("Accept-Ranges", "").lower() != "bytes":
                raise IOError(f"{url} does not accept range requests")

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _fetch(self, start: int, end: int) -> bytes:
        """Inclusive byte range [start, end]."""
        req = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT,
                                                        "Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status != 206:
                raise IOError(f"expected 206 Partial Content, got {r.status}")
            self.requests += 1
            return r.read()

    def _block(self, n: int) -> bytes:
        if n not in self.blocks:
            start = n * BLOCK
            self.blocks[n] = self._fetch(start, min(start + BLOCK, self.size) - 1)
        return self.blocks[n]

    def read(self, size=-1) -> bytes:
        if size is None or size < 0:
            size = self.size - self.pos
        size = min(size, self.size - self.pos)
        if size <= 0:
            return b""
        if size > 4 * BLOCK:  # big member body: one direct request, not cached
            data = self._fetch(self.pos, self.pos + size - 1)
        else:
            out, p, end = [], self.pos, self.pos + size
            while p < end:
                n, off = divmod(p, BLOCK)
                chunk = self._block(n)[off:off + (end - p)]
                out.append(chunk)
                p += len(chunk)
            data = b"".join(out)
        self.pos += len(data)
        return data

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


class RemoteZip:
    """`with RemoteZip(url) as z: z.read(name)`; keeps the central directory between reads."""

    def __init__(self, url: str):
        self.raw = HTTPRangeFile(url)
        self.zip = zipfile.ZipFile(self.raw)
        self.names = {n.rsplit("/", 1)[-1]: n for n in self.zip.namelist()}

    def read(self, filename: str) -> bytes:
        return self.zip.read(self.names[filename])

    def __enter__(self): return self
    def __exit__(self, *exc): self.zip.close()
