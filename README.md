# How to Install and Use the Project

This README provides instructions to run the ReportBro Designer demo and includes documentation for a Flask-based ReportBro server implementation.

## Installation (frontend demo)

1. Clone the repository:

```bash
git clone <repository-url>
cd reportbro-designer-3.11.3
```

2. Install dependencies:

```bash
npm install
```

3. Run the dev server:

```bash
npx vite --port 3000
```

4. Open the app in your browser:

```
http://localhost:3000
```

## ReportBro Server

A Flask-based server implementation for ReportBro that handles PDF and XLSX report generation. The server provides REST API endpoints to generate and manage reports using the ReportBro library.

Features

- Generate PDF and XLSX reports
- RESTful API endpoints
- Report caching system
- CORS enabled for frontend integration
- Automatic cache cleanup
- Support for both synchronous and asynchronous report generation

Prerequisites

- Python 3.12
- Virtual Environment (venv)

Installation (server)

1. Navigate to the server directory:

```bash
cd server
```

2. Create and activate a virtual environment:

```bash
# Create virtual environment
py -3.12 -m venv venv

# Activate virtual environment
# For Windows PowerShell:
.\venv\Scripts\Activate.ps1
# For Windows CMD:
.\venv\Scripts\activate.bat
# For Linux/Mac:
source venv/bin/activate
```

3. Install Python dependencies:

```bash
pip install -r requirements.txt
```

API Endpoints

1. Generate Report (PUT /api/report/run)

   - Generates PDF or XLSX reports
   - Request body should include report definition and data
   - Returns a key for PDF reports (for later download) or direct download for XLSX

2. Download Report (GET /api/report/run?key=xxx)

   - Downloads a previously generated report using the provided key
   - Supports both PDF and XLSX formats

3. View Cache Info (GET /api/report/cache)

   - Shows current cache status and information
   - Useful for debugging

4. Test Connection (GET /api/report/test)

   - Simple endpoint to test if server is running
   - Returns server status and version information

Running the Server

1. Make sure the virtual environment is activated (see above).

2. Start the server:

```bash
python app.py
```

The server will start on `http://localhost:8000` by default.

Configuration

- Server listens on port 8000 by default
- Temporary reports are stored in the `temp_reports` directory
- Cache entries are cleaned up after 1 hour by default
- CORS is enabled for frontend integration

Error Handling

The server returns helpful error messages for common issues such as:

- Invalid report definitions
- Missing data
- Report generation errors
- Invalid cache keys
- Unsupported formats

Directory Structure

```
reportbro-server/
├── app.py              # Main application file
├── requirements.txt    # Python dependencies
├── temp_reports/       # Temporary report storage
├── venv/               # Virtual environment
└── README.md           # This file
```

Development

- The server runs in debug mode by default (suitable for development only).
- Disable debug mode for production deployment.

Cache Management

- Reports are cached for 1 hour by default.
- An automatic cleanup removes cache entries older than 1 hour.
- Cache status can be inspected via the `/api/report/cache` endpoint.

License

This project is distributed under the ISC license (see `package.json`).
How to Install and Use the Project
