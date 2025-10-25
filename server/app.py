#!/usr/bin/env python3
"""
Refactored ReportBro local server (Flask) with richText fallback for open-source reportbro-lib
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
import re
import html

# ---------- Configuration ----------
HOST = "0.0.0.0"
PORT = 8000
CACHE_TTL_SECONDS = 3600  # 1 hour
CACHE_CLEAN_INTERVAL = 300  # clean every 5 minutes
API_PREFIX = "/api/report"

# ---------- App & Logging ----------
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------- Cache (thread-safe) ----------
report_cache: Dict[str, Dict[str, Any]] = {}
cache_lock = threading.Lock()


def make_key() -> str:
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
    logging.info("Background cache cleaner started (interval=%s seconds)", CACHE_CLEAN_INTERVAL)
    while not stop_event.wait(CACHE_CLEAN_INTERVAL):
        now = datetime.utcnow()
        keys_to_delete = []
        with cache_lock:
            for k, v in list(report_cache.items()):
                age = (now - v["timestamp"]).total_seconds()
                if age > CACHE_TTL_SECONDS:
                    keys_to_delete.append(k)
            for k in keys_to_delete:
                logging.info("Cache cleaner removing key: %s", k)
                del report_cache[k]
    logging.info("Background cache cleaner stopping.")


_cleaner_stop = threading.Event()
_cleaner_thread = threading.Thread(target=background_cache_cleaner, args=(_cleaner_stop,), daemon=True)
_cleaner_thread.start()

# ---------- RichText Normalizer ----------
def normalize_richtext_elements(report_definition: dict) -> dict:
    """Convert richText fields into plain text (for OSS reportbro-lib)."""
    if not isinstance(report_definition, dict):
        return report_definition

    doc_elements = report_definition.get("docElements", [])
    for el in doc_elements:
        try:
            if el.get("richText") or el.get("richTextHtml") or el.get("richTextContent"):
                raw_html = el.get("richTextHtml") or ""

                if not raw_html and el.get("richTextContent"):
                    rtc = el.get("richTextContent")
                    if isinstance(rtc, dict) and rtc.get("ops"):
                        # detect attributes from delta
                        align_val = None
                        for op in rtc["ops"]:
                            attrs = op.get("attributes") or {}
                            if attrs.get("bold"):
                                el["bold"] = True
                            if attrs.get("italic"):
                                el["italic"] = True
                            if attrs.get("underline"):
                                el["underline"] = True
                            if attrs.get("strike"):
                                el["strikethrough"] = True
                            if not el.get("link") and isinstance(attrs.get("link"), str):
                                el["link"] = attrs.get("link")
                            if not el.get("textColor") and isinstance(attrs.get("color"), str):
                                el["textColor"] = attrs.get("color")
                            if not el.get("backgroundColor") and isinstance(attrs.get("background"), str):
                                el["backgroundColor"] = attrs.get("background")
                            if not el.get("font") and isinstance(attrs.get("font"), str):
                                f = attrs.get("font").lower()
                                if f in ("helvetica", "times", "courier"):
                                    el["font"] = {"helvetica": "Helvetica", "times": "Times New Roman", "courier": "Courier"}[f]
                                else:
                                    el["font"] = attrs.get("font")
                            if not el.get("fontSize") and isinstance(attrs.get("size"), str):
                                size = attrs.get("size")
                                m = re.match(r"^(\d+)(px|pt)$", size)
                                if m:
                                    num = int(m.group(1))
                                    unit = m.group(2)
                                    el["fontSize"] = int(round(num * 1.333)) if unit == "pt" else num
                            if isinstance(op.get("insert"), str) and op.get("insert", "").endswith("\n"):
                                if isinstance(attrs.get("align"), str):
                                    align_val = attrs.get("align").lower()
                        if align_val in ("left", "center", "right", "justify"):
                            el["horizontalAlignment"] = align_val

                        # build minimal HTML from delta text for plain conversion
                        text_parts = []
                        for op in rtc["ops"]:
                            v = op.get("insert")
                            if isinstance(v, str):
                                text_parts.append(v)
                        raw_html = "<p>" + "</p><p>".join([html.escape(p) for p in text_parts]) + "</p>"

                s = raw_html or el.get("content", "")
                s = s.replace("\r", "")

                # global detection from HTML
                lower = s.lower()
                if "<b" in lower or "<strong" in lower:
                    el["bold"] = True
                if "<i" in lower or "<em" in lower:
                    el["italic"] = True
                if "<u" in lower:
                    el["underline"] = True
                if "<s" in lower or "<strike" in lower or "<del" in lower or "line-through" in lower:
                    el["strikethrough"] = True

                # alignment via class or style
                if "ql-align-center" in lower:
                    el["horizontalAlignment"] = "center"
                elif "ql-align-right" in lower:
                    el["horizontalAlignment"] = "right"
                elif "ql-align-justify" in lower:
                    el["horizontalAlignment"] = "justify"
                else:
                    m_align = re.search(r"text-align\s*:\s*(left|right|center|justify)", lower)
                    if m_align:
                        el["horizontalAlignment"] = m_align.group(1)

                # first link
                if not el.get("link"):
                    m_link = re.search(r"<a[^>]+href=\"([^\"]+)\"", s, flags=re.IGNORECASE)
                    if not m_link:
                        m_link = re.search(r"<a[^>]+href='([^']+)'", s, flags=re.IGNORECASE)
                    if m_link:
                        el["link"] = m_link.group(1)

                # color & background
                if not el.get("textColor"):
                    m_color = re.search(r"color\s*:\s*(#[0-9a-f]{3,8}|rgb\([^\)]+\))", lower)
                    if m_color:
                        el["textColor"] = m_color.group(1)
                if not el.get("backgroundColor"):
                    m_bg = re.search(r"background-color\s*:\s*(#[0-9a-f]{3,8}|rgb\([^\)]+\))", lower)
                    if m_bg:
                        el["backgroundColor"] = m_bg.group(1)

                # font family via class or style
                if not el.get("font"):
                    if "ql-font-helvetica" in lower:
                        el["font"] = "Helvetica"
                    elif "ql-font-times" in lower:
                        el["font"] = "Times New Roman"
                    elif "ql-font-courier" in lower:
                        el["font"] = "Courier"
                    else:
                        m_ff = re.search(r"font-family\s*:\s*([^;]+)", s, flags=re.IGNORECASE)
                        if m_ff:
                            ff = m_ff.group(1).split(",")[0].strip().strip("'\"")
                            el["font"] = ff

                # font size
                if not el.get("fontSize"):
                    m_fs = re.search(r"font-size\s*:\s*(\d+)(px|pt)", s, flags=re.IGNORECASE)
                    if m_fs:
                        num = int(m_fs.group(1))
                        unit = m_fs.group(2).lower()
                        el["fontSize"] = int(round(num * 1.333)) if unit == "pt" else num

                # strip tags to plain text
                s = re.sub(r"(?i)</p\s*>", "\n", s)
                s = re.sub(r"(?i)<br\s*/?>", "\n", s)
                s = re.sub(r"(?i)<li\s*>", "• ", s)
                s = re.sub(r"(?i)</li\s*>", "\n", s)
                text = re.sub(r"<[^>]+>", "", s)
                text = html.unescape(text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                text = text.strip()

                el["content"] = text
                el["richText"] = False
                el.pop("richTextHtml", None)
                el.pop("richTextContent", None)

        except Exception as ex:
            logging.warning("Failed to normalize element %s: %s", el.get("id"), ex)
            continue

    return report_definition


# ---------- PDF/XLSX generation ----------
def generate_pdf_from_definition(report_definition: dict, report_data: dict) -> bytes:
    report = Report(report_definition, report_data)
    if report.errors:
        raise ReportBroError(report.errors)
    pdf_bytes = report.generate_pdf()
    return bytes(pdf_bytes)


def generate_xlsx_from_definition(report_definition: dict, report_data: dict) -> bytes:
    report = Report(report_definition, report_data)
    if report.errors:
        raise ReportBroError(report.errors)
    xlsx_bytes = report.generate_xlsx()
    return bytes(xlsx_bytes)


# ---------- Routes ----------
@app.route(f"{API_PREFIX}/run", methods=["PUT", "OPTIONS"])
def generate_report():
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

        # 🧩 Normalize rich text for open-source engine
        normalize_richtext_elements(report_definition)

        if output_format == "pdf":
            pdf_bytes = generate_pdf_from_definition(report_definition, report_data)
            cache_key = make_key()
            cache_set(cache_key, {
                "pdf": pdf_bytes,
                "report_definition": report_definition,
                "report_data": report_data,
                "timestamp": datetime.utcnow(),
            })
            logging.info("PDF generated and cached (key=%s, %d bytes)", cache_key, len(pdf_bytes))
            resp = make_response(f"key:{cache_key}", 200)
            resp.headers["Content-Type"] = "text/plain"
            resp.headers["X-Report-Key"] = cache_key
            return resp

        elif output_format == "xlsx":
            xlsx_bytes = generate_xlsx_from_definition(report_definition, report_data)
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
        errors = [{"msg": str(err)} for err in getattr(e, "errors", [])]
        return jsonify({"errors": errors or [{"msg": str(e)}]}), 400
    except Exception as e:
        logging.exception("Unhandled exception while generating report")
        return jsonify({"errors": [{"msg": str(e)}]}), 500


@app.route(f"{API_PREFIX}/run", methods=["GET"])
def get_report():
    try:
        report_key = request.args.get("key")
        output_format = (request.args.get("outputFormat") or "pdf").lower()
        logging.info("GET report key=%s format=%s", report_key, output_format)

        if not report_key:
            return jsonify({"error": "No report key provided"}), 400

        cached = cache_get(report_key)
        if not cached:
            return jsonify({"error": "Invalid or expired report key", "key": report_key}), 404

        if output_format == "pdf":
            pdf_bytes = cached.get("pdf")
            if not pdf_bytes:
                return jsonify({"error": "PDF not found"}), 404
            bio = BytesIO(pdf_bytes)
            bio.seek(0)
            return send_file(bio, mimetype="application/pdf", as_attachment=False, download_name="report.pdf")

        elif output_format == "xlsx":
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

    except Exception as e:
        logging.exception("Error while retrieving report")
        return jsonify({"error": str(e)}), 500


@app.route(f"{API_PREFIX}/cache", methods=["GET"])
def route_cache_info():
    return jsonify(cache_info())


@app.route(f"{API_PREFIX}/test", methods=["GET"])
def route_test():
    return jsonify({
        "status": "ok",
        "message": "ReportBro server with richText normalizer is running",
        "version": "reportbro-lib",
        "cache_size": len(report_cache)
    })


def shutdown_background_cleaner():
    _cleaner_stop.set()
    _cleaner_thread.join(timeout=2)


if __name__ == "__main__":
    logging.info("=" * 60)
    logging.info("Starting ReportBro Server with richText normalizer")
    logging.info("=" * 60)
    logging.info("Server URL: http://%s:%s", HOST, PORT)
    logging.info("=" * 60)
    try:
        app.run(host=HOST, port=PORT, debug=True)
    finally:
        shutdown_background_cleaner()
