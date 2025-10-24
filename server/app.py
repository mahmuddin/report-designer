#!/usr/bin/env python3
"""
Refactored ReportBro local server (Flask)

Features / perbaikan:
- Konsisten mengirim PDF/XLSX via send_file(BytesIO(...)) sehingga koneksi tidak
  putus sebelum file selesai dikirim (menghindari curl error 18).
- Thread-safe cache dengan threading.Lock.
- Background cleaner thread untuk menghapus cache lebih lama dari TTL (default 1 hour).
- CORS terkonfigurasi untuk semua /api/* origin.
- Logging yang lebih informatif.
- Response kompatibel dengan ReportBro Designer (PUT returns plain "key:<key>").
- Support PDF & XLSX.
"""
from flask import Flask, request, jsonify, send_file, Response, make_response
from flask_cors import CORS
from reportbro import Report, ReportBroError
from io import BytesIO
from datetime import datetime, timedelta
import threading
import time
import os
import logging
import uuid
from typing import Dict, Any

# ---------- Configuration ----------
HOST = "0.0.0.0"
PORT = 8000
CACHE_TTL_SECONDS = 3600  # 1 hour
CACHE_CLEAN_INTERVAL = 300  # clean every 5 minutes
API_PREFIX = "/api/report"

# ---------- App & Logging ----------
app = Flask(__name__)
# Allow any origin for /api/* to simplify development; adjust for production.
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------- Cache (thread-safe) ----------
# report_cache keys -> { 'pdf': bytes, 'report_definition': dict, 'report_data': dict, 'timestamp': datetime }
report_cache: Dict[str, Dict[str, Any]] = {}
cache_lock = threading.Lock()


def make_key() -> str:
    """Create a unique cache key."""
    # use uuid4 + timestamp for readability and uniqueness
    return datetime.utcnow().strftime("%Y%m%d%H%M%S%f") + "-" + uuid.uuid4().hex


def cache_set(key: str, entry: Dict[str, Any]) -> None:
    with cache_lock:
        report_cache[key] = entry


def cache_get(key: str) -> Dict[str, Any]:
    with cache_lock:
        return report_cache.get(key)


def cache_delete(key: str) -> None:
    with cache_lock:
        if key in report_cache:
            del report_cache[key]


def cache_info() -> Dict[str, Any]:
    with cache_lock:
        info = {}
        for k, v in report_cache.items():
            info[k] = {
                "timestamp": v["timestamp"].isoformat(),
                "pdf_size": len(v["pdf"]) if "pdf" in v else 0,
                "age_seconds": (datetime.utcnow() - v["timestamp"]).total_seconds(),
            }
        return {"cache_size": len(report_cache), "items": info}


def background_cache_cleaner(stop_event: threading.Event) -> None:
    """Background thread to clean old cache entries periodically."""
    logging.info("Background cache cleaner started (interval=%s seconds)", CACHE_CLEAN_INTERVAL)
    while not stop_event.wait(CACHE_CLEAN_INTERVAL):
        now = datetime.utcnow()
        keys_to_delete = []
        with cache_lock:
            for k, v in report_cache.items():
                age = (now - v["timestamp"]).total_seconds()
                if age > CACHE_TTL_SECONDS:
                    keys_to_delete.append(k)
            for k in keys_to_delete:
                logging.info("Cache cleaner removing key: %s (age %s s)", k, int((now - report_cache[k]["timestamp"]).total_seconds()))
                del report_cache[k]
    logging.info("Background cache cleaner stopping.")


# Start background cleaner thread
_cleaner_stop = threading.Event()
_cleaner_thread = threading.Thread(target=background_cache_cleaner, args=(_cleaner_stop,), daemon=True)
_cleaner_thread.start()

# ---------- Helper functions ----------
def generate_pdf_from_definition(report_definition: dict, report_data: dict) -> bytes:
    """
    Uses reportbro.Report to generate PDF bytes. Raises exceptions on failure.
    """
    report = Report(report_definition, report_data)
    if report.errors:
        # Collect structured errors and raise a ReportBroError-like exception
        raise ReportBroError(report.errors)
    pdf_bytes = report.generate_pdf()
    # ensure type is bytes
    if isinstance(pdf_bytes, (bytearray, bytes)):
        return bytes(pdf_bytes)
    # if the library returned something else unexpectedly
    return bytes(pdf_bytes)


def generate_xlsx_from_definition(report_definition: dict, report_data: dict) -> bytes:
    report = Report(report_definition, report_data)
    if report.errors:
        raise ReportBroError(report.errors)
    xlsx_bytes = report.generate_xlsx()
    if isinstance(xlsx_bytes, (bytearray, bytes)):
        return bytes(xlsx_bytes)
    return bytes(xlsx_bytes)


