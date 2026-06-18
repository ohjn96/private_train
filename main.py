#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KTX/SRT Train Reservation System - Unified Entry Point
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app import create_app

app = create_app()

if __name__ == '__main__':
    # 기본 포트를 5050으로 변경 (macOS AirPlay가 5000 사용)
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'

    print(f"Starting Train Reservation App on http://localhost:{port}")
    print("Press Ctrl+C to quit")

    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
