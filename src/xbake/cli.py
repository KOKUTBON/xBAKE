"""
Command line interface for xBAKE.
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from xbake import __version__
from xbake.config import load_config
from xbake.storage.database import Database

logger = logging.getLogger("xbake")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_source(value: str):
    return int(value) if value.strip().isdigit() else value


def cmd_run(args) -> int:
    """Run the live inspection engine."""
    from xbake.storage.sync_worker import CloudSyncWorker
    from xbake.vision.camera import CameraStream
    from xbake.vision.pipeline import InspectionPipeline
    from xbake.web.app import WebDashboardServer

    config = load_config(args.config)
    if args.source is not None:
        config.camera.source = _parse_source(args.source)
    if args.host is not None:
        config.web.host = args.host
    if args.port is not None:
        config.web.port = args.port
    if args.no_web:
        config.web.enabled = False
    if args.mock_hardware:
        config.hardware.driver = "mock"

    logger.info(f"Starting xBAKE v{__version__} (config: {config.path})")
    logger.info(f"Video source: {config.camera.source}")

    db = Database(config.storage.db_path)
    stream = CameraStream(config.camera, loop_video=not args.no_loop)
    pipeline = InspectionPipeline(config, stream=stream).start()

    sync_worker = CloudSyncWorker(config.storage, db).start() if config.storage.enable_cloud_sync else None
    web_server = None
    if config.web.enabled:
        try:
            web_server = WebDashboardServer(pipeline, db, config).start()
        except OSError as e:
            logger.error(f"Could not start web dashboard on {config.web.host}:{config.web.port}: {e}")
            pipeline.stop()
            return 1

    show_gui = args.gui
    cv2 = None
    if show_gui:
        try:
            import cv2
        except ImportError:
            logger.warning("OpenCV GUI not available, running headless")
            show_gui = False

    stop_requested = False

    def _request_stop(*_):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _request_stop)

    last_telemetry = time.time()
    try:
        while not stop_requested:
            result, display_frame = pipeline.process_next_frame()
            if display_frame is None:
                if stream.finished:
                    logger.info("Video source finished.")
                    break
                time.sleep(0.005)
                continue

            for item in result.new_defects + result.passed_items:
                db.insert_inspection(item)

            if web_server:
                web_server.update_frame(display_frame)

            now = time.time()
            if now - last_telemetry >= config.storage.telemetry_interval_seconds:
                db.insert_telemetry(pipeline.metrics)
                last_telemetry = now

            if show_gui:
                cv2.imshow("xBAKE Live", display_frame)
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    break
    except KeyboardInterrupt:
        logger.info("Shutdown requested (Ctrl+C)...")
    finally:
        for item in pipeline.flush():
            db.insert_inspection(item)
        db.insert_telemetry(pipeline.metrics)
        pipeline.stop()
        if sync_worker:
            sync_worker.stop()
        if web_server:
            web_server.stop()
        if show_gui:
            cv2.destroyAllWindows()
        logger.info("xBAKE shutdown complete.")
    return 0


def cmd_benchmark(args) -> int:
    """Process every frame of a recorded video as fast as possible and report results."""
    import cv2

    from xbake.vision.pipeline import InspectionPipeline

    video_path = args.video
    if not Path(video_path).exists():
        logger.error(f"Benchmark video not found: {video_path}")
        return 1

    config = load_config(args.config)
    config.camera.source = video_path
    config.hardware.driver = "mock"
    config.storage.save_snapshots = bool(args.save_snapshots)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Could not open video: {video_path}")
        return 1

    pipeline = InspectionPipeline(config, stream=_NullStream(), actuate=False)
    db = Database(args.db) if args.db else None

    logger.info(f"Running offline benchmark on: {video_path}")
    frames = 0
    start = time.perf_counter()
    try:
        while args.max_frames <= 0 or frames < args.max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            result, _ = pipeline.process_frame(frame)
            frames += 1
            if db:
                for item in result.new_defects + result.passed_items:
                    db.insert_inspection(item)
        for item in pipeline.flush():
            if db:
                db.insert_inspection(item)
    finally:
        cap.release()
        pipeline.rejector.cleanup()
    elapsed = time.perf_counter() - start

    m = pipeline.metrics
    lines = [
        "=" * 50,
        "BENCHMARK RESULTS",
        "=" * 50,
        f"Video                  : {video_path}",
        f"Frames processed       : {frames}",
        f"Elapsed time           : {elapsed:.2f}s",
        f"Average pipeline FPS   : {frames / max(1e-6, elapsed):.1f}",
        f"Mean frame latency     : {1000.0 * elapsed / max(1, frames):.2f} ms",
        f"Items inspected        : {m.total_inspected}",
        f"Good                   : {m.total_good}",
        f"Burnt                  : {m.total_burnt}",
        f"Undercooked            : {m.total_undercooked}",
        f"Deformed               : {m.total_deformed}",
        f"Defect rate            : {m.defect_rate_pct}%",
        "=" * 50,
    ]
    print("\n".join(lines))
    return 0


class _NullStream:
    """Stand-in stream for offline processing where frames are pushed directly."""

    source = "offline"
    finished = False
    is_opened = True
    fps = 0.0

    def start(self):
        return self

    def stop(self):
        pass

    def read(self):
        return 0, None

    def apply_settings(self):
        pass


def cmd_export(args) -> int:
    """Export inspection records from the SQLite database to CSV."""
    config = load_config(args.config)
    db_path = args.db or config.storage.db_path
    if not Path(db_path).exists():
        logger.error(f"Database not found: {db_path}")
        return 1
    db = Database(db_path)
    if args.output == "-":
        count = db.export_csv(sys.stdout)
    else:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            count = db.export_csv(f)
        logger.info(f"Exported {count} records to {args.output}")
    return 0


def cmd_init_config(args) -> int:
    """Write a configuration file populated with defaults."""
    from xbake.config import AppConfig

    out = Path(args.output)
    if out.exists() and not args.force:
        logger.error(f"{out} already exists (use --force to overwrite)")
        return 1
    AppConfig().save(out)
    logger.info(f"Wrote default configuration to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xbake",
        description="xBAKE: computer vision quality control for bakery conveyor lines",
    )
    parser.add_argument("--version", action="version", version=f"xBAKE {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Start the live inspection engine (default)")
    p_run.add_argument("-c", "--config", help="Path to config.json (default: ./config.json)")
    p_run.add_argument("-s", "--source", help="Camera index (e.g. 0), video file, or stream URL")
    p_run.add_argument("--host", help="Dashboard bind address (default from config: 127.0.0.1)")
    p_run.add_argument("-p", "--port", type=int, help="Dashboard port (default from config: 8000)")
    p_run.add_argument("--gui", action="store_true", help="Open a local OpenCV preview window")
    p_run.add_argument("--no-web", action="store_true", help="Disable the web dashboard")
    p_run.add_argument("--no-loop", action="store_true", help="Stop at the end of a video file")
    p_run.add_argument("--mock-hardware", action="store_true", help="Simulate the rejector")
    p_run.set_defaults(func=cmd_run)

    p_bench = sub.add_parser("benchmark", help="Measure pipeline throughput on a video file")
    p_bench.add_argument("video", help="Path to a video file")
    p_bench.add_argument("-c", "--config", help="Path to config.json")
    p_bench.add_argument("--max-frames", type=int, default=0, help="Stop after N frames (0 = all)")
    p_bench.add_argument("--db", help="Also record inspections into this SQLite file")
    p_bench.add_argument("--save-snapshots", action="store_true", help="Save defect snapshots")
    p_bench.set_defaults(func=cmd_benchmark)

    p_exp = sub.add_parser("export", help="Export inspection records to CSV")
    p_exp.add_argument("-o", "--output", default="-", help="Output CSV path ('-' for stdout)")
    p_exp.add_argument("-c", "--config", help="Path to config.json")
    p_exp.add_argument("--db", help="SQLite database path (default from config)")
    p_exp.set_defaults(func=cmd_export)

    p_init = sub.add_parser("init-config", help="Write a default config.json")
    p_init.add_argument("-o", "--output", default="config.json", help="Output path")
    p_init.add_argument("-f", "--force", action="store_true", help="Overwrite an existing file")
    p_init.set_defaults(func=cmd_init_config)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    if not args.command:
        # Only top-level options were given: default to "run"
        args = parser.parse_args(list(argv if argv is not None else sys.argv[1:]) + ["run"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
