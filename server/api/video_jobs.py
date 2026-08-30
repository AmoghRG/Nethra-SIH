"""Video ingestion jobs — upload a clip, pin it to a location, run the pipeline.

Owner E's addition, living in D's directory because ingest has to sit beside
the event store. Announced in server/PATCHES_TO_OWNER_D.md.

What this module owns:

* the upload itself, streamed to disk so a 170 MB clip never sits in RAM;
* a job record per upload, persisted in the same SQLite file as the events so
  a server restart does not lose the site list;
* a subprocess around ``ingest_video.py`` — the pipeline is run as a child
  process with cwd at the repo root, because ``ingest_video`` loads
  ``edge/config.yaml`` by relative path and imports torch. Neither belongs
  inside a web worker thread;
* stamping the operator-supplied location onto every event the run produced.

**On that last point, deliberately.** The edge derives an event's location
from ``calibration.location``, and a clip uploaded through this form has no
calibration — ``create_auto_calibration`` invents a road plane and carries a
placeholder coordinate with it. So the location on these events is the one the
operator typed, and nothing about it is measured. Distances and speeds within
the frame are still the pipeline's own metric output; the map coordinate is a
site marker, not a fix on the vehicles. Say that out loud rather than letting a
pin imply GPS.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
VIDEO_DIR = BASE_DIR / "data" / "videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".m4v"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

# Frame budgets offered in the dashboard. 0 means the whole clip.
QUALITY_PRESETS = {
    "quick": {"max_frames": 600, "imgsz": 640, "conf": 0.25, "model": "yolov8n.pt"},
    "standard": {"max_frames": 2400, "imgsz": 640, "conf": 0.25, "model": "yolov8m.pt"},
    "full": {"max_frames": 0, "imgsz": 640, "conf": 0.25, "model": "yolov8m.pt"},
}

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_jobs_table(db_path: Path) -> None:
    conn = _connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS video_jobs (
            job_id TEXT PRIMARY KEY,
            site_name TEXT,
            lat REAL,
            lon REAL,
            filename TEXT,
            video_path TEXT,
            quality TEXT,
            status TEXT,
            stage TEXT,
            message TEXT,
            created_at TEXT,
            finished_at TEXT,
            events_total INTEGER DEFAULT 0,
            events_severe INTEGER DEFAULT 0,
            frames INTEGER DEFAULT 0,
            annotated_url TEXT,
            summary TEXT
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON video_jobs(status);")
    # A job that was mid-flight when the server died is not running any more.
    conn.execute(
        "UPDATE video_jobs SET status=?, message=? WHERE status IN (?, ?)",
        (STATUS_ERROR, "Interrupted by a server restart. Re-upload to retry.",
         STATUS_RUNNING, STATUS_QUEUED),
    )
    conn.commit()
    conn.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    if d.get("summary"):
        try:
            d["summary"] = json.loads(d["summary"])
        except (ValueError, TypeError):
            d["summary"] = None
    d["location"] = [d.pop("lat", None), d.pop("lon", None)]
    return d


def list_jobs(db_path: Path) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM video_jobs ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_job(db_path: Path, job_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM video_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def _update(db_path: Path, job_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock:
        conn = _connect(db_path)
        conn.execute(
            f"UPDATE video_jobs SET {cols} WHERE job_id = ?",
            (*fields.values(), job_id),
        )
        conn.commit()
        conn.close()


def delete_job(db_path: Path, job_id: str, drop_events: bool = True) -> bool:
    """Remove a job, its files, and optionally the events it produced."""
    job = get_job(db_path, job_id)
    if not job:
        return False
    with _lock:
        conn = _connect(db_path)
        conn.execute("DELETE FROM video_jobs WHERE job_id = ?", (job_id,))
        if drop_events:
            lat, lon = job["location"]
            if lat is not None and lon is not None:
                conn.execute(
                    "DELETE FROM events WHERE ABS(lat - ?) < 1e-6 AND ABS(lon - ?) < 1e-6",
                    (lat, lon),
                )
        conn.commit()
        conn.close()
    folder = VIDEO_DIR / job_id
    if folder.exists():
        for path in sorted(folder.rglob("*"), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except OSError:
                pass
        try:
            folder.rmdir()
        except OSError:
            pass
    return True


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------

def new_job_id() -> str:
    return "vid_" + uuid.uuid4().hex[:10]


def job_dir(job_id: str) -> Path:
    d = VIDEO_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_job(
    db_path: Path,
    *,
    job_id: str,
    site_name: str,
    lat: float,
    lon: float,
    filename: str,
    video_path: Path,
    quality: str,
) -> Dict[str, Any]:
    with _lock:
        conn = _connect(db_path)
        conn.execute(
            """
            INSERT INTO video_jobs (
                job_id, site_name, lat, lon, filename, video_path, quality,
                status, stage, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, site_name, float(lat), float(lon), filename,
                str(video_path), quality, STATUS_QUEUED, "queued",
                "Waiting for a pipeline slot.", datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        conn.close()
    return get_job(db_path, job_id)


# ---------------------------------------------------------------------------
# the run itself
# ---------------------------------------------------------------------------

_STAGE_LABELS = {
    "STAGE 1": ("detect", "Detecting and tracking vehicles (M2)"),
    "STAGE 2": ("project", "Projecting tracks to ground metres (M1)"),
    "STAGE 3": ("conflicts", "Computing TTC and PET conflicts (M3)"),
    "STAGE 4": ("norms", "Learning road norms (M5)"),
    "STAGE 5": ("overlay", "Rendering the annotated clip"),
}


def _pipeline_command(video_path: Path, out_dir: Path, preset: Dict[str, Any]) -> List[str]:
    cmd = [
        sys.executable, "-u", "ingest_video.py",
        "--video", str(video_path),
        "--outdir", str(out_dir),
        "--model", preset["model"],
        "--imgsz", str(preset["imgsz"]),
        "--conf", str(preset["conf"]),
    ]
    if preset["max_frames"]:
        cmd += ["--max-frames", str(preset["max_frames"])]
    return cmd


# ---------------------------------------------------------------------------
# making the annotated render playable in a browser
# ---------------------------------------------------------------------------

#: Longest we will spend re-encoding one clip before giving up on it.
ENCODE_TIMEOUT_S = 1800

#: Anything wider than this is downscaled for playback. The pipeline renders at
#: the source resolution, which for a 1080p+ clip is a file no browser wants to
#: stream over a laptop's loopback during a demo.
WEB_MAX_WIDTH = 1280


def find_ffmpeg() -> Optional[str]:
    """ffmpeg on PATH, else the one imageio-ffmpeg bundles, else None."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def make_web_playable(video_path: Path) -> tuple[bool, str]:
    """Re-encode the annotated render to H.264 in place.

    OpenCV's ``VideoWriter`` writes MPEG-4 Part 2 with the ``mp4v`` tag, because
    that is the only codec it can rely on having. It is a valid .mp4 and no
    browser will play it - Chrome, Edge and Firefox all decode H.264 and not
    MPEG-4 Part 2, so the ``<video>`` element fails with a format/MIME error.
    Nothing upstream is wrong; the container just carries a codec the web does
    not take.

    The re-encode replaces the file rather than writing a second one, so the
    URL already stored on the job and on every event stays correct.

    Returns (ok, note). A failure is never fatal - the original file is left
    exactly as it was and the note explains what the operator will see.
    """
    exe = find_ffmpeg()
    if not exe:
        return False, ("Annotated clip is MPEG-4 Part 2 (OpenCV's only reliable "
                       "codec) and browsers cannot play it. Install ffmpeg, or "
                       "pip install imageio-ffmpeg, and re-run to get H.264.")

    tmp = video_path.with_suffix(".h264.mp4")
    cmd = [
        exe, "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",                       # Safari and older Chrome
        "-vf", f"scale='min({WEB_MAX_WIDTH},iw)':-2",  # -2 keeps it even
        "-movflags", "+faststart",                   # play before it fully loads
        "-an",                                       # the render has no audio
        str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=ENCODE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, "Re-encoding the annotated clip timed out; left as MPEG-4 Part 2."
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not re-encode the annotated clip: {exc}"

    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        err = (proc.stderr or "").strip().splitlines()
        return False, "ffmpeg failed on the annotated clip: " + (err[-1] if err else "no output")

    try:
        tmp.replace(video_path)
    except OSError as exc:
        return False, f"Re-encoded clip written but could not replace the original: {exc}"
    return True, ""


def run_job(
    db_path: Path,
    job_id: str,
    ingest_events: Callable[[List[Dict[str, Any]]], int],
) -> None:
    """Run one upload through the pipeline. Called on a worker thread.

    ``ingest_events`` is handed the finished event dicts, already carrying the
    operator's location, and returns how many were stored. It is passed in
    rather than imported so this module never reaches back into the server.
    """
    job = get_job(db_path, job_id)
    if not job:
        return

    lat, lon = job["location"]
    out_dir = job_dir(job_id) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir(job_id) / "pipeline.log"
    preset = QUALITY_PRESETS.get(job["quality"], QUALITY_PRESETS["standard"])

    _update(db_path, job_id, status=STATUS_RUNNING, stage="detect",
            message="Starting the edge pipeline.")

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen(
                _pipeline_command(Path(job["video_path"]), out_dir, preset),
                cwd=str(BASE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            for line in proc.stdout:
                log.write(line)
                log.flush()
                stripped = line.strip()
                for key, (stage, label) in _STAGE_LABELS.items():
                    if key in stripped:
                        _update(db_path, job_id, stage=stage, message=label)
                        break
                else:
                    if stripped.startswith("Processed ") or "FPS" in stripped:
                        _update(db_path, job_id, message=stripped[:180])
            code = proc.wait()
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
        _update(db_path, job_id, status=STATUS_ERROR, stage="error",
                message=f"Could not start the pipeline: {exc}",
                finished_at=datetime.now().isoformat(timespec="seconds"))
        return

    if code != 0:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
        except (OSError, IndexError):
            pass
        _update(db_path, job_id, status=STATUS_ERROR, stage="error",
                message=f"Pipeline exited with code {code}. {tail}"[:400],
                finished_at=datetime.now().isoformat(timespec="seconds"))
        return

    # ---- collect what the run produced ------------------------------------
    _update(db_path, job_id, stage="ingest", message="Storing events.")

    events: List[Dict[str, Any]] = []
    events_path = out_dir / "events.jsonl"
    if events_path.exists():
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue

    annotated = out_dir / "annotated_video.mp4"
    encode_note = ""
    if annotated.exists():
        _update(db_path, job_id, stage="encode",
                message="Re-encoding the annotated clip for the browser.")
        ok, encode_note = make_web_playable(annotated)
        if not ok:
            # Not fatal. The events are the deliverable; the clip is a view of
            # them, and the file is still on disk and playable in VLC.
            pass
    annotated_url = f"/videos/{job_id}/out/annotated_video.mp4" if annotated.exists() else None

    for ev in events:
        # The operator's coordinate, not the pipeline's placeholder. See the
        # module docstring for why this is a site marker and not a fix.
        ev["location"] = [float(lat), float(lon)]
        ev["conditions"] = None          # M6 owns this; the edge never fills it
        # severity is left exactly as the edge derived it from ttc_s. It has no
        # setter there and none here - PRD 5.3.
        if annotated_url:
            ev["clip"] = annotated_url

    stored = 0
    try:
        stored = ingest_events(events)
    except Exception as exc:  # noqa: BLE001
        _update(db_path, job_id, status=STATUS_ERROR, stage="error",
                message=f"Pipeline finished but ingest failed: {exc}",
                finished_at=datetime.now().isoformat(timespec="seconds"))
        return

    severe = sum(1 for e in events if e.get("ttc_s") is not None and e["ttc_s"] < 0.8)

    summary = {}
    summary_path = out_dir / "pipeline_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except ValueError:
            summary = {}

    frames = 0
    try:
        frames = int(summary.get("tracking_metrics", {}).get("frames_processed", 0) or 0)
    except (TypeError, ValueError):
        frames = 0

    if events:
        msg = f"{stored} conflict{'' if stored == 1 else 's'} detected, {severe} severe."
    else:
        msg = "Pipeline completed. No conflicts crossed the TTC threshold in this clip."
    if encode_note:
        msg = f"{msg} {encode_note}"

    _update(
        db_path, job_id,
        status=STATUS_DONE,
        stage="done",
        message=msg,
        events_total=stored,
        events_severe=severe,
        frames=frames,
        annotated_url=annotated_url or "",
        summary=json.dumps(summary),
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )


def start_job(db_path: Path, job_id: str, ingest_events) -> None:
    """Kick a job off on a daemon thread and return immediately."""
    t = threading.Thread(
        target=run_job, args=(db_path, job_id, ingest_events),
        name=f"netra-video-{job_id}", daemon=True,
    )
    t.start()