# ---------- Routes ----------
@app.route(f"{API_PREFIX}/run", methods=["PUT", "OPTIONS"])
def generate_report():
    """
    Generate a report (PDF/XLSX).
    Expected payload (from ReportBro Designer):
    {
      "report": { ... },
      "data": { ... },
      "outputFormat": "pdf" | "xlsx",
      "isTestData": true|false
    }
    Returns:
      - text/plain "key:<cache_key>" (kept for compatibility with ReportBro Designer)
      - 400 or 500 with JSON errors on failure
    """
    if request.method == "OPTIONS":
        return "", 200

    try:
        payload = request.get_json(force=True)
        report_definition = payload.get("report")
        report_data = payload.get("data", {})
        output_format = (payload.get("outputFormat") or "pdf").lower()
        is_test_data = bool(payload.get("isTestData", False))

        logging.info("Received generate request. format=%s isTestData=%s", output_format, is_test_data)

        if not report_definition:
            return jsonify({"errors": [{"msg": "No report definition provided"}]}), 400

        if output_format == "pdf":
            # generate bytes
            pdf_bytes = generate_pdf_from_definition(report_definition, report_data)
            cache_key = make_key()
            cache_entry = {
                "pdf": pdf_bytes,
                "report_definition": report_definition,
                "report_data": report_data,
                "timestamp": datetime.utcnow(),
            }
            cache_set(cache_key, cache_entry)
            logging.info("PDF generated and cached. key=%s size=%d bytes", cache_key, len(pdf_bytes))
            # Return plain text "key:<key>" — designer expects that format
            resp = make_response(f"key:{cache_key}", 200)
            resp.headers["Content-Type"] = "text/plain"
            # Also include header for convenience
            resp.headers["X-Report-Key"] = cache_key
            return resp

        elif output_format == "xlsx":
            xlsx_bytes = generate_xlsx_from_definition(report_definition, report_data)
            logging.info("XLSX generated. size=%d bytes", len(xlsx_bytes))
            bio = BytesIO(xlsx_bytes)
            bio.seek(0)
            filename = f"report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return send_file(
                bio,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name=filename,
            )

        else:
            return jsonify({"errors": [{"msg": f"Unsupported output format: {output_format}"}]}), 400

    except ReportBroError as e:
        logging.exception("ReportBroError while generating report")
        error_list = []
        # ReportBroError in this library exposes e.errors (list)
        for err in getattr(e, "errors", []):
            error_list.append({
                "object_id": getattr(err, "object_id", None),
                "field": getattr(err, "field", None),
                "msg_key": getattr(err, "msg_key", None),
                "info": getattr(err, "info", None),
            })
        return jsonify({"errors": error_list or [{"msg": str(e)}]}), 400

    except Exception as e:
        logging.exception("Unhandled exception while generating report")
        return jsonify({"errors": [{"msg": str(e)}]}), 500


@app.route(f"{API_PREFIX}/run", methods=["GET"])
def get_report():
    """
    Download generated report by key:
      GET /api/report/run?key=<key>&outputFormat=pdf|xlsx
    """
    try:
        report_key = request.args.get("key")
        output_format = (request.args.get("outputFormat") or "pdf").lower()

        logging.info("GET report requested. key=%s format=%s", report_key, output_format)
        with cache_lock:
            available_keys = list(report_cache.keys())
        logging.debug("Available cache keys: %s", available_keys)

        if not report_key:
            return jsonify({"error": "No report key provided"}), 400

        cached = cache_get(report_key)
        if not cached:
            return jsonify({"error": "Invalid or expired report key", "key": report_key}), 404

        if output_format == "pdf":
            pdf_bytes = cached.get("pdf")
            if not pdf_bytes:
                return jsonify({"error": "PDF not available for this key"}), 404

            bio = BytesIO(pdf_bytes)
            bio.seek(0)
            # send_file will set Content-Length and stream properly
            return send_file(
                bio,
                mimetype="application/pdf",
                as_attachment=False,
                download_name="report.pdf",
            )

        elif output_format == "xlsx":
            # Option: regenerate XLSX from cached definition & data to keep memory small
            logging.info("Generating XLSX from cached definition for key=%s", report_key)
            xlsx_bytes = generate_xlsx_from_definition(cached["report_definition"], cached["report_data"])
            bio = BytesIO(xlsx_bytes)
            bio.seek(0)
            filename = f"report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
            return send_file(
                bio,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name=filename,
            )
        else:
            return jsonify({"error": f"Unsupported output format: {output_format}"}), 400

    except ReportBroError as e:
        logging.exception("ReportBroError during GET")
        error_list = []
        for err in getattr(e, "errors", []):
            error_list.append({
                "object_id": getattr(err, "object_id", None),
                "field": getattr(err, "field", None),
                "msg_key": getattr(err, "msg_key", None),
                "info": getattr(err, "info", None),
            })
        return jsonify({"errors": error_list or [{"msg": str(e)}]}), 400

    except Exception as e:
        logging.exception("Unhandled exception while retrieving report")
        return jsonify({"error": str(e)}), 500


@app.route(f"{API_PREFIX}/cache", methods=["GET"])
def route_cache_info():
    """Debug endpoint to inspect cache contents (timestamps & sizes)."""
    return jsonify(cache_info())


@app.route(f"{API_PREFIX}/test", methods=["GET"])
def route_test():
    return jsonify({
        "status": "ok",
        "message": "ReportBro server is running",
        "version": "reportbro-lib",
        "cache_size": len(report_cache)
    })


# Graceful shutdown helper (for local dev)
def shutdown_background_cleaner():
    _cleaner_stop.set()
    _cleaner_thread.join(timeout=2)


if __name__ == "__main__":
    logging.info("=" * 60)
    logging.info("Starting ReportBro Server")
    logging.info("=" * 60)
    logging.info("Server URL: http://%s:%s", HOST, PORT)
    logging.info("Available Endpoints:")
    logging.info("  PUT  /api/report/run              - Generate report (returns plain 'key:<key>')")
    logging.info("  GET  /api/report/run?key=xxx      - Download report")
    logging.info("  GET  /api/report/cache            - View cache info")
    logging.info("  GET  /api/report/test             - Test connection")
    logging.info("=" * 60)
    try:
        app.run(host=HOST, port=PORT, debug=True)
    finally:
        shutdown_background_cleaner()
