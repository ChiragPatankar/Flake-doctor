import random
import threading
import time


class ExportJob:
    def __init__(self, rows):
        self.rows = rows
        self.done = False
        self.result = None

    def _work(self):
        time.sleep(random.uniform(0.005, 0.035))  # stands in for writing to storage
        self.result = "\n".join(",".join(map(str, r)) for r in self.rows)
        self.done = True


def start_export(rows):
    """Start exporting in the background and return the job immediately."""
    job = ExportJob(rows)
    thread = threading.Thread(target=job._work, daemon=True)
    job.thread = thread
    thread.start()
    return job
