import threading
import shutil
import os
import webview
from app import app, get_model

class Api:
    def __init__(self):
        self.window = None

    def download(self, source_path, filename):
        if not self.window:
            return None
        result = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            directory=os.path.expanduser("~/Videos"),
            save_filename=filename
        )
        if result:
            dest = result[0] if isinstance(result, list) else result
            shutil.copy2(source_path, dest)
            os.remove(source_path)
            return dest
        return None

api = Api()

def start_flask(port=5000):
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    get_model("base")
    t = threading.Thread(target=start_flask, daemon=True)
    t.start()
    window = webview.create_window(
        "Subtitulador",
        "http://127.0.0.1:5000",
        width=1100,
        height=750,
        resizable=True,
        js_api=api,
    )
    api.window = window
    webview.start()
