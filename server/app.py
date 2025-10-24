from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from reportbro import Report, ReportBroError
from io import BytesIO
import json
import os
from datetime import datetime
import tempfile
import io

app = Flask(__name__)
CORS(app)  # Enable CORS untuk frontend

# Folder untuk menyimpan generated reports sementara
UPLOAD_FOLDER = 'temp_reports'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Store generated reports dengan key (untuk download xlsx)
report_cache = {}

@app.route('/api/report/run', methods=['PUT', 'OPTIONS'])
def generate_report():
    """
    Endpoint untuk generate PDF/XLSX report
    """
    if request.method == 'OPTIONS':
        # Handle preflight request
        return '', 200
    
    try:
        # Parse request data
        data = request.get_json()
        
        report_definition = data.get('report')
        report_data = data.get('data', {})
        output_format = data.get('outputFormat', 'pdf')
        is_test_data = data.get('isTestData', False)
        
        print(f"Received request - Format: {output_format}, IsTestData: {is_test_data}")
        
        if not report_definition:
            return jsonify({'errors': [{'msg': 'No report definition provided'}]}), 400
        
        # Create Report instance
        report = Report(report_definition, report_data)
        
        # Check for errors during report creation
        if report.errors:
            print(f"Report validation errors: {len(report.errors)}")
            error_list = []
            for error in report.errors:
                error_list.append({
                    'object_id': error.object_id,
                    'field': error.field,
                    'msg_key': error.msg_key,
                    'info': error.info
                })
            return jsonify({'errors': error_list}), 400
        
        # Generate report
        if output_format == 'pdf':
            # Generate PDF
            print("Generating PDF...")
            pdf_report = report.generate_pdf()
            
            # generate_pdf() returns bytearray directly, not an object with errors
            # If there are errors, ReportBroError exception will be raised
            
            # Save PDF temporarily and return key
            report_key = datetime.now().strftime('%Y%m%d%H%M%S%f')
            report_cache[report_key] = {
                'pdf': pdf_report,
                'report_definition': report_definition,
                'report_data': report_data,
                'timestamp': datetime.now()
            }
            
            print(f"PDF generated successfully. Key: {report_key}")
            print(f"PDF size: {len(pdf_report)} bytes")
            print(f"Cache size: {len(report_cache)}")
            
            # Clean old cache (older than 1 hour)
            clean_old_cache()
            
            # Return key dengan format yang benar
            return f'key:{report_key}', 200, {'Content-Type': 'text/plain'}
            
        elif output_format == 'xlsx':
            # Generate XLSX (untuk download spreadsheet)
            print("Generating XLSX...")
            xlsx_report = report.generate_xlsx()
            
            # Return XLSX file directly
            return Response(
                xlsx_report,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={
                    'Content-Disposition': f'attachment; filename=report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
                }
            )
        
        else:
            return jsonify({'errors': [{'msg': f'Unsupported output format: {output_format}'}]}), 400
    
    except ReportBroError as e:
        # Handle ReportBro specific errors
        print(f"ReportBroError: {e}")
        error_list = []
        for error in e.errors:
            error_list.append({
                'object_id': error.object_id,
                'field': error.field,
                'msg_key': error.msg_key,
                'info': error.info
            })
        return jsonify({'errors': error_list}), 400
    
    except Exception as e:
        print(f"Error generating report: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'errors': [{'msg': str(e)}]}), 500


@app.route('/api/report/run', methods=['GET'])
def get_report():
    """
    Endpoint untuk download report yang sudah di-generate (by key)
    """
    try:
        report_key = request.args.get('key')
        output_format = request.args.get('outputFormat', 'pdf')
        
        print(f"GET request - Key: {report_key}, Format: {output_format}")
        print(f"Available keys in cache: {list(report_cache.keys())}")
        
        if not report_key:
            return jsonify({'error': 'No report key provided'}), 400
            
        if report_key not in report_cache:
            return jsonify({'error': 'Invalid or expired report key', 'key': report_key}), 404
        
        cached_report = report_cache[report_key]
        
        if output_format == 'pdf':
            # Return PDF directly from cache
            print(f"Returning cached PDF, size: {len(cached_report['pdf'])} bytes")
            pdf_bytes = BytesIO(cached_report['pdf'])
            pdf_bytes.seek(0)
            return send_file(
                pdf_bytes,
                mimetype='application/pdf',
                as_attachment=False,
                download_name='report.pdf'
            )
        
        elif output_format == 'xlsx':
            # Generate XLSX from cached report definition
            print("Generating XLSX from cached report...")
            report = Report(
                cached_report['report_definition'],
                cached_report['report_data']
            )
            
            # Check for errors
            if report.errors:
                error_list = []
                for error in report.errors:
                    error_list.append({
                        'object_id': error.object_id,
                        'field': error.field,
                        'msg_key': error.msg_key,
                        'info': error.info
                    })
                return jsonify({'errors': error_list}), 400
            
            xlsx_report = report.generate_xlsx()
            
            return Response(
                xlsx_report,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                headers={
                    'Content-Disposition': f'attachment; filename=report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
                }
            )
        
        else:
            return jsonify({'error': f'Unsupported output format: {output_format}'}), 400
    
    except ReportBroError as e:
        print(f"ReportBroError during retrieval: {e}")
        error_list = []
        for error in e.errors:
            error_list.append({
                'object_id': error.object_id,
                'field': error.field,
                'msg_key': error.msg_key,
                'info': error.info
            })
        return jsonify({'errors': error_list}), 400
    
    except Exception as e:
        print(f"Error retrieving report: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def clean_old_cache():
    """
    Clean cache entries older than 1 hour
    """
    current_time = datetime.now()
    keys_to_delete = []
    
    for key, value in report_cache.items():
        time_diff = (current_time - value['timestamp']).total_seconds()
        if time_diff > 3600:  # 1 hour
            keys_to_delete.append(key)
    
    for key in keys_to_delete:
        print(f"Deleting old cache key: {key}")
        del report_cache[key]


# Endpoint untuk testing/debugging
@app.route('/api/report/cache', methods=['GET'])
def get_cache_info():
    """
    Get cache information (for debugging)
    """
    cache_info = {}
    for key, value in report_cache.items():
        cache_info[key] = {
            'timestamp': value['timestamp'].isoformat(),
            'pdf_size': len(value['pdf']) if 'pdf' in value else 0,
            'age_seconds': (datetime.now() - value['timestamp']).total_seconds()
        }
    return jsonify({
        'cache_size': len(report_cache),
        'items': cache_info
    })


@app.route('/api/report/test', methods=['GET'])
def test():
    """
    Simple test endpoint
    """
    return jsonify({
        'status': 'ok',
        'message': 'ReportBro server is running',
        'version': 'reportbro-lib',
        'cache_size': len(report_cache)
    })


if __name__ == '__main__':
    print("=" * 60)
    print("Starting ReportBro Server")
    print("=" * 60)
    print("Server URL: http://localhost:8000")
    print("\nAvailable Endpoints:")
    print("  PUT  /api/report/run              - Generate report")
    print("  GET  /api/report/run?key=xxx      - Download report")
    print("  GET  /api/report/cache            - View cache info")
    print("  GET  /api/report/test             - Test connection")
    print("=" * 60)
    print("\nWaiting for requests...\n")
    app.run(host='0.0.0.0', port=8000, debug=True)