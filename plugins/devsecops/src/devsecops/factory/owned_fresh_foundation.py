"""Process-bound fresh RKE2 component; never a retained guest adoption path.

Canonical provisioning and cleanup still require their current journal mutation
and exact installed input/package identities. Receipts do not restore a handle.
"""

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import base64
import math
import os
import ipaddress
import re
from pathlib import Path
import shlex
import shutil
import socket
import stat
import subprocess
import time
from uuid import uuid4

from devsecops.runtime.cli import _new_directory, _write
from . import fresh_foundation_recipe as recipes, fresh_foundation_remote as remote, qemu_acceleration, registry_remote, tcg
from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .native_codex import _package
from .owned_linux import _plain
from .state import RunJournal, StateError, _json, _read, _safe_path, _sha

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
MODULES = (
    "owned_fresh_foundation",
    "fresh_foundation_recipe",
    "fresh_foundation_remote",
    "registry_remote",
    "tcg",
    "cleanup_observation",
    "qemu_acceleration",
)
BOOTSTRAP = """import base64,json,sys,types
p=json.loads(sys.stdin.buffer.read(2000001))
def load(name,raw):
 m=types.ModuleType(name);sys.modules[name]=m
 exec(compile(base64.b64decode(raw,validate=True),name,'exec'),m.__dict__)
 return m
try:
 s=load('devsecops_fresh_support',p.pop('support_source'))
 r=load('devsecops_fresh_recipes',p.pop('recipe_source'))
 c=load('devsecops_fresh_cleanup',p.pop('cleanup_source'))
 a=load('devsecops_qemu_acceleration',p.pop('acceleration_source'))
 d=load('devsecops_fresh_driver',p.pop('driver_source'))
 v=d.execute(p,s,r,c,accelerator=a)
 print(json.dumps({'binding':p['binding'],'operation_id':p['operation_id'],'observation':v},sort_keys=True))
except Exception:
 print('{"error":"fresh-operation-unobserved-reconciliation-required"}')
 sys.exit(2)
"""


@dataclass(frozen=True)
class FreshTransport:
    transport: dict
    original_ca_sha256: str | None = None  # Compatibility field for the fixed stateless TLS reader only.


class _ResidentSSHForward:
    """One original multiplexed forward; saved dictionaries cannot restore it."""

    def __init__(self, session, role, host, remote_port, port):
        self.session, self.role = session, role
        self.host, self.remote_port, self.port = host, remote_port, port
        self.closed, self.unknown, self.close_attempted = False, True, False
        self.specification = f"127.0.0.1:{port}:{host}:{remote_port}"

    def __reduce__(self):
        raise TypeError("Resident SSH forwards cannot be serialized")

    def _admit(self):
        if self.session.forwards.get(self.role) is not self:
            raise StateError("Original resident SSH forward required")

    def audit(self, deadline):
        self._admit()
        if self.closed or self.unknown:
            raise StateError("Resident SSH forward is closed or unresolved")
        self.session.audit(deadline)

    def close(self, deadline):
        self._admit()
        if self.closed:
            return
        if self.close_attempted or self.unknown:
            raise StateError("Resident SSH forward needs reconciliation; cancellation is not repeated")
        self.audit(deadline)
        self.close_attempted, self.unknown = True, True
        self.session.record("cancel-intent-" + self.role, {"forward": self.specification})
        argv = self.session.argv(forwarding=True)
        argv[-1:-1] = ["-O", "cancel", "-L", self.specification]
        tcg._run(argv, deadline)
        self.session.audit(deadline)
        self.closed, self.unknown = True, False
        self.session.record("forward-closed-" + self.role, {"forward": self.specification, "cancel_acknowledged": True})


