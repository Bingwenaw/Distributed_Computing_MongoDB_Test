"""Dashboard jobs and explicit, reversible local/shared switching."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import threading
import time
from cluster_config import LOCAL, load_linked
from lab import Actor, EventLog, Settings, topology
from setup_lab import setup
from link_setup import start_shared, check_hosts
from faults import control


class Dashboard:
    def __init__(self):
        self.cluster = LOCAL
        self.target = None
        self.connection_state = "disconnected"
        self.message = "Local mode — disconnected from the shared cluster."
        self.log = EventLog()
        self.actors = [Actor("A", self.log, LOCAL), Actor("B", self.log, LOCAL)]
        self.jobs = {}
        self.lock = threading.RLock()
        self.control = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-control")
        self.diagnostics = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-status")
        self.cancel = threading.Event()
        self.connection_cancel = threading.Event()
        self.status = []
        self.status_job = None
        self.status_cluster = None
        self.status_time = 0
        self.log.emit("dashboard_started", defaults=asdict(Settings()), cluster=LOCAL.name)

    def busy(self, key):
        job = self.jobs.get(key)
        return job is not None and not job.done()

    @property
    def switching(self):
        return self.connection_state in ("connecting", "disconnecting")

    @property
    def blocked(self):
        return self.switching or self.busy("connection") or self.connection_state == "error"

    def submit(self, key, function, *args, **kwargs):
        with self.lock:
            if self.blocked or self.busy("suite") or self.busy("reset") or self.busy(key):
                raise ValueError("Wait for the active operation or connection change first.")
            if key in ("suite", "reset") and any(not job.done() for job in self.jobs.values()):
                raise ValueError("Wait for active commands, experiments, and fault controls first.")
            if key == "reset" and self.cluster.linked:
                raise ValueError("Shared data reset is disabled.")
            if key == "suite":
                self.cancel.clear()
            executor = self.actors[0 if key == "A" else 1].executor if key in ("A", "B") else self.control
            self.jobs[key] = executor.submit(function, *args, **kwargs)

    def request_connection(self, connect):
        with self.lock:
            if self.switching or any(not job.done() for job in self.jobs.values()):
                raise ValueError("Finish active operations first. Stop experiments and wait for recovery before switching.")
            if connect and self.connection_state != "disconnected":
                raise ValueError("Return to local mode before connecting again.")
            target = load_linked() if connect else self.target
            self.connection_cancel.clear()
            self.connection_state = "connecting" if connect else "disconnecting"
            self.message = "Preparing connection…" if connect else "Returning to the local lab…"
            self.jobs["connection"] = self.control.submit(self.switch, connect, target)

    def progress(self, message):
        self.message = message
        self.log.emit("connection_progress", message=message)

    def new_actors(self, cluster, preserve="connection"):
        self.cluster = cluster
        self.actors = [Actor("A", self.log, cluster), Actor("B", self.log, cluster)]
        self.jobs = {key: job for key, job in self.jobs.items() if key == preserve}
        self.status = []
        self.status_time = 0

    def switch(self, connect, target):
        local_stopped = False
        try:
            if connect:
                # Missing hosts/config must not interrupt an otherwise working local lab.
                check_hosts(target)
                self.target = target
            for actor in self.actors:
                actor.close()
            self.actors = []
            if connect:
                self.progress("Pausing this copy's local lab; its data remains saved…")
                local_stopped = True
                control.stop_cluster(LOCAL)
                start_shared(target, self.connection_cancel, self.progress)
                with self.lock:
                    self.new_actors(target)
                    self.connection_state = "connected"
                self.progress("Connected — nine shared nodes. Local data is saved separately.")
            else:
                if target is not None:
                    control.stop_cluster(target)
                setup()
                with self.lock:
                    self.new_actors(LOCAL)
                    self.target = None
                    self.connection_state = "disconnected"
                self.progress("Disconnected — local lab restored. Shared volumes are preserved.")
        except Exception as exc:
            reason = str(exc)
            try:
                if connect and not local_stopped and self.actors:
                    with self.lock:
                        self.connection_state = "disconnected"
                        self.target = None
                else:
                    if self.target is not None:
                        control.stop_cluster(self.target)
                    setup()
                    with self.lock:
                        self.new_actors(LOCAL)
                        self.target = None
                        self.connection_state = "disconnected"
                self.progress(f"Connection change failed: {reason}. Local mode is available.")
            except Exception as recovery:
                self.connection_state = "error"
                self.progress(f"Connection change failed: {reason}. Local recovery also failed: {recovery}. "
                              "Use Return to local mode after fixing the problem.")
        self.log.emit("connection_finished", state=self.connection_state, cluster=self.cluster.name)
        return self.message

    def clear_database(self):
        if self.cluster.linked:
            raise ValueError("Shared reset is disabled.")
        try:
            for actor in self.actors:
                actor.close()
            return control.clear_data(self.log, confirmed=True, cluster=LOCAL)
        finally:
            with self.lock:
                self.new_actors(LOCAL, preserve="reset")

    def refresh_status(self):
        if self.status_job is not None and self.status_job.done():
            result = self.status_job.result()
            if self.status_cluster == self.cluster:
                self.status = result
                self.log.emit("topology", category="diagnostic", cluster=self.cluster.name, nodes=result)
            self.status_job = None
        if not self.blocked and self.status_job is None and time.monotonic() - self.status_time >= 3:
            self.status_cluster = self.cluster
            self.status_job = self.diagnostics.submit(topology, self.cluster)
            self.status_time = time.monotonic()
