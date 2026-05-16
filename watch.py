"""
Job Search Dashboard - File Watcher
=====================================
Usage:
    python3 watch.py [path/to/Jobs.ods]

If no argument is given, the script scans the current directory for the first
.ods file that is NOT named example.ods and watches that file.

On start, generate.py is run once immediately to produce dashboard.html.
After that, any modification to the watched file triggers a regeneration.

LibreOffice often fires multiple filesystem events for a single save, so the
watcher debounces: it waits 1 second after the first event before regenerating,
and ignores any further events that arrive within that window.

Press Ctrl+C to stop watching.
"""

import sys
import os
import time
import threading
import glob
from datetime import datetime

# Make the watchdog package findable even if it was installed to the user path.
sys.path.insert(0, "/home/tm/.local/lib/python3.9/site-packages")

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import generate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_ods_file():
    """Return the first .ods file in the current directory that is not example.ods."""
    for path in sorted(glob.glob("*.ods")):
        if os.path.basename(path).lower() != "example.ods":
            return os.path.abspath(path)
    return None


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Event handler with debounce
# ---------------------------------------------------------------------------

class OdsHandler(FileSystemEventHandler):
    """Regenerate dashboard.html when the watched ODS file is modified.

    Uses a threading.Timer to debounce: multiple events within DEBOUNCE_SECONDS
    are collapsed into a single regeneration call.
    """

    DEBOUNCE_SECONDS = 1.0

    def __init__(self, ods_path):
        super().__init__()
        self.ods_path = os.path.abspath(ods_path)
        self._timer = None
        self._lock = threading.Lock()

    def _is_target(self, event_path):
        return os.path.abspath(event_path) == self.ods_path

    def on_modified(self, event):
        if not event.is_directory and self._is_target(event.src_path):
            self._schedule_regeneration()

    def on_created(self, event):
        # LibreOffice sometimes replaces the file entirely on save.
        if not event.is_directory and self._is_target(event.src_path):
            self._schedule_regeneration()

    def _schedule_regeneration(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.DEBOUNCE_SECONDS, self._regenerate)
            self._timer.daemon = True
            self._timer.start()

    def _regenerate(self):
        print(f"[{timestamp()}] Change detected — regenerating dashboard.html ...")
        try:
            generate.main(self.ods_path)
            print(f"[{timestamp()}] dashboard.html updated.")
        except (Exception, SystemExit) as exc:
            print(f"[{timestamp()}] ERROR during generation: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Determine which ODS file to watch.
    if len(sys.argv) > 1:
        ods_path = os.path.abspath(sys.argv[1])
        if not os.path.exists(ods_path):
            print(f"Error: file not found: {ods_path}")
            sys.exit(1)
    else:
        ods_path = find_ods_file()
        if ods_path is None:
            print("Error: no .ods file found in the current directory (other than example.ods).")
            print("Usage: python3 watch.py [Jobs.ods]")
            sys.exit(1)

    print(f"[{timestamp()}] Watching: {ods_path}")

    # Initial generation.
    print(f"[{timestamp()}] Running initial generation ...")
    try:
        generate.main(ods_path)
        print(f"[{timestamp()}] dashboard.html created.")
    except Exception as exc:
        print(f"[{timestamp()}] ERROR during initial generation: {exc}")

    # Set up the observer.
    handler = OdsHandler(ods_path)
    observer = Observer()
    watch_dir = os.path.dirname(ods_path)
    observer.schedule(handler, path=watch_dir, recursive=False)
    observer.start()

    print(f"[{timestamp()}] Watching for changes. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n[{timestamp()}] Stopping watcher. Goodbye.")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
