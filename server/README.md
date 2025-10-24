# ReportBro Server

A Flask-based server implementation for ReportBro that handles PDF and XLSX report generation. This server provides REST API endpoints to generate and manage reports using the ReportBro library.

## Features

- Generate PDF and XLSX reports
- RESTful API endpoints
- Report caching system
- CORS enabled for frontend integration
- Automatic cache cleanup
- Support for both synchronous and asynchronous report generation

## Prerequisites

- Python 3.x
- Virtual Environment (venv)

## Installation

1. Clone this repository:

```bash
git clone <repository-url>
cd reportbro-server
```

2. Create and activate virtual environment:

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# For Windows PowerShell:
.\venv\Scripts\Activate.ps1
# For Windows CMD:
.\venv\Scripts\activate.bat
# For Linux/Mac:
source venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

## API Endpoints

1. **Generate Report** (PUT /api/report/run)

   - Generates PDF or XLSX reports
   - Request body should include report definition and data
   - Returns a key for PDF reports or direct download for XLSX

2. **Download Report** (GET /api/report/run?key=xxx)

   - Downloads a previously generated report using the key
   - Supports both PDF and XLSX formats

3. **View Cache Info** (GET /api/report/cache)

   - Shows current cache status and information
   - Useful for debugging

4. **Test Connection** (GET /api/report/test)
   - Simple endpoint to test if server is running
   - Returns server status and version information

## Running the Server

1. Make sure your virtual environment is activated:

```bash
.\venv\Scripts\Activate.ps1  # For Windows PowerShell
```

2. Start the server:

```bash
python app.py
```

The server will start on `http://localhost:8000`

## Configuration

- Server runs on port 8000 by default
- Reports are temporarily stored in the `temp_reports` directory
- Cache entries are automatically cleaned up after 1 hour
- CORS is enabled for frontend integration

## Error Handling

The server provides detailed error messages for:

- Invalid report definitions
- Missing data
- Generation errors
- Invalid cache keys
- Unsupported formats

## Directory Structure

```
reportbro-server/
├── app.py              # Main application file
├── requirements.txt    # Python dependencies
├── temp_reports/      # Temporary report storage
├── venv/              # Virtual environment
└── README.md          # This file
```

## Development

The server runs in debug mode by default, which is suitable for development but should be disabled in production.

## Cache Management

- Reports are cached for 1 hour
- Cache is automatically cleaned for entries older than 1 hour
- Cache status can be monitored via the /api/report/cache endpoint
