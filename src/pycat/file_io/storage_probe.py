"""
PyCAT storage speed probe
=========================
Detects slow file I/O at load time (network shares, slow external drives, cloud-
sync placeholders) so PyCAT can warn the user before a long, silent load — a
common source of "is it frozen?" confusion as more people try the tool.

Design (agreed with the maintainer)
-----------------------------------
- **Measure, don't guess.** The reliable signal for "will this load slowly" is
  *measured throughput* (a quick timed read of the first few MB), NOT the storage
  bus type. A USB 3.x SSD is fast; an old internal spinning disk can be slow; a
  fast network mount need not warn. Bus-type classification alone produces false
  warnings, so throughput leads and path-type is only a hint / for network+cloud
  detection.
- **Network / cloud detection is the path-type job.** Detect UNC / mapped network
  drives (so we can say "network location") and cloud-sync ONLINE-ONLY placeholders
  (OneDrive/Dropbox/Drive File Stream mark these with a recall-on-access
  attribute, meaning the read will trigger a download).
- **No new dependencies** — stdlib only (os, time, ctypes on Windows).

The caller uses ``probe_path()`` and, if ``verdict.slow`` (or ``needs_download``),
shows a warning whose lifetime is the load's lifetime (so it can't flash-and-clear
before the user reads it).
"""

from __future__ import annotations
import os
import time
import platform

# Throughput below this (MB/s) is treated as "slow" and worth warning about.
# ~30 MB/s comfortably clears fast local SSDs and USB 3.x, and flags genuinely
# slow paths (busy network shares, USB2 sticks, spinning disks under load).
SLOW_MBPS_THRESHOLD = 30.0

# How much to read for the timed probe. Big enough to get past OS read-ahead
# caching of tiny files, small enough to stay quick even on slow media.
PROBE_BYTES = 8 * 1024 * 1024  # 8 MB


class StorageVerdict:
    """Result of probing a file's storage. Attributes:

    location   'local' | 'network' | 'removable' | 'cloud_placeholder' | 'unknown'
    throughput_mbps  measured MB/s of the probe read (None if not measured)
    slow       True if throughput below threshold OR a cloud placeholder
    needs_download  True if the file is a cloud online-only placeholder (read
               will trigger a download)
    size_bytes file size
    message    a human-readable one-liner for the UI (empty if nothing to warn)
    """
    __slots__ = ('location', 'throughput_mbps', 'slow', 'needs_download',
                 'size_bytes', 'message')

    def __init__(self, location='unknown', throughput_mbps=None, slow=False,
                 needs_download=False, size_bytes=0, message=''):
        self.location = location
        self.throughput_mbps = throughput_mbps
        self.slow = slow
        self.needs_download = needs_download
        self.size_bytes = size_bytes
        self.message = message

    def __repr__(self):
        tp = (f"{self.throughput_mbps:.0f} MB/s"
              if self.throughput_mbps is not None else "n/a")
        return (f"<StorageVerdict {self.location} {tp} slow={self.slow} "
                f"needs_download={self.needs_download}>")


# ── Path-type classification (hint + network/cloud detection) ────────────────
def _windows_drive_type(path):
    """Return one of 'local'/'network'/'removable'/'unknown' from the Windows
    drive type, or 'network' for a UNC path. Windows-only; other OSes return
    'unknown' (throughput still governs the warning)."""
    try:
        import ctypes
        # UNC path -> network share.
        norm = os.path.normpath(path)
        if norm.startswith('\\\\'):
            return 'network'
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        if not drive:
            return 'unknown'
        root = drive + '\\'
        DRIVE = {2: 'removable', 3: 'local', 4: 'network', 5: 'unknown',
                 6: 'local'}  # 6=RAMDISK -> treat as local (fast)
        t = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        return DRIVE.get(t, 'unknown')
    except Exception:
        return 'unknown'


def _is_cloud_placeholder(path):
    """True if the file is a cloud-sync ONLINE-ONLY placeholder on Windows
    (OneDrive/Dropbox/Drive File Stream), i.e. reading it triggers a download.
    Detected via the recall-on-data-access / offline file attributes."""
    if platform.system() != 'Windows':
        return False
    try:
        import ctypes
        FILE_ATTRIBUTE_OFFLINE = 0x1000
        FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
        FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
        attrs = ctypes.windll.kernel32.GetFileAttributesW(
            ctypes.c_wchar_p(os.path.abspath(path)))
        if attrs == 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
            return False
        return bool(attrs & (FILE_ATTRIBUTE_OFFLINE
                             | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
                             | FILE_ATTRIBUTE_RECALL_ON_OPEN))
    except Exception:
        return False


