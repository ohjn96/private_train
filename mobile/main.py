#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KTX/SRT Train Reservation - Android APK Entry Point
Kivy app that runs Flask server locally and displays it in Android WebView.
"""
import os
import sys
import threading
import time
import socket

# Set up paths - ensure project root is in sys.path
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# Flask configuration
os.environ['FLASK_ENV'] = 'production'
os.environ['FLASK_DEBUG'] = 'false'

FLASK_HOST = '127.0.0.1'
FLASK_PORT = 5050


def start_flask_server():
    """Start Flask server in background thread."""
    try:
        from app import create_app
        flask_app = create_app()
        flask_app.run(
            host=FLASK_HOST,
            port=FLASK_PORT,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        print(f"Flask server error: {e}")


def is_server_ready(timeout=1):
    """Check if Flask server is accepting connections."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((FLASK_HOST, FLASK_PORT))
        sock.close()
        return result == 0
    except Exception:
        return False


# --- Kivy App ---
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.clock import Clock
from kivy.utils import platform
from kivy.core.window import Window


class TrainReservationApp(App):
    """Main Kivy application that hosts Flask + WebView."""

    def build(self):
        self.title = '열차 예약'
        Window.clearcolor = (0.96, 0.96, 0.96, 1)

        # Loading screen
        self.layout = BoxLayout(orientation='vertical')
        self.label = Label(
            text='[b]열차 예약 시스템[/b]\n서버를 시작하는 중...',
            markup=True,
            font_size='18sp',
            color=(0.3, 0.3, 0.3, 1),
            halign='center',
            valign='middle'
        )
        self.label.bind(size=self.label.setter('text_size'))
        self.layout.add_widget(self.label)

        # Start Flask server in background thread
        self.server_thread = threading.Thread(target=start_flask_server, daemon=True)
        self.server_thread.start()

        # Poll for server readiness
        Clock.schedule_interval(self._check_server, 0.5)

        return self.layout

    def _check_server(self, dt):
        """Poll until Flask server is ready, then launch WebView."""
        if is_server_ready():
            # Server is ready - stop polling
            Clock.unschedule(self._check_server)
            self.label.text = '[b]열차 예약 시스템[/b]\n로딩 중...'

            if platform == 'android':
                # Small delay to ensure server is fully ready
                Clock.schedule_once(lambda dt: self._open_android_webview(), 0.5)
            else:
                # Desktop fallback: open in browser
                import webbrowser
                webbrowser.open(f'http://{FLASK_HOST}:{FLASK_PORT}')
                self.label.text = '[b]열차 예약 시스템[/b]\n브라우저에서 열렸습니다'

    def _open_android_webview(self):
        """Set up Android WebView to display Flask app."""
        try:
            from jnius import autoclass
            from android.runnable import run_on_ui_thread

            @run_on_ui_thread
            def create_webview():
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                WebView = autoclass('android.webkit.WebView')
                WebViewClient = autoclass('android.webkit.WebViewClient')
                WebSettings = autoclass('android.webkit.WebSettings')
                LinearLayout = autoclass('android.widget.LinearLayout')
                LayoutParams = autoclass('android.view.ViewGroup$LayoutParams')
                Color = autoclass('android.graphics.Color')
                KeyEvent = autoclass('android.view.KeyEvent')

                activity = PythonActivity.mActivity

                # Create WebView
                webview = WebView(activity)
                ws = webview.getSettings()
                ws.setJavaScriptEnabled(True)
                ws.setDomStorageEnabled(True)
                ws.setLoadWithOverviewMode(True)
                ws.setUseWideViewPort(True)
                ws.setCacheMode(WebSettings.LOAD_NO_CACHE)
                ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW)

                # Enable viewport meta tag support
                ws.setBuiltInZoomControls(True)
                ws.setDisplayZoomControls(False)

                webview.setWebViewClient(WebViewClient())
                webview.setBackgroundColor(Color.WHITE)

                # Layout
                params = LayoutParams(
                    LayoutParams.MATCH_PARENT,
                    LayoutParams.MATCH_PARENT
                )

                layout = LinearLayout(activity)
                layout.setOrientation(LinearLayout.VERTICAL)
                layout.addView(webview, params)

                activity.setContentView(layout)
                webview.loadUrl(f'http://{FLASK_HOST}:{FLASK_PORT}')

                # Store reference for back button handling
                self._webview = webview

            create_webview()

        except Exception as e:
            self.label.text = f'WebView 오류: {str(e)}'
            print(f"WebView error: {e}")

    def on_pause(self):
        """Handle app pause (Android)."""
        return True

    def on_resume(self):
        """Handle app resume (Android)."""
        pass


if __name__ == '__main__':
    TrainReservationApp().run()
