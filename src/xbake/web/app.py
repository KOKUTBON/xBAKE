"""
Embedded web server: dashboard UI, live MJPEG stream, and JSON/CSV API.
Uses only the Python standard library.
"""

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import cv2

from xbake import __version__
from xbake.config import TUNABLE_PARAMS, AppConfig, ConfigError
from xbake.storage.database import Database
from xbake.vision.pipeline import InspectionPipeline

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"
LOGO_PATH = Path(__file__).parent / "static" / "xbake-logo.png"
MAX_BODY_BYTES = 64 * 1024


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, app: "WebDashboardServer"):
        super().__init__(address, handler)
        self.app = app


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Routes HTTP requests to the dashboard, stream, and API handlers."""

    server: DashboardServer
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> "WebDashboardServer":
        return self.server.app

    # ---------- routing ----------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._serve_html()
            elif path == "/logo.png":
                self._serve_logo()
            elif path == "/stream":
                self._serve_mjpeg(view=query.get("view", ["annotated"])[0])
            elif path == "/frame.jpg":
                self._serve_frame(view=query.get("view", ["annotated"])[0])
            elif path == "/api/status":
                self._send_json(self.app.status())
            elif path == "/api/metrics":
                self._send_json(self.app.pipeline.metrics.to_dict())
            elif path == "/api/config":
                self._send_json(
                    {
                        "config": self.app.config.to_dict(include_secrets=False),
                        "tunable": {
                            s: {k: {"kind": v[0], "min": v[1], "max": v[2]} for k, v in p.items()}
                            for s, p in TUNABLE_PARAMS.items()
                        },
                        "path": self.app.config.path,
                    }
                )
            elif path == "/api/recent":
                limit = _int_param(query, "limit", 20, 1, 500)
                defects_only = query.get("defects", ["0"])[0] in ("1", "true")
                self._send_json(self.app.database.get_recent_inspections(limit, defects_only))
            elif path == "/api/summary":
                self._send_json(self.app.database.get_summary())
            elif path == "/api/export.csv":
                self._serve_csv(query)
            elif path.startswith("/api/snapshot/"):
                self._serve_snapshot(path.rsplit("/", 1)[-1])
            else:
                self._send_error(404, "Not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # never let a request crash the server thread
            logger.exception(f"Error handling GET {path}")
            self._send_error(500, str(e))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if not self._authorized():
                self._send_error(401, "Missing or invalid token")
                return
            body = self._read_json()
            if body is None:
                return

            if path == "/api/config":
                applied = self.app.config.apply_updates(body)
                if "camera" in applied:
                    self.app.pipeline.stream.apply_settings()
                self._send_json({"status": "ok", "applied": applied})
            elif path == "/api/config/save":
                saved = self.app.config.save()
                self._send_json({"status": "ok", "saved": str(saved)})
            elif path == "/api/rejector/test":
                hw = self.app.config.hardware
                fired = self.app.pipeline.actuator.trigger(hw.pulse_duration_s, reason="manual test")
                self._send_json({"status": "ok", "triggered": fired})
            elif path == "/api/metrics/reset":
                self.app.pipeline.reset_metrics()
                self._send_json({"status": "ok"})
            else:
                self._send_error(404, "Not found")
        except ConfigError as e:
            self._send_error(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            logger.exception(f"Error handling POST {path}")
            self._send_error(500, str(e))

    # ---------- helpers ----------

    def _authorized(self) -> bool:
        token = self.app.config.effective_auth_token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        return hmac.compare_digest(supplied.encode(), token.encode())

    def _read_json(self) -> Optional[Any]:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_error(413, "Request body too large")
            return None
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            self._send_error(400, f"Invalid JSON: {e}")
            return None

    def _cors_headers(self):
        if self.app.config.web.allow_cors:
            self.send_header("Access-Control-Allow-Origin", "*")

    def _send_bytes(self, status: int, content_type: str, payload: bytes, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: Any, status: int = 200):
        self._send_bytes(status, "application/json", json.dumps(data).encode("utf-8"))

    def _send_error(self, status: int, message: str):
        try:
            self._send_json({"error": message}, status=status)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_html(self):
        if TEMPLATE_PATH.exists():
            content = TEMPLATE_PATH.read_bytes()
        else:
            content = b"<h1>xBAKE dashboard template missing</h1>"
        self._send_bytes(200, "text/html; charset=utf-8", content)

    def _serve_logo(self):
        if not LOGO_PATH.is_file():
            self._send_error(404, "Logo not available")
            return
        self._send_bytes(200, "image/png", LOGO_PATH.read_bytes())

    def _serve_csv(self, query):
        since = _float_param(query, "since")
        until = _float_param(query, "until")
        payload = self.app.database.export_csv_string(since, until).encode("utf-8")
        filename = f"xbake_inspections_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        self._send_bytes(
            200,
            "text/csv; charset=utf-8",
            payload,
            {"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _serve_snapshot(self, record_id: str):
        if not record_id.isdigit():
            self._send_error(400, "Invalid record id")
            return
        rec = self.app.database.get_inspection(int(record_id))
        snap = rec.get("snapshot_path") if rec else None
        if not snap:
            self._send_error(404, "No snapshot for this record")
            return
        # Only serve files that live inside the configured snapshot directory
        base = Path(self.app.config.storage.snapshot_dir).resolve()
        file_path = Path(snap).resolve()
        if base not in file_path.parents or not file_path.is_file():
            self._send_error(404, "Snapshot not available")
            return
        self._send_bytes(200, "image/jpeg", file_path.read_bytes())

    def _serve_frame(self, view: str):
        jpeg = self.app.get_jpeg(view)
        if not jpeg:
            self._send_error(503, "No frame available yet")
            return
        self._send_bytes(200, "image/jpeg", jpeg)

    def _serve_mjpeg(self, view: str):
        """Continuous multipart MJPEG stream."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        interval = 1.0 / max(1, self.app.config.web.stream_fps)
        last = None
        while self.app.running:
            jpeg = self.app.get_jpeg(view)
            if jpeg and jpeg is not last:
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii"))
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                last = jpeg
            time.sleep(interval)

    def log_message(self, format, *args):
        logger.debug("%s - %s", self.address_string(), format % args)


