"""
Kite Connect OAuth flow.

Access tokens expire every day at midnight IST.
Run `python main.py auth` each morning before the market opens.
The token is automatically saved to your .env file.

IMPORTANT — one-time setup in Zerodha Developer Console:
  1. Go to https://developers.kite.trade/apps
  2. Open your app settings
  3. Set Redirect URL to: http://127.0.0.1:5000/callback
  4. Save changes
"""
import os
import sys
import threading
import webbrowser
from datetime import datetime
from flask import Flask, request
from src.utils import get_logger
from config.settings import settings

log = get_logger("kite_auth")

app = Flask(__name__)
app.logger.disabled = True          # suppress Flask request logs
_shutdown_event = threading.Event()
_token_result = {}


@app.route("/")
def index():
    from src.broker import KiteClient
    kite = KiteClient(settings.KITE_API_KEY, settings.KITE_API_SECRET)
    login_url = kite.get_login_url()
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>Zerodha Login</title>
  <style>
    body {{ font-family: Arial, sans-serif; display:flex; align-items:center;
            justify-content:center; height:100vh; margin:0; background:#f5f5f5; }}
    .card {{ background:white; padding:40px; border-radius:12px;
             box-shadow:0 4px 16px rgba(0,0,0,0.1); text-align:center; max-width:400px; }}
    .btn {{ display:inline-block; margin-top:20px; padding:14px 32px;
            background:#387ed1; color:white; text-decoration:none;
            border-radius:6px; font-size:16px; font-weight:bold; }}
    .btn:hover {{ background:#2d6bbf; }}
    p {{ color:#555; line-height:1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Trade Agent</h2>
    <p>Click below to log in with your Zerodha account.<br>
       Your access token will be saved automatically.</p>
    <a href="{login_url}" class="btn">Login with Zerodha</a>
  </div>
</body>
</html>"""


@app.route("/callback")
def callback():
    request_token = request.args.get("request_token")
    status = request.args.get("status", "")

    if status == "cancelled" or not request_token:
        _shutdown_event.set()
        return "<h2>Login cancelled. You can close this tab.</h2>"

    try:
        from src.broker import KiteClient
        kite = KiteClient(settings.KITE_API_KEY, settings.KITE_API_SECRET)
        access_token = kite.generate_session(request_token)

        _update_env("KITE_ACCESS_TOKEN", access_token)
        _token_result["access_token"] = access_token
        _token_result["generated_at"] = datetime.now().isoformat()

        log.info(f"Access token saved — valid until midnight IST today")

        html = f"""<!DOCTYPE html>
<html>
<head>
  <title>Login Successful</title>
  <style>
    body {{ font-family: Arial, sans-serif; display:flex; align-items:center;
            justify-content:center; height:100vh; margin:0; background:#f5f5f5; }}
    .card {{ background:white; padding:40px; border-radius:12px;
             box-shadow:0 4px 16px rgba(0,0,0,0.1); text-align:center; max-width:420px; }}
    .token {{ font-family:monospace; background:#f0f0f0; padding:10px;
              border-radius:4px; font-size:12px; word-break:break-all; margin:15px 0; }}
    .ok {{ color:#2e7d32; font-size:48px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="ok">✓</div>
    <h2>Authentication Successful!</h2>
    <p>Token saved to <code>.env</code> — you can close this tab.</p>
    <p>Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <div class="token">{access_token}</div>
    <p style="color:#888; font-size:13px;">Token expires at midnight IST. Run <code>python main.py auth</code> each morning.</p>
  </div>
</body>
</html>"""
        # Notify Telegram that auth succeeded
        threading.Thread(target=_notify_auth_success, daemon=True).start()
        # Shutdown server after a short delay
        threading.Timer(1.5, _shutdown_event.set).start()
        return html

    except Exception as e:
        log.error(f"Auth callback failed: {e}")
        return f"<h2>Error: {e}</h2><p>Check your API key/secret and try again.</p>", 500


def _notify_auth_success():
    try:
        from src.notifications import TelegramNotifier
        notifier = TelegramNotifier()
        notifier.send(
            "✅ <b>Zerodha Auth Successful</b>\n\n"
            "Token refreshed and saved.\n"
            "Market scan will run at 09:00 IST.\n\n"
            "Send /scan to run a scan right now."
        )
    except Exception:
        pass


def _update_env(key: str, value: str):
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    lines = []
    found = False

    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)


def run_auth_server(port: int = 5000):
    from rich.console import Console
    from rich.panel import Panel
    console = Console()

    if not settings.KITE_API_KEY or not settings.KITE_API_SECRET:
        console.print("[red]ERROR: KITE_API_KEY and KITE_API_SECRET must be set in .env[/red]")
        console.print("\nGet your keys from: https://developers.kite.trade/apps")
        sys.exit(1)

    console.print(Panel(
        "[bold]One-time Zerodha Developer Console setup[/bold]\n\n"
        "1. Go to [link]https://developers.kite.trade/apps[/link]\n"
        "2. Click your app → Edit\n"
        f"3. Set [yellow]Redirect URL[/yellow] to: [cyan]http://127.0.0.1:{port}/callback[/cyan]\n"
        "4. Save — then come back here and press Enter\n\n"
        "[dim]Skip if you already did this step[/dim]",
        title="Setup (do once)",
        border_style="yellow"
    ))
    input("Press Enter when ready...")

    console.print(f"\n[cyan]Starting auth server on http://127.0.0.1:{port}[/cyan]")
    console.print("[dim]Opening browser automatically...[/dim]\n")

    # Open browser after short delay
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    # Run Flask in background thread
    server_thread = threading.Thread(
        target=lambda: app.run(port=port, debug=False, use_reloader=False),
        daemon=True
    )
    server_thread.start()

    # Wait until token received or user cancels
    _shutdown_event.wait(timeout=300)  # 5 min timeout

    if _token_result.get("access_token"):
        console.print("[green]Access token saved to .env successfully![/green]")
        console.print("[dim]Token is valid until midnight IST — run 'python main.py auth' each morning[/dim]")
        return _token_result["access_token"]
    else:
        console.print("[yellow]Auth timed out or was cancelled[/yellow]")
        return None