def classify_location(path):
    """Classify where a file lives (hint only; throughput governs 'slow')."""
    if _is_cloud_placeholder(path):
        return 'cloud_placeholder'
    if platform.system() == 'Windows':
        return _windows_drive_type(path)
    # POSIX: a light heuristic — network mounts often live under /mnt, /media,
    # /Volumes, or are NFS/SMB. We don't over-claim here; throughput decides.
    return 'unknown'


# ── Throughput probe (the reliable "is it slow" signal) ──────────────────────
def measure_throughput(path, nbytes=PROBE_BYTES):
    """Time a sequential read of up to ``nbytes`` from the start of the file and
    return the SUSTAINED throughput in MB/s (or None if unmeasurable).

    Measures steady-state rate, not first-read latency. The first read chunk
    carries file-open + seek + cache-miss cost that has nothing to do with the
    medium's sustained speed, so timing from byte 0 can flag a fast-but-cold drive
    as "slow" (a false warning). We therefore read one warm-up chunk first
    (untimed), then time the remaining reads — that reflects how fast the bulk of
    a large microscopy file will actually stream. For a cloud placeholder this
    read is what triggers the download, so callers should probe off the UI thread.
    """
    try:
        size = os.path.getsize(path)
        to_read = min(nbytes, size)
        if to_read <= 0:
            return None
        chunk_sz = 1024 * 1024
        with open(path, 'rb') as f:
            # Untimed warm-up chunk: absorbs open/seek/first-access latency.
            warm = f.read(min(chunk_sz, to_read))
            if not warm:
                return None
            remaining = to_read - len(warm)
            if remaining <= 0:
                # File smaller than one chunk — fall back to a whole-read timing.
                t0 = time.perf_counter()
                with open(path, 'rb') as f2:
                    got = len(f2.read(to_read))
                dt = time.perf_counter() - t0
                return ((got / (1024 * 1024)) / dt) if dt > 0 and got > 0 else None
            t0 = time.perf_counter()
            got = 0
            while got < remaining:
                chunk = f.read(min(chunk_sz, remaining - got))
                if not chunk:
                    break
                got += len(chunk)
            dt = time.perf_counter() - t0
        if dt <= 0 or got <= 0:
            return None
        return (got / (1024 * 1024)) / dt
    except Exception:
        return None


def probe_path(path, measure=True):
    """Probe a file's storage and return a StorageVerdict with a UI message.

    measure=False skips the timed read (path-type only) — useful when the caller
    wants an instant hint without touching the disk (e.g. a cloud placeholder we
    don't want to accidentally trigger downloading during a hover).
    """
    if not path or not os.path.exists(path):
        return StorageVerdict(message='')

    location = classify_location(path)
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0
    needs_download = (location == 'cloud_placeholder')

    throughput = None
    # Don't run the timed read on a cloud placeholder unless explicitly asked —
    # the read itself would force the download we're trying to warn about.
    if measure and not needs_download:
        throughput = measure_throughput(path)

    slow = needs_download or (throughput is not None
                              and throughput < SLOW_MBPS_THRESHOLD)

    message = _build_message(location, throughput, size, needs_download)
    return StorageVerdict(location=location, throughput_mbps=throughput,
                          slow=slow, needs_download=needs_download,
                          size_bytes=size, message=message)


def _fmt_size(nbytes):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if nbytes < 1024 or unit == 'TB':
            return f"{nbytes:.0f} {unit}" if unit in ('B', 'KB') else f"{nbytes:.1f} {unit}"
        nbytes /= 1024.0
    return f"{nbytes:.1f} TB"


def _build_message(location, throughput, size, needs_download):
    """Human-readable one-liner for the UI, or '' when nothing is worth saying."""
    size_str = _fmt_size(size) if size else ''
    if needs_download:
        return (f"This file{(' (' + size_str + ')') if size_str else ''} is a "
                f"cloud online-only file — opening it will download it first, "
                f"which may take a while. Consider making it available offline, "
                f"or copy it to a local drive.")
    if throughput is None:
        # couldn't measure; only warn on a clear network/removable hint
        if location == 'network':
            return ("This file is on a network location — loading may be slow "
                    "depending on your connection.")
        return ''
    if throughput >= SLOW_MBPS_THRESHOLD:
        return ''  # fast enough — stay silent (avoid the flash-warning problem)
    where = {'network': 'a network location',
             'removable': 'a removable/external drive',
             'local': 'this drive',
             'unknown': 'this location'}.get(location, 'this location')
    return (f"This file{(' (' + size_str + ')') if size_str else ''} is on "
            f"{where} and reads slowly (~{throughput:.0f} MB/s) — loading may "
            f"take a while. Copying it to a fast local drive first can help if "
            f"you'll work with it repeatedly.")