def _int_param(query, name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(query.get(name, [default])[0])))
    except (TypeError, ValueError):
        return default


def _float_param(query, name: str) -> Optional[float]:
    try:
        return float(query[name][0]) if name in query else None
    except (TypeError, ValueError):
        return None


class WebDashboardServer:
    """Embedded HTTP server hosting the dashboard and API."""

    def __init__(self, pipeline: InspectionPipeline, database: Database, config: AppConfig):
        self.pipeline = pipeline
        self.database = database
        self.config = config
        self.running = False
        self._server: Optional[DashboardServer] = None
        self._thread: Optional[threading.Thread] = None
        self._jpeg_lock = threading.Lock()
        self._jpegs = {"annotated": b"", "mask": b""}
        self._last_encode = 0.0
        self.started_at = time.time()

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self.config.web.port

    def update_frame(self, frame_bgr) -> None:
        """Encode the latest annotated frame (and mask view) as JPEG, rate-limited to stream_fps."""
        if frame_bgr is None:
            return
        now = time.time()
        if now - self._last_encode < 1.0 / max(1, self.config.web.stream_fps):
            return
        self._last_encode = now
        encoded = {}
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if ok:
            encoded["annotated"] = buf.tobytes()
        mask = self.pipeline.latest_mask_view
        if mask is not None:
            ok, buf = cv2.imencode(".jpg", mask, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
            if ok:
                encoded["mask"] = buf.tobytes()
        with self._jpeg_lock:
            self._jpegs.update(encoded)

    def get_jpeg(self, view: str) -> bytes:
        with self._jpeg_lock:
            return self._jpegs.get(view) or self._jpegs["annotated"]

    def status(self) -> dict:
        stream = self.pipeline.stream
        return {
            "version": __version__,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "camera": {
                "source": str(stream.source),
                "connected": stream.is_opened,
                "fps": stream.fps,
                "finished": stream.finished,
            },
            "actuator": {
                "type": type(self.pipeline.actuator).__name__,
                "active": self.pipeline.actuator.is_active(),
                "enabled": self.config.hardware.enabled,
            },
            "classifier": "onnx" if self.pipeline.classifier.uses_onnx else "rules",
            "auth_required": bool(self.config.effective_auth_token),
            "cloud_sync": self.config.storage.enable_cloud_sync,
        }

    def start(self) -> "WebDashboardServer":
        host, port = self.config.web.host, self.config.web.port
        self._server = DashboardServer((host, port), DashboardRequestHandler, self)
        self.running = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="WebServer", daemon=True)
        self._thread.start()
        shown = "localhost" if host in ("0.0.0.0", "127.0.0.1", "") else host
        logger.info(f"Web dashboard available at http://{shown}:{self.port}")
        if host not in ("127.0.0.1", "localhost") and not self.config.effective_auth_token:
            logger.warning(
                "Dashboard is reachable from the network without an auth token; "
                "set web.auth_token or XBAKE_WEB_TOKEN to protect control endpoints."
            )
        return self

    def stop(self) -> None:
        self.running = False
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        logger.info("Web dashboard server stopped.")