class _ResidentSSHSession:
    """One host-bound authentication for commands and all three owned forwards.

    Multiplexed clients have no authentication or network fallback. Their
    process, private directory and control socket must remain the originals.
    """

    def __init__(self, owner):
        if type(owner) is not OwnedFreshRKE2Foundation or not owner._resident_transport_enabled:
            raise StateError("Resident SSH requires its explicitly enabled original foundation")
        owner._private_guard()
        self.owner, self.pid = owner, os.getpid()
        self.directory = owner._private / "host-session"
        self.directory.mkdir(mode=0o700)
        info = self.directory.stat()
        self.directory_identity = (info.st_dev, info.st_ino)
        self.control = self.directory / "control"
        if len(str(self.control).encode()) > 100:
            raise StateError("Resident SSH control path exceeds Unix socket bounds")
        self.process = self.process_hash = self.control_identity = None
        self.started = self.closed = self.stop_attempted = False
        self.forwards, self.pins = {}, {}
        self.transport = owner._effective_transport()
        executable = shutil.which("ssh")
        if executable is None:
            raise StateError("The installed OpenSSH client is unavailable")
        self.executable = Path(executable).resolve()
        self.executable_sha256 = _sha(tcg._public_read(self.executable, 16000000))

    def __reduce__(self):
        raise TypeError("Resident SSH sessions cannot be serialized")

    def guard(self):
        if self.pid != os.getpid() or self.owner._host_session is not self:
            raise StateError("Resident SSH requires its original process and owner")
        self.owner._private_guard()
        info = tcg._private(self.directory).stat()
        if (info.st_dev, info.st_ino) != self.directory_identity:
            raise StateError("Resident SSH private directory changed")
        current = self.owner._effective_transport()
        if any(current[k] != self.transport[k] for k in self.transport if k not in ("config", "certificate")):
            raise StateError("Resident SSH route or client identity changed")
        if _sha(tcg._public_read(self.executable, 16000000)) != self.executable_sha256:
            raise StateError("Resident OpenSSH client changed")
        for path, pin in self.pins.items():
            if _sha(_read(path, 128000)) != pin:
                raise StateError("Resident SSH original evidence changed")

    def record(self, name, value):
        self.guard()
        path = self.directory / (name + ".json")
        tcg._write(path, tcg._canonical({"run_id": self.owner._journal_run_id, **value}))
        self.pins[path] = _sha(_read(path, 128000))
        return path

    def identity_now(self, deadline):
        if self.process is None or self.process.poll() is not None:
            return None
        result = tcg._run(["ps", "-p", str(self.process.pid), "-o", "lstart=", "-o", "command="],
                          deadline, check=False)
        if result.returncode or not result.stdout.strip():
            raise StateError("Original resident SSH process identity is unobserved")
        return _sha(result.stdout)

    def argv(self, *, forwarding=False):
        # The false proxy also blocks a new TCP connection if the control socket
        # disappears between the original-identity check and OpenSSH's connect.
        return [str(self.executable), "-F", "/dev/null", "-S", str(self.control),
                "-o", "ControlMaster=no", "-o", "ControlPersist=no",
                "-o", "ProxyCommand=/usr/bin/false", "-o", "CanonicalizeHostname=no",
                "-o", "BatchMode=yes", "-o", "PubkeyAuthentication=no",
                "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
                "-o", "GSSAPIAuthentication=no", "-o", "HostbasedAuthentication=no",
                "-o", "IdentityAgent=none", "-o", "IdentityFile=none",
                "-o", "ForwardAgent=no", "-o", "PermitLocalCommand=no",
                "-o", "ClearAllForwardings=" + ("no" if forwarding else "yes"),
                "-o", "ConnectTimeout=1", "retained"]

    def start(self, deadline):
        self.guard()
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
            raise StateError("Resident SSH authentication deadline expired or is invalid")
        if self.started:
            raise StateError("Resident SSH authentication was already attempted")
        self.started = True
        bounded = min(deadline, time.monotonic() + 40)
        receipts_before = set((self.owner._private / "receipts").glob("ssh-agent-*.json"))
        try:
            with tcg._ssh_context(FreshTransport(self.transport), bounded, self.owner._private / "receipts",
                                  control=self.control, forwarding=True) as argv:
                argv[0] = str(self.executable)
                argv[-1:-1] = ["-N", "-o", "ControlPersist=no", "-o", "ServerAliveInterval=15",
                               "-o", "ServerAliveCountMax=2"]
                self.record("start-intent", {"argv": argv, "authentication_attempts_allowed": 1})
                fd = os.open(self.directory / "ssh.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                                    start_new_session=True)
                finally:
                    os.close(fd)
                self.process_hash = self.identity_now(bounded)
                self.record("process", {"pid": self.process.pid, "process_sha256": self.process_hash})
                while time.monotonic() < bounded:
                    if self.process.poll() is not None:
                        raise StateError("Resident SSH authentication did not establish")
                    if self.control.exists():
                        info = self.control.lstat()
                        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                            raise StateError("Resident SSH control socket identity differs")
                        self.control_identity = (info.st_dev, info.st_ino)
                        self.audit(bounded)
                        break
                    time.sleep(0.05)
                else:
                    raise StateError("Resident SSH authentication exceeded its deadline")
            agent_receipts = set((self.owner._private / "receipts").glob("ssh-agent-*.json")) - receipts_before
            if self.transport.get("agent_protocol", "native") == "certificate-view-v1":
                if len(agent_receipts) != 1:
                    raise StateError("Resident SSH lacks its original certificate-agent receipt")
                path = next(iter(agent_receipts))
                raw = _read(path, 128000)
                audit = _json(raw)
                if audit.get("status") != "command_completed" or (audit.get("agent_audit") or {}).get("signatures") != 1:
                    raise StateError("Resident SSH lacks its one observed host-bound signature")
                self.pins[path] = _sha(raw)
            self.record("established", {"pid": self.process.pid, "process_sha256": self.process_hash,
                                        "authentication_connections_started": 1, "automatic_reauthentication": False,
                                        "agent_receipts": [{"path": str(p), "sha256": self.pins[p]} for p in agent_receipts if p in self.pins]})
        except BaseException:
            # No remote command or VM effect has been dispatched at this point.
            self.close(time.monotonic() + 15, allow_unestablished=True)
            raise

    def audit(self, deadline):
        self.guard()
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
            raise StateError("Resident SSH command deadline expired or is invalid")
        if self.closed or self.process_hash is None or self.identity_now(deadline) != self.process_hash:
            raise StateError("Resident SSH connection disappeared or changed; no reauthentication attempted")
        try:
            info = self.control.lstat()
        except OSError:
            raise StateError("Resident SSH control socket disappeared; no reauthentication attempted") from None
        if not stat.S_ISSOCK(info.st_mode) or (info.st_dev, info.st_ino) != self.control_identity:
            raise StateError("Resident SSH control socket changed")
        argv = self.argv()
        argv[-1:-1] = ["-O", "check"]
        tcg._run(argv, deadline)

    @contextmanager
    def command(self, deadline):
        if not self.started:
            self.start(deadline)
        self.audit(deadline)
        yield self.argv()
        self.audit(deadline)

    def forward(self, role, host, remote_port, deadline):
        self.audit(deadline)
        if role not in ("kubernetes", "gitlab", "registry") or role in self.forwards:
            raise StateError("Resident SSH forward role is invalid or already attempted")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise StateError("Resident SSH forwards require an observed private IPv4 target") from None
        owned_network = address.version == 4 and any(address in ipaddress.ip_network(network)
            for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))
        if (not owned_network
                or type(remote_port) is not int or not 1024 < remote_port < 65536
                or role == "kubernetes" and remote_port != 6443
                or role != "kubernetes" and host != "127.0.0.1"):
            raise StateError("Resident SSH forward target differs from its owned scope")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        item = _ResidentSSHForward(self, role, host, remote_port, port)
        self.forwards[role] = item
        self.record("forward-intent-" + role, {"forward": item.specification})
        argv = self.argv(forwarding=True)
        argv[-1:-1] = ["-O", "forward", "-o", "ExitOnForwardFailure=yes", "-L", item.specification]
        tcg._run(argv, deadline)
        self.audit(deadline)
        item.unknown = False
        self.record("forward-established-" + role, {"forward": item.specification, "acknowledged": True})
        return item

    def close(self, deadline, *, allow_unestablished=False):
        self.guard()
        if self.closed:
            if self.process is not None and self.process.poll() is None:
                raise StateError("Closed resident SSH process is still alive")
            return
        if any(not item.closed for item in self.forwards.values()):
            raise StateError("Resident SSH forwards must be closed before their shared connection")
        if self.stop_attempted:
            raise StateError("Resident SSH stop is unresolved; no duplicate signal")
        if self.process is not None and self.process.poll() is None:
            observed = self.identity_now(deadline)
            if self.process_hash is not None and observed != self.process_hash:
                raise StateError("Refusing to stop a replaced resident SSH process")
            if self.process_hash is None and not allow_unestablished:
                raise StateError("Resident SSH original process is unobserved")
            self.stop_attempted = True
            self.record("stop-intent", {"pid": self.process.pid, "process_sha256": self.process_hash})
            self.process.terminate()
            try:
                self.process.wait(timeout=max(0.001, min(15, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                raise StateError("Resident SSH shutdown remains unobserved") from None
        if self.control.exists() or self.control.is_symlink():
            info = self.control.lstat()
            if not stat.S_ISSOCK(info.st_mode) or (info.st_dev, info.st_ino) != self.control_identity:
                raise StateError("Resident SSH residual socket changed")
            self.control.unlink()
        self.closed = True
        self.record("closed", {"pid": self.process.pid if self.process else None, "process_absent": True,
                               "control_socket_absent": True, "automatic_reauthentication": False})


class _FreshTunnel:
    """Reuse only fixed transport/API functions, never retained adoption/state."""

    def __init__(self, directory, transport, ca_sha256, *, owner):
        if type(owner) is not OwnedFreshRKE2Foundation:
            raise StateError("Fresh tunnel requires its original component owner")
        self.directory = directory
        self.spec = FreshTransport(transport, ca_sha256)
        self.owner = owner
        self._shared_forward = None

    def _transport_spec(self):
        return replace(self.spec, transport=self.owner._effective_transport())

    _process_identity = tcg.RetainedTCGDriver._process_identity
    def _tunnel(self, address, operation, deadline):
        if not self.owner.resident_transport_enabled:
            return tcg.RetainedTCGDriver._tunnel(self, address, operation, deadline)
        if operation != self.owner._up_operation:
            raise StateError("Kubernetes forward differs from the original provisioning operation")
        if self._shared_forward is None:
            previous = self.owner._host_session.forwards.get("kubernetes")
            try:
                self._shared_forward = self.owner._host_session.forward("kubernetes", address, 6443, deadline)
            finally:
                if self._shared_forward is None and previous is None:
                    self._shared_forward = self.owner._host_session.forwards.get("kubernetes")
        self._shared_forward.audit(deadline)
        if self._shared_forward.host != address:
            raise StateError("Original Kubernetes forward destination changed")
        session = self._shared_forward.session
        return {"port": self._shared_forward.port, "address": address, "operation_id": operation,
                "pid": session.process.pid, "process_sha256": session.process_hash,
                "control": str(session.control), "shared_authenticated_connection": True}

    def _close_tunnel(self, deadline):
        if not self.owner.resident_transport_enabled:
            return tcg.RetainedTCGDriver._close_tunnel(self, deadline)
        if self._shared_forward is not None:
            self._shared_forward.close(deadline)
        return {"owned_tunnel_absent": True, "shared_connection_retained_for_cleanup": True}
    _observe_tunnel_intent = tcg.RetainedTCGDriver._observe_tunnel_intent
    _api = tcg.RetainedTCGDriver._api


class OwnedFreshRKE2Foundation:
    def __init__(self, journal, *, plugin_id, plugin_version):
        if type(journal) is not RunJournal:
            raise StateError("Fresh foundation requires the original canonical journal")
        self.journal, self.plugin_id, self.plugin_version = journal, plugin_id, plugin_version
        self.pid = os.getpid()
        self._up_operation, self._down_operation, self._binding = None, None, None
        self._private, self._private_identity, self._tunnel = None, None, None
        self._credentials = {}
        self._observations, self._bootstrap_observations = ComponentObservations(), ComponentObservations()
        self._origin = None
        self._link_outcome = None
        self._remote_identity = None
        self._remote_dispatched = False
        self._selected_recipe = None
        self._renewals, self._renewal_attempts = [], []
        self._renewal_active = False
        self._resident_transport_enabled = False
        self._host_session = None
        self._journal_identities = dict(journal.identities)
        self._journal_run_id = journal.state["run_id"]

    @property
    def resident_transport_enabled(self):
        if self._host_session is not None and not self._resident_transport_enabled:
            raise StateError("Resident SSH selection changed after authentication")
        return self._resident_transport_enabled

    def enable_resident_transport(self):
        if self.pid != os.getpid() or self._up_operation is not None or self._resident_transport_enabled:
            raise StateError("Resident SSH must be selected once before original provisioning")
        self._resident_transport_enabled = True

    @contextmanager
    def command_context(self, selected_transport, journal, deadline, audit_directory):
        transport = self.current_transport_for(selected_transport, journal)
        if not self.resident_transport_enabled:
            with tcg._ssh_context(FreshTransport(transport), deadline, audit_directory) as argv:
                yield argv
            return
        if self._host_session is None:
            self._host_session = _ResidentSSHSession(self)
        with self._host_session.command(deadline) as argv:
            yield argv

    def close_resident_transport(self, deadline):
        if self._host_session is None:
            return None
        self._host_session.close(deadline)
        return PhaseOutcome("incomplete", "original-ssh-transport-closed", tuple(self._host_session.pins),
                            mutation_effect="not_applied")

    def _admit(self, context, action, operation_id=None):
        if (
            type(context) is not PhaseContext
            or self.pid != os.getpid()
            or not self.journal._lifecycle_locked
            or type(context.deadline) not in (float, int)
            or not math.isfinite(context.deadline)
            or time.monotonic() >= context.deadline
        ):
            raise StateError("Fresh foundation requires its current bounded process context")
        state = self.journal.state
        life = state.get("lifecycle", {})
        phase = _plain(context.phase)
        expected = phase.get("operation") if action in ("reconcile", "credentials") else action
        if (
            expected not in ("up", "down")
            or phase.get("operation") != expected
            or context.run_id != state["run_id"]
            or context.run_dir != self.journal.run_dir
            or _plain(context.identities) != self.journal.identities
            or _plain(context.input_artifacts) != life.get("inputs")
            or context.execution_mode != life.get("execution_mode")
            or life.get("driver_id") != "owned-linux"
            or phase not in life.get("phases", [])
            or (life.get("active_phase") or {}).get("phase_id") != phase.get("id")
            or life.get("final_result") is not None
        ):
            raise StateError("Fresh foundation differs from the active canonical operation")
        current = self.journal.current_mutation(phase["id"], expected, reconciliation=action == "reconcile")
        if operation_id is not None and operation_id != current:
            raise StateError("Fresh reconciliation operation differs from the journal")
        paths = {name: self.journal._artifact_path(ref["path"]) for name, ref in life["inputs"].items()}
        _, docs = _load_inputs(paths, self.journal.identities, state["environment_id"])
        if docs != _plain(context.inputs):
            raise StateError("Fresh selected inputs changed")
        config, platform = docs["config"], docs["lock"]["platform"]
        recipe = recipes.validate(config["runtime"]["foundation"])
        if (
            config["profile"] != "bigbang-live"
            or config["runtime"]["architecture"] != "amd64"
            or "cleanup" not in config["allowed_mutations"]
            or action == "up"
            and "provision" not in config["allowed_mutations"]
            or any(
                platform.get(k) != v
                for k, v in {
                    "os": "linux",
                    "architecture": "amd64",
                    "distribution": "rke2",
                    "distribution_version": recipes.VERSION,
                    "kubernetes_version": recipes.VERSION,
                    "cni": "canal",
                }.items()
            )
        ):
            raise StateError("Fresh provisioning needs the fixed RKE2 reference and recorded scope")
        if action == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
            config["expires_at"].replace("Z", "+00:00")
        ):
            raise StateError("Fresh provisioning authorization expired")
        manifest, members = _package(
            PACKAGE_ROOT,
            self.journal.identities["package_sha256"],
            self.plugin_id,
            self.plugin_version,
            context.deadline,
        )
        from . import cleanup_observation

        modules = {
            "owned_fresh_foundation": Path(__file__),
            "fresh_foundation_recipe": Path(recipes.__file__),
            "fresh_foundation_remote": Path(remote.__file__),
            "registry_remote": Path(registry_remote.__file__),
            "tcg": Path(tcg.__file__),
            "cleanup_observation": Path(cleanup_observation.__file__),
            "qemu_acceleration": Path(qemu_acceleration.__file__),
        }
        hashes = {}
        for name, file in modules.items():
            target = PACKAGE_ROOT / "src/devsecops/factory" / (name + ".py")
            digest = _sha(_read(file, 500000))
            if file.resolve() != target or members.get(str(target.relative_to(PACKAGE_ROOT))) != digest:
                raise StateError("Fresh implementation is outside its exact installed package")
            hashes[name] = digest
        binding = recipes.binding(
            recipe,
            run_id=context.run_id,
            environment_id=state["environment_id"],
            identities=dict(context.identities),
            modules=hashes,
        )
        if self._binding is not None and binding != self._binding:
            raise StateError("Fresh ownership binding changed")
        return current, recipe, hashes, binding, config, {"sha256": _sha(manifest), "files": len(members)}

    def _validate_transport(self, recipe):
        """Only the closed approved snapshot syntax, never arbitrary SSH config."""
        transport = recipe["transport"]
        raw = {}
        for name in ("config", "known_hosts", "certificate", "identity", "proxy"):
            reader = tcg._public_read if name == "proxy" else tcg._read
            raw[name] = reader(transport[name]["path"])
            if _sha(raw[name]) != transport[name]["sha256"]:
                raise StateError("Fresh pinned SSH transport changed")
        try:
            config = raw["config"].decode()
            users = re.findall(r"^  User ([A-Za-z0-9][A-Za-z0-9_-]{0,63})$", config, re.MULTILINE)
            proxies = re.findall(r"^  ProxyCommand (.+)$", config, re.MULTILINE)
            if len(users) != 1 or len(proxies) != 1:
                raise ValueError
            proxy = shlex.split(proxies[0])
            if (
                not proxy
                or proxy[0] != transport["proxy"]["path"]
                or len(proxy) > 17
                or any(not re.fullmatch(r"[A-Za-z0-9_./:%-]{1,256}", part) for part in proxy[1:])
            ):
                raise ValueError
            known = raw["known_hosts"].strip().split()
            if (
                len(known) != 3
                or known[0].decode() != recipe["host"]
                or known[1] != b"ssh-ed25519"
                or _sha(base64.b64decode(known[2], validate=True)) != transport["host_key_sha256"]
            ):
                raise ValueError

            def quote(value):
                if any(char in value for char in ("\n", "\r", '"', "\\", "%")):
                    raise ValueError
                return '"' + value + '"'

            expected = "\n".join(
                [
                    "Host retained",
                    "  HostName " + recipe["host"],
                    "  User " + users[0],
                    "  Port 22",
                    "  IdentityFile " + quote(transport["identity"]["path"]),
                    "  IdentitiesOnly yes",
                    "  IdentityAgent " + quote(transport["identity_agent"]),
                    "  CertificateFile " + quote(transport["certificate"]["path"]),
                    "  UserKnownHostsFile " + quote(transport["known_hosts"]["path"]),
                    "  GlobalKnownHostsFile /dev/null",
                    "  HostKeyAlgorithms ssh-ed25519",
                    "  StrictHostKeyChecking yes",
                    "  UpdateHostKeys no",
                    "  VerifyHostKeyDNS no",
                    "  ForwardAgent no",
                    "  PermitLocalCommand no",
                    "  RequestTTY no",
                    "  ControlPersist no",
                    "  ProxyCommand " + shlex.join(proxy),
                    "",
                ]
            )
            if config != expected:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise StateError("Fresh SSH route differs from the closed selected snapshot") from None

    def current_transport_for(self, selected_transport, journal):
        """Share only this original journal's exact selected transport generation.

        Registered service/registry components retain their own action admission;
        this capability is transport continuity only, never a mutation grant.
        """
        if (
            journal is not self.journal
            or type(journal) is not RunJournal
            or self.pid != os.getpid()
            or not journal._lifecycle_locked
            or journal.identities != self._journal_identities
            or journal.state["run_id"] != self._journal_run_id
            or journal.state.get("lifecycle", {}).get("final_result") is not None
        ):
            raise StateError("Shared fresh transport requires the original active journal")
        reference = journal.state.get("lifecycle", {}).get("inputs", {}).get("config")
        if type(reference) is not dict:
            raise StateError("Shared fresh transport requires its frozen factory config")
        raw = _read(journal._artifact_path(reference["path"]), 2_000_000)
        if _sha(raw) != self._journal_identities["config_sha256"]:
            raise StateError("Shared fresh transport config identity changed")
        recipe = recipes.validate(_json(raw)["runtime"]["foundation"])
        if type(selected_transport) is not dict or selected_transport != recipe["transport"]:
            raise StateError("Component transport differs from the original fresh route selection")
        if self._selected_recipe is None:
            if self._up_operation is not None or self._renewals or self._private is not None:
                raise StateError("Fresh transport preparation has inconsistent resident ownership")
            self._validate_transport(recipe)
            return deepcopy(recipe["transport"])
        if self._selected_recipe != recipe or self._binding is None:
            raise StateError("Original fresh transport recipe or binding changed")
        return self._effective_transport()

    def _effective_transport(self):
        """Original resident renewal chain; serialized records never restore it."""
        if self.pid != os.getpid() or self._selected_recipe is None:
            raise StateError("Fresh transport requires the original creating process")
        self._private_guard()
        self._renewal_artifacts()
        self._validate_transport(self._selected_recipe)
        active = deepcopy(self._selected_recipe["transport"])
        original_grant = None
        for entry in self._renewals:
            if original_grant is None:
                original_grant = tcg._certificate_grant(tcg._read(active["certificate"]["path"]))
            if (
                _sha(tcg._read(entry["receipt"])) != entry["receipt_sha256"]
                or _sha(tcg._read(entry["intent"])) != entry["intent_sha256"]
            ):
                raise StateError("Fresh authenticated renewal receipt changed")
            generation = tcg._private(entry["directory"]).stat()
            if (generation.st_dev, generation.st_ino) != entry["directory_identity"]:
                raise StateError("Fresh certificate generation changed")
            candidate = deepcopy(entry["transport"])
            if entry["previous_certificate_sha256"] != active["certificate"]["sha256"] or any(
                candidate[key] != active[key] for key in active if key not in ("certificate", "config")
            ):
                raise StateError("Fresh renewal changed the selected route, host or client identity")
            self._validate_transport({**self._selected_recipe, "transport": candidate})
            grant = tcg._certificate_grant(tcg._read(candidate["certificate"]["path"]))
            if grant["grant_sha256"] != original_grant["grant_sha256"]:
                raise StateError("Fresh renewal grant differs from original issuer and identity")
            active = candidate
        return active

    def _renewal_artifacts(self):
        paths = []
        for entry in self._renewal_attempts:
            current = tcg._private(entry["directory"]).stat()
            if (current.st_dev, current.st_ino) != entry["directory_identity"]:
                raise StateError("Fresh renewal audit directory changed")
            for path, digest in entry.get("pins", {}).items():
                if _sha(tcg._read(path)) != digest:
                    raise StateError("Fresh renewal attempt evidence changed")
            paths.extend(path for path in (entry["intent"], entry["receipt"]) if path in entry["pins"])
        return tuple(paths)

    def _transport_evidence(self):
        self._renewal_artifacts()
        active = self._effective_transport()
        return {
            "initial_certificate_sha256": self._selected_recipe["transport"]["certificate"]["sha256"],
            "current_certificate_sha256": active["certificate"]["sha256"],
            "recipe_identity_changed": False,
            "resident_connection": None if self._host_session is None else {
                "pid": self._host_session.process.pid if self._host_session.process else None,
                "process_sha256": self._host_session.process_hash,
                "closed": self._host_session.closed,
                "automatic_reauthentication": False,
                "artifacts": [{"path": str(p), "sha256": h} for p, h in self._host_session.pins.items()],
            },
            "renewals": [
                {
                    "receipt_name": row["receipt"].name,
                    "intent_name": row["intent"].name,
                    "intent_sha256": row["intent_sha256"],
                    "receipt_sha256": row["receipt_sha256"],
                    "operation_id": row["operation_id"],
                    "previous_certificate_sha256": row["previous_certificate_sha256"],
                    "certificate_sha256": row["transport"]["certificate"]["sha256"],
                }
                for row in self._renewals
            ],
        }

    def renew_certificate(self, context, issued_certificate, *, minimum_validity_seconds=1800):
        """Admit an already-issued same-grant certificate under the current action.

        Issuance and action-time approval stay with the normal caller workflow.
        This method only uses the original frozen route and an authenticated
        read-only `true` probe; it cannot extend the recipe grant or run cleanup.
        """
        operation, recipe, hashes, binding, config, package = self._admit(context, "reconcile")
        if (
            self._up_operation is None
            or self._renewal_active
            or len(self._renewal_attempts) >= 32
            or type(minimum_validity_seconds) is not int
            or not 60 <= minimum_validity_seconds <= 86400
        ):
            raise StateError("Fresh renewal requires the original bounded nonconcurrent action")
        if context.phase["operation"] == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
            config["expires_at"].replace("Z", "+00:00")
        ):
            raise StateError("Fresh provisioning grant expired before renewal")
        self._private_guard()
        if self._selected_recipe != recipe or binding != self._binding:
            raise StateError("Fresh renewal cannot change the original recipe")
        active = self._effective_transport()
        selected = _safe_path(Path(issued_certificate))
        if not selected.is_absolute() or selected.is_relative_to(self._private):
            raise StateError("Fresh issued certificate must be an explicit separate input")
        raw = tcg._public_read(selected, tcg.LIMIT)
        grant = tcg._certificate_grant(raw)
        original = tcg._certificate_grant(tcg._read(recipe["transport"]["certificate"]["path"]))
        if grant["grant_sha256"] != original["grant_sha256"] or _sha(raw) == active["certificate"]["sha256"]:
            raise StateError("Fresh certificate must renew the exact original issuer, key and grant")

        def lifetime():
            if context.phase["operation"] == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
                config["expires_at"].replace("Z", "+00:00")
            ):
                raise StateError("Fresh provisioning grant expired during renewal")
            now = time.time()
            if (
                not grant["valid_after"] <= now
                or grant["valid_before"] - now < minimum_validity_seconds
                or time.monotonic() >= context.deadline
            ):
                raise StateError("Fresh renewed certificate cannot cover its bounded action")

        lifetime()
        identifier = operation + "-" + uuid4().hex
        parent = tcg._private(self._private / "transport-generations", create=True)
        directory = parent / identifier
        directory.mkdir(mode=0o700)
        info = directory.stat()
        receipt = self._private / "receipts" / ("fresh-transport-renewal-" + identifier + ".json")
        intent = self._private / "receipts" / ("fresh-transport-renewal-" + identifier + "-intent.json")
        report = {
            "schema_version": 1,
            "kind": "fresh-foundation-certificate-renewal",
            "run_id": context.run_id,
            "operation_id": operation,
            "up_operation_id": self._up_operation,
            "binding_sha256": binding,
            "identities": dict(context.identities),
            "modules": hashes,
            "package": package,
            "execution_mode": context.execution_mode,
            "previous_certificate_sha256": active["certificate"]["sha256"],
            "certificate_sha256": _sha(raw),
            "certificate_grant": grant,
            "original_grant_sha256": original["grant_sha256"],
            "original_identity_sha256": original["identity_sha256"],
            "initial_certificate_sha256": recipe["transport"]["certificate"]["sha256"],
            "minimum_validity_seconds": minimum_validity_seconds,
            "recipe_identity_changed": False,
            "owner_binding_changed": False,
            "remote_command": "true",
            "certificate_only_probe": True,
            "status": "unobserved",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        tcg._write(intent, recipes.canonical(report))
        self._renewal_attempts.append(
            {
                "intent": intent,
                "receipt": receipt,
                "directory": directory,
                "directory_identity": (info.st_dev, info.st_ino),
                "pins": {intent: _sha(tcg._read(intent))},
            }
        )
        attempt = self._renewal_attempts[-1]
        self._renewal_active = True
        try:
            transport = tcg._renewed_transport(recipe["transport"], directory, raw)
            self._validate_transport({**recipe, "transport": transport})
            lifetime()
            probe_deadline = min(context.deadline, time.monotonic() + 30)
            with tcg._ssh_context(
                FreshTransport(transport), probe_deadline, self._private / "receipts"
            ) as argv:
                tcg._run([*argv, "true"], probe_deadline)
            lifetime()
            self._admit(context, "reconcile", operation)
            self._private_guard()
            if self._effective_transport() != active:
                raise StateError("Fresh active transport changed during renewal")
            report.update(
                status="authenticated",
                transport=transport,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            tcg._write(receipt, recipes.canonical(report))
            entry = {
                "receipt": receipt,
                "receipt_sha256": _sha(tcg._read(receipt)),
                "intent": intent,
                "intent_sha256": _sha(tcg._read(intent)),
                "directory": directory,
                "directory_identity": (info.st_dev, info.st_ino),
                "operation_id": operation,
                "previous_certificate_sha256": active["certificate"]["sha256"],
                "transport": deepcopy(transport),
            }
            self._renewals.append(entry)
            self._effective_transport()
            return receipt
        except BaseException:
            if not receipt.exists():
                report.update(
                    status="rejected-or-unobserved", finished_at=datetime.now(timezone.utc).isoformat()
                )
                tcg._write(receipt, recipes.canonical(report))
            raise
        finally:
            self._renewal_active = False
            if receipt.exists():
                attempt["pins"][receipt] = _sha(tcg._read(receipt))
            for name in ("client-certificate.pub", "ssh-config"):
                path = directory / name
                if path.exists():
                    attempt["pins"][path] = _sha(tcg._read(path))
            self._renewal_artifacts()

    def _dispatch(self, context, action, operation, recipe, hashes, binding):
        self._admit(
            context,
            "reconcile" if action == "reconcile" else context.phase["operation"],
            operation if action == "reconcile" else None,
        )
        packet = {
            "action": action,
            "operation_id": operation,
            "run_id": context.run_id,
            "environment_id": self.journal.state["environment_id"],
            "identities": dict(context.identities),
            "modules": hashes,
            "binding": binding,
            "recipe": recipe,
            "timeout": min(3600, max(0.001, context.deadline - time.monotonic() - 5)),
            "expires_at": context.inputs["config"]["expires_at"],
            "expected_root_identity": self._remote_identity,
        }
        for field, module, name in [
            ("support_source", tcg, "tcg"),
            ("recipe_source", recipes, "fresh_foundation_recipe"),
            ("driver_source", remote, "fresh_foundation_remote"),
            ("cleanup_source", registry_remote, "registry_remote"),
            ("acceleration_source", qemu_acceleration, "qemu_acceleration"),
        ]:
            raw = _read(Path(module.__file__), 500000)
            if _sha(raw) != hashes[name]:
                raise StateError("Fresh transported source changed")
            packet[field] = base64.b64encode(raw).decode()
        raw = recipes.canonical(packet)
        if len(raw) > 2_000_000:
            raise StateError("Fresh dispatch exceeds its packet bound")
        with self.command_context(
            recipe["transport"], self.journal, context.deadline, self._private / "receipts"
        ) as argv:
            self._remote_dispatched = True
            result = tcg._run(
                [*argv, shlex.join(["python3", "-I", "-B", "-c", BOOTSTRAP])],
                context.deadline,
                data=raw,
                check=False,
            )
        value = _json(result.stdout)
        if (
            result.returncode
            or value.get("binding") != binding
            or value.get("operation_id") != operation
            or "error" in value
        ):
            raise StateError("Fresh operation outcome is unobserved; reconcile without repeating it")
        actual = value["observation"]
        if (
            type(actual) is not dict
            or actual.get("binding") != binding
            or actual.get("operation_id") != operation
        ):
            raise StateError("Fresh direct observation differs from the current operation")
        return actual

    def _private_guard(self):
        if self._private is None:
            raise StateError("Fresh private generation was never created here")
        directory = tcg._private(self._private).stat()
        if (directory.st_dev, directory.st_ino) != self._private_identity:
            raise StateError("Fresh local private generation changed")
        if _json(_read(self._private / "owner.json", 64000)) != {
            "binding": self._binding,
            "up_operation_id": self._up_operation,
        }:
            raise StateError("Fresh local ownership changed")

    def _retain_credentials(self, raw, expected_ca):
        import yaml

        value = yaml.safe_load(raw)
        try:
            if len(value["clusters"]) != 1 or len(value["users"]) != 1 or len(value["contexts"]) != 1:
                raise ValueError
            cluster, user = value["clusters"][0]["cluster"], value["users"][0]["user"]
            if cluster["server"] != "https://127.0.0.1:6443" or set(user) != {
                "client-certificate-data",
                "client-key-data",
            }:
                raise ValueError
            ca = base64.b64decode(cluster["certificate-authority-data"], validate=True)
            cert = base64.b64decode(user["client-certificate-data"], validate=True)
            key = base64.b64decode(user["client-key-data"], validate=True)
            if _sha(ca) != expected_ca:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise StateError("Fresh guest credential identity differs") from None
        for name, contents in [
            ("ca.crt", ca),
            ("client.crt", cert),
            ("client.key", key),
            ("kubeconfig-source.yaml", raw),
        ]:
            self._private_guard()
            path = self._private / name
            tcg._write(path, contents)
            item = path.stat()
            self._credentials[name] = {"identity": (item.st_dev, item.st_ino), "sha256": _sha(contents)}

    def _remove_credentials(self):
        self._private_guard()
        for name, record in self._credentials.items():
            path = self._private / name
            item = path.lstat()
            if (
                not stat.S_ISREG(item.st_mode)
                or item.st_nlink != 1
                or item.st_uid != os.getuid()
                or (item.st_dev, item.st_ino) != record["identity"]
                or _sha(_read(path, 2_000_000)) != record["sha256"]
            ):
                raise StateError("Fresh local credential copy changed before deletion")
        for name in self._credentials:
            path = self._private / name
            path.unlink()
            if path.exists() or path.is_symlink():
                raise StateError("Fresh local credential removal is unobserved")
        self._credentials = {}
        descriptor = os.open(self._private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _perform(self, context, action, operation_id=None):
        operation, recipe, hashes, binding, config, package = self._admit(context, action, operation_id)
        if action == "up":
            if self._up_operation is not None:
                raise StateError("Fresh creation was already attempted")
            self._up_operation, self._binding = operation, binding
            self._selected_recipe = deepcopy(recipe)
            private = _safe_path(Path(recipe["private_state_directory"]))
            if any(
                private.is_relative_to(root) or root.is_relative_to(private)
                for root in (self.journal.run_dir, PACKAGE_ROOT)
            ):
                raise StateError("Fresh private state must be separate from run/package")
            # No preexisting local state can become this process's origin handle.
            private.mkdir(mode=0o700)
            self._private = private
            info = private.stat()
            self._private_identity = (info.st_dev, info.st_ino)
            tcg._write(
                private / "owner.json", recipes.canonical({"binding": binding, "up_operation_id": operation})
            )
            tcg._private(private / "receipts", create=True)
        elif self._up_operation is None:
            raise StateError("Fresh cleanup/reconciliation requires the original creating process")
        never_dispatched = action != "up" and not self._remote_dispatched
        if self._private is not None:
            self._private_guard()
        elif not never_dispatched:
            raise StateError("Fresh private origin is unavailable")
        if action == "down":
            if self._down_operation is not None:
                raise StateError("Fresh cleanup already attempted; use reconciliation")
            self._down_operation = operation
        if action == "reconcile" and operation not in (self._up_operation, self._down_operation):
            raise StateError("Fresh reconciliation does not match a resident attempt")
        output = self.journal.run_dir / ("fresh-foundation-" + uuid4().hex)
        with _new_directory(output) as directory:
            _write(
                directory,
                "intent.json",
                {
                    "kind": "fresh-foundation-intent",
                    "run_id": context.run_id,
                    "operation_id": operation,
                    "action": action,
                    "binding": binding,
                    "identities": dict(context.identities),
                    "modules": hashes,
                },
            )
            os.fsync(directory)
            tunnel_absent = self._tunnel is None
            if action == "down" and self._tunnel is not None:
                tunnel_absent = (
                    self._tunnel._close_tunnel(context.deadline).get("owned_tunnel_absent") is True
                )
            if never_dispatched:
                actual = {
                    "binding": binding,
                    "operation_id": operation,
                    "up_operation_id": self._up_operation,
                    "root_identity": None,
                    "execution_basis": "original-resident-never-dispatched",
                    "complete_cleanup": True,
                    "unresolved_operations": [],
                    "mutation_effect": "applied",
                    "resources": [],
                }
            else:
                actual = self._dispatch(context, action, operation, recipe, hashes, binding)
            identity = actual.get("root_identity")
            if not never_dispatched and (
                type(identity) is not dict
                or set(identity) != {"device", "inode"}
                or any(type(value) is not int or value < 0 for value in identity.values())
                or actual.get("up_operation_id") != self._up_operation
            ):
                raise StateError("Fresh observed generation differs from original create intent")
            if self._remote_identity is not None and identity != self._remote_identity:
                raise StateError("Fresh remote generation was replaced")
            if not never_dispatched:
                self._remote_identity = identity
            readiness = "unknown"
            if action == "up":
                if actual.get("state") != "ready" or actual.get("unresolved_operations"):
                    raise StateError("Fresh guest has unresolved readiness effects")
                api = actual.get("api") or {}
                issued = self._dispatch(context, "credentials", operation, recipe, hashes, binding)
                if issued.get("ca_sha256") != api.get("ca_sha256"):
                    raise StateError("Fresh issued CA differs from the observed guest")
                raw = base64.b64decode(issued["private_kubeconfig_b64"], validate=True)
                self._retain_credentials(raw, api["ca_sha256"])
                self._tunnel = _FreshTunnel(self._private, recipe["transport"], api["ca_sha256"], owner=self)
                tunnel = self._tunnel._tunnel(actual["address"], operation, context.deadline)
                tunnel_absent = False
                observations = []
                for _ in range(3):
                    row = self._tunnel._api(tunnel["port"], context.deadline)
                    wanted = {**api["fingerprint"], "ca_sha256": api["ca_sha256"]}
                    if row.get("ready") is not True or row.get("fingerprint") != wanted:
                        raise StateError("Independent fresh TLS/API identity or readiness differs")
                    observations.append(row)
                actual.update(
                    endpoint=f"https://127.0.0.1:{tunnel['port']}",
                    tunnel_identity=tunnel,
                    api_observations=observations,
                )
                self._origin = {
                    "kind": "fresh-created",
                    "generation": context.run_id,
                    "binding": binding,
                    "root_identity": actual["root_identity"],
                    "up_operation_id": operation,
                    "root_disk_creation": actual["root_disk_creation"],
                    "guest_freshness": actual["guest_freshness"],
                    "cluster_fingerprint": wanted,
                    "source_assets": actual["source_assets"],
                    "ssh_pki_basis": "operator-supplied-pins-with-run-created-copies",
                    "api_ca_basis": "issued-by-observed-new-guest",
                }
                readiness = "fresh-rke2-ready"
            if (
                action == "down"
                and actual.get("complete_cleanup") is True
                and tunnel_absent
                and self._private is not None
            ):
                self._remove_credentials()
            if action == "reconcile" and self._tunnel is not None:
                tunnel_path = self._private / "tunnel.json"
                if tunnel_path.exists():
                    saved = _json(_read(tunnel_path, 64000))
                    tunnel_absent = self._tunnel._process_identity(saved["pid"], context.deadline) is None
                else:
                    tunnel_absent = not (self._private / "tunnel-intent.json").exists()
            local_absent = not self._credentials and (
                self._private is None
                or not any(
                    (self._private / name).exists()
                    for name in ("ca.crt", "client.crt", "client.key", "kubeconfig-source.yaml")
                )
            )
            complete = actual.get("complete_cleanup") is True and tunnel_absent and local_absent
            resources = [
                *actual.get("resources", []),
                {
                    "kind": "local-api-tunnel",
                    "disposition": "remove",
                    "state": "absent" if tunnel_absent else "present",
                },
                {
                    "kind": "local-issued-credential-copies",
                    "disposition": "remove",
                    "state": "absent" if local_absent else "present",
                },
                {
                    "kind": "local-public-certificate-renewal-audit",
                    "identity": [
                        {"device": row["directory_identity"][0], "inode": row["directory_identity"][1]}
                        for row in self._renewal_attempts
                    ],
                    "disposition": "preserve",
                    "state": "preserved",
                },
                {
                    "kind": "local-ownership-and-audit-evidence",
                    "identity": {"device": self._private_identity[0], "inode": self._private_identity[1]}
                    if self._private_identity
                    else None,
                    "disposition": "preserve",
                    "state": "preserved",
                },
            ]
            report = {
                "schema_version": 1,
                "kind": "owned-fresh-rke2-foundation",
                "action": action,
                "run_id": context.run_id,
                "operation_id": operation,
                "phase_id": context.phase["id"],
                "identities": dict(context.identities),
                "execution_mode": context.execution_mode,
                "package": package,
                "binding_sha256": binding,
                "foundation_readiness": readiness,
                "origin": self._origin,
                "cluster_fingerprint": self._origin["cluster_fingerprint"] if self._origin else None,
                "transport_generations": self._transport_evidence() if self._private is not None else None,
                "resources": resources,
                "complete_cleanup": complete,
                "unresolved_operations": actual.get("unresolved_operations", []),
                "mutation_effect": "applied"
                if complete
                or readiness == "fresh-rke2-ready"
                or action == "reconcile"
                and context.phase["operation"] == "up"
                and actual.get("mutation_effect") == "applied"
                else "unknown",
                "canonical_success": False,
                "coverage": "fresh-rke2-foundation-only",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            self._admit(context, action, operation_id)
            if self._private is not None:
                self._private_guard()
            _write(directory, "remote-observation.json", actual)
            _write(directory, "foundation.json", report)
            os.fsync(directory)
            self._admit(context, action, operation_id)
            if self._private is not None:
                self._private_guard()
        outcome = PhaseOutcome(
            "incomplete",
            "owned-fresh-foundation-component-only",
            (
                output / "intent.json",
                output / "foundation.json",
                output / "remote-observation.json",
                *self._renewal_artifacts(),
                *(tuple(self._host_session.pins) if self._host_session is not None else ()),
            ),
            mutation_effect=report["mutation_effect"],
        )
        self._bootstrap_observations.retain(
            outcome, {"report": report, "actual": actual, "private_state_directory": str(self._private)}
        )
        retained = self._observations.retain(outcome, report)
        if action == "up":
            self._link_outcome = retained
        return retained

    def up(self, context):
        return self._perform(context, "up")

    def down(self, context):
        return self._perform(context, "down")

    def reconcile(self, context, operation_id):
        return self._perform(context, "reconcile", operation_id)

    def cleanup_observation(self, outcome):
        return self._observations.observe(outcome)

    def bootstrap_observation(self, outcome):
        return self._bootstrap_observations.observe(outcome)

    def validate_cleanup(self, receipts):
        raise StateError("Fresh component cannot release the whole canonical environment")

    def service_link_origin(self, context, *, reconciliation_operation=None):
        """Original live fresh generation; files alone cannot issue this handle."""
        from .owned_service_links import _endpoint_context

        _endpoint_context(self.journal, context, reconciliation_operation=reconciliation_operation)
        from .owned_services import _freeze

        if self._link_outcome is None or self._down_operation is not None or self._origin is None:
            raise StateError("Fresh service routing requires the original ready generation")
        # Read-only use is allowed across later phases; current caller admission
        # is performed by OwnedServiceLinks before invoking this accessor.
        if context.run_id != self.journal.state["run_id"] or context.identities != self.journal.identities:
            raise StateError("Fresh routing context differs from its generation")
        self.current_transport_for(self._selected_recipe["transport"], self.journal)
        value = self._bootstrap_observations.observe(self._link_outcome)
        actual = value["actual"]
        rows = {row["kind"]: row for row in actual["resources"]}
        if any(rows.get(role, {}).get("state") != "present" for role in ("runtime", "network")):
            raise StateError("Fresh routing original resource identity is incomplete")
        return _freeze(
            {
                "run_id": context.run_id,
                "identities": dict(context.identities),
                "binding": self._binding,
                "up_operation_id": self._up_operation,
                "generation": context.run_id,
                "root_identity": self._remote_identity,
                "root": recipes.generation(self._selected_recipe, context.run_id),
                "runtime_id": rows["runtime"]["identity"],
                "network_id": rows["network"]["identity"],
                "address": actual["address"],
                "cluster_uid": self._origin["cluster_fingerprint"]["namespaces"]["kube-system"],
                "recipe": deepcopy(self._selected_recipe),
                "root_disk_creation": deepcopy(self._origin["root_disk_creation"]),
                "source_assets": deepcopy(self._origin["source_assets"]),
                "guest_forward_source_sha256": _sha(remote.GUEST_SERVICE_FORWARD.encode()),
                "qemu_argv": remote._qemu(self._selected_recipe),
            }
        )
