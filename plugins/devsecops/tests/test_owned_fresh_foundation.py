"""Real canonical journal/package admission; controlled external boundaries only."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from devsecops.factory import cleanup_observation, contracts
from devsecops.factory import fresh_foundation_recipe as recipes, fresh_foundation_remote as remote
from devsecops.factory import owned_fresh_foundation as fresh, qemu_acceleration, registry_remote, tcg
from devsecops.factory.lifecycle import PhaseContext, SCENARIO_ROOT, _readonly
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from tests.test_factory_contracts import config, factory_lock
from tests.test_factory_tcg import transport_inputs, issued_certificate  # noqa: F401
from tests.test_fresh_foundation import lab  # noqa: F401


@pytest.fixture
def component(lab, transport_inputs, tmp_path, monkeypatch):  # noqa: F811 - explicit imported pytest fixtures
    lab.recipe["transport"] = tcg.create_transport_snapshot(**transport_inputs)
    lab.recipe["host"] = transport_inputs["hostname"]
    home = tmp_path / "codex"
    package = home / "plugins/cache/devsecops-local/devsecops/0.1.0"
    modules = {
        m.__name__.rsplit(".", 1)[-1]: m
        for m in (fresh, recipes, remote, registry_remote, tcg, cleanup_observation, qemu_acceleration)
    }
    files = {
        "src/devsecops/factory/" + name + ".py": Path(module.__file__).read_bytes()
        for name, module in modules.items()
    }
    for name in ("scenario", "assertions"):
        files["demo/software-factory/scenarios/ironbank-mission-recovery-v1/" + name + ".json"] = (
            SCENARIO_ROOT / (name + ".json")
        ).read_bytes()
    files[".codex-plugin/plugin.json"] = _canonical({"name": "devsecops", "version": "0.1.0"})
    for name, raw in files.items():
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = "".join(f"{_sha(raw)}  {name}\n" for name, raw in sorted(files.items())).encode()
    (package / "SHA256SUMS").write_bytes(manifest)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(fresh, "PACKAGE_ROOT", package)
    for name, module in modules.items():
        monkeypatch.setattr(module, "__file__", str(package / "src/devsecops/factory" / (name + ".py")))
    # Exercise the precise proposed shared schema branch without editing root-owned contracts.
    monkeypatch.setitem(
        contracts.CONFIG_SCHEMA["properties"]["runtime"]["properties"],
        "foundation",
        recipes.schema(),
    )
    selected = config()
    selected.update(expires_at=lab.packet["expires_at"], allowed_mutations=["provision", "cleanup"])
    selected["runtime"]["foundation"] = lab.recipe
    lock = factory_lock()
    lock["platform"].update(
        distribution="rke2",
        distribution_version=recipes.VERSION,
        kubernetes_version=recipes.VERSION,
        cni="canal",
    )
    values = {
        "config": selected,
        "lock": lock,
        "environment": {"schema_version": 1, "environment_id": selected["environment"], "services": []},
    }
    for name in ("scenario", "assertions"):
        raw = files["demo/software-factory/scenarios/ironbank-mission-recovery-v1/" + name + ".json"]
        values[name] = json.loads(raw)
        lock[name + "_sha256"] = _sha(raw)
    raw = {name: _canonical(value) for name, value in values.items()}
    for name in ("scenario", "assertions"):
        raw[name] = files["demo/software-factory/scenarios/ironbank-mission-recovery-v1/" + name + ".json"]
    raw["package"] = manifest
    identities = {name + "_sha256": _sha(value) for name, value in raw.items()}
    journal = RunJournal.create(
        tmp_path / "journal", identities=identities, environment_id=selected["environment"]
    )
    journal.initialize_lifecycle(inputs=raw, execution_mode="controlled-test", driver_id="owned-linux")
    driver = fresh.OwnedFreshRKE2Foundation(
        journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0"
    )
    proof = tmp_path / "controlled.json"
    proof.write_bytes(b'{"controlled_test":true}')
    calls = []
    ca = b"-----BEGIN CERTIFICATE-----\ncontrolled-ca\n-----END CERTIFICATE-----\n"
    fingerprint = {
        "namespaces": {"default": "new-default", "kube-system": "new-system"},
        "nodes": {"devsecops-fresh": "new-node"},
        "ca_sha256": _sha(ca),
    }

    def encoded(value):
        return base64.b64encode(value).decode()

    kubeconfig = _canonical(
        {
            "clusters": [
                {"cluster": {"server": "https://127.0.0.1:6443", "certificate-authority-data": encoded(ca)}}
            ],
            "users": [
                {
                    "user": {
                        "client-certificate-data": encoded(b"controlled-cert"),
                        "client-key-data": encoded(b"controlled-private-key"),
                    }
                }
            ],
            "contexts": [{}],
        }
    )
    changes = {}

    def dispatch(context, action, operation, recipe, hashes, binding):
        driver._remote_dispatched = True
        calls.append(action)
        if action == "credentials":
            return {"private_kubeconfig_b64": encoded(kubeconfig), "ca_sha256": _sha(ca)}
        result = {
            "binding": binding,
            "operation_id": operation,
            "up_operation_id": driver._up_operation,
            "root_identity": {"device": 1, "inode": 10},
            "root_disk_creation": {
                "exclusive_generation": {"device": 1, "inode": 10},
                "initial_source_sha256": recipe["boot_image"]["sha256"],
                "helper_id": "1" * 64,
                "helper_exit_code": 0,
                "disk_identity": {"device": 1, "inode": 11},
                "virtual_bytes": 160 * 1024**3,
            },
            "guest_freshness": {"before_install": "rke2-config-and-data-absent", "seed_sha256": "f" * 64},
            "api": {
                "ca_sha256": _sha(ca),
                "fingerprint": {k: v for k, v in fingerprint.items() if k != "ca_sha256"},
            },
            "source_assets": {},
            "state": "ready" if action == "up" else "absent",
            "address": "172.19.0.2",
            "unresolved_operations": [],
            "complete_cleanup": action != "up",
            "resources": [],
            "mutation_effect": "applied",
        }
        result.update(deepcopy(changes))
        return result

    monkeypatch.setattr(driver, "_dispatch", dispatch)

    class Tunnel:
        def __init__(self, directory, transport, ca_hash, *, owner):
            self.directory = directory
            self.closed = False

        def _tunnel(self, address, operation, deadline):
            return {"port": 43210, "pid": 123, "process_sha256": "a" * 64, "address": address}

        def _api(self, port, deadline):
            return {"ready": True, "fingerprint": deepcopy(fingerprint), "tls_hostname_and_ca_verified": True}

        def _close_tunnel(self, deadline):
            self.closed = True
            return {"owned_tunnel_absent": True}

        def _process_identity(self, pid, deadline):
            return None if self.closed else "a" * 64

    monkeypatch.setattr(fresh, "_FreshTunnel", Tunnel)

    def context(action):
        phase = next(p for p in values["scenario"]["phases"] if p["operation"] == action)
        return PhaseContext(
            journal.state["run_id"],
            _readonly(phase),
            _readonly(journal.identities),
            _readonly(values),
            _readonly(journal.state["lifecycle"]["inputs"]),
            journal.run_dir,
            time.monotonic() + 60,
            "controlled-test",
        )

    @contextmanager
    def invocation():
        with journal.lifecycle_lock(), journal.mutation_lock(tmp_path / "locks"):
            for name in ("preflight", "prepare"):
                journal.begin_phase(name)
                journal.finish_phase(name, "completed", "controlled-only", receipt_artifacts=[proof])
            ctx = context("up")
            journal.begin_phase(ctx.phase["id"])
            op = journal.begin_mutation(ctx.phase["id"], "up", intent_artifacts=[proof])
            yield ctx, op

    def down_context(ctx, op, artifacts):
        journal.finish_mutation(op, receipt_artifacts=artifacts)
        journal.abort_lifecycle(ctx.phase["id"], "incomplete", "controlled-stop")
        journal.finish_phase(ctx.phase["id"], "incomplete", "controlled-stop", receipt_artifacts=artifacts)
        journal.begin_phase("export")
        journal.finish_phase("export", "completed", "controlled-only", receipt_artifacts=[proof])
        down = context("down")
        journal.begin_phase(down.phase["id"])
        journal.begin_mutation(down.phase["id"], "down", intent_artifacts=[proof])
        return down

    return SimpleNamespace(
        driver=driver,
        journal=journal,
        values=values,
        invocation=invocation,
        down_context=down_context,
        calls=calls,
        changes=changes,
        fingerprint=fingerprint,
        proof=proof,
        package=package,
    )


def test_actual_canonical_journal_and_direct_fresh_origin(component):
    with component.invocation() as (ctx, op):
        outcome = component.driver.up(ctx)
        observed = component.driver.bootstrap_observation(outcome)
        report = observed["report"]
        assert outcome.status == "incomplete" and outcome.mutation_effect == "applied"
        assert report["foundation_readiness"] == "fresh-rke2-ready"
        assert report["origin"]["up_operation_id"] == op and report["origin"]["generation"] == ctx.run_id
        assert report["execution_mode"] == "controlled-test" and report["canonical_success"] is False
        assert observed["actual"]["endpoint"] == "https://127.0.0.1:43210"
        assert report["cluster_fingerprint"] == component.fingerprint
        assert (
            next(row for row in report["resources"] if row["kind"] == "local-api-tunnel")["state"]
            == "present"
        )
        assert component.calls == ["up", "credentials"]
        assert "controlled-private-key" not in json.dumps(observed)
        down = component.down_context(ctx, op, outcome.artifacts)
        cleaned = component.driver.down(down)
        final = component.driver.cleanup_observation(cleaned)
        assert final["complete_cleanup"] and final["origin"] == report["origin"]
        assert not (component.driver._private / "client.key").exists()
        assert component.journal.state["environment_reserved"]
        with pytest.raises(StateError, match="cannot release"):
            component.driver.validate_cleanup(())


def test_resident_no_remote_dispatch_closes_early_failure_without_adoption(component):
    private = Path(component.values["config"]["runtime"]["foundation"]["private_state_directory"])
    private.mkdir(mode=0o700)
    (private / "operator-data").write_bytes(b"preserve")
    with component.invocation() as (ctx, op):
        with pytest.raises(FileExistsError):
            component.driver.up(ctx)
        assert component.calls == []
        down = component.down_context(ctx, op, [component.proof])
        outcome = component.driver.down(down)
        report = component.driver.cleanup_observation(outcome)
        assert report["complete_cleanup"] and report["origin"] is None
        assert component.calls == [] and (private / "operator-data").read_bytes() == b"preserve"
        remote_observation = json.loads(outcome.artifacts[2].read_bytes())
        assert remote_observation["execution_basis"] == "original-resident-never-dispatched"


def test_reconstructed_component_cannot_adopt_existing_result(component):
    with component.invocation() as (ctx, op):
        outcome = component.driver.up(ctx)
        clone = fresh.OwnedFreshRKE2Foundation(
            component.journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0"
        )
        with pytest.raises(StateError, match="original resident"):
            clone.bootstrap_observation(outcome)
        with pytest.raises(StateError, match="original resident"):
            component.driver.bootstrap_observation(replace(outcome))
        down = component.down_context(ctx, op, outcome.artifacts)
        with pytest.raises(StateError, match="original creating process"):
            clone.down(down)


def test_artifact_mutation_invalidates_direct_observation(component):
    with component.invocation() as (ctx, _):
        outcome = component.driver.up(ctx)
        outcome.artifacts[1].write_bytes(b"{}")
        with pytest.raises(StateError, match="evidence changed"):
            component.driver.bootstrap_observation(outcome)


def test_actual_independent_cluster_identity_must_match(component):
    component.changes["api"] = {
        "ca_sha256": component.fingerprint["ca_sha256"],
        "fingerprint": {"namespaces": {"kube-system": "different"}, "nodes": {"node": "different"}},
    }
    with component.invocation() as (ctx, _):
        with pytest.raises(StateError, match="TLS/API identity"):
            component.driver.up(ctx)
        assert component.driver._origin is None


def test_current_journal_lock_and_exact_input_identity_required(component):
    with component.invocation() as (ctx, _):
        with pytest.raises(StateError, match="active canonical"):
            component.driver.up(replace(ctx, execution_mode="live"))
        assert component.calls == []
        path = component.package / "src/devsecops/factory/fresh_foundation_remote.py"
        path.write_bytes(path.read_bytes() + b"\n# changed\n")
        with pytest.raises(StateError):
            component.driver.up(ctx)
        assert component.calls == []


def test_expired_provisioning_grant_cannot_dispatch(component):
    with component.invocation() as (ctx, _):
        with pytest.raises(StateError, match="bounded"):
            component.driver.up(replace(ctx, deadline=time.monotonic() - 1))
        assert component.calls == []


def test_closed_transport_rejects_added_configuration(component, monkeypatch):
    transport = component.values["config"]["runtime"]["foundation"]["transport"]
    recipe = component.values["config"]["runtime"]["foundation"]
    proxy = [transport["proxy"]["path"], "ssh", "--host", "%h"]
    import shlex

    hostkey = b"controlled-host-key"
    raw = {
        "known_hosts": recipe["host"].encode() + b" ssh-ed25519 " + base64.b64encode(hostkey) + b"\n",
        "certificate": b"controlled-certificate",
        "identity": b"controlled-identity",
        "proxy": b"controlled-proxy",
    }

    def q(v):
        return '"' + v + '"'

    raw["config"] = (
        "\n".join(
            [
                "Host retained",
                "  HostName " + recipe["host"],
                "  User operator",
                "  Port 22",
                "  IdentityFile " + q(transport["identity"]["path"]),
                "  IdentitiesOnly yes",
                "  IdentityAgent " + q(transport["identity_agent"]),
                "  CertificateFile " + q(transport["certificate"]["path"]),
                "  UserKnownHostsFile " + q(transport["known_hosts"]["path"]),
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
    ).encode()
    transport["host_key_sha256"] = _sha(hostkey)
    for name, value in raw.items():
        Path(transport[name]["path"]).write_bytes(value)
        transport[name]["sha256"] = _sha(value)
    component.driver._validate_transport(recipe)
    for suffix in (
        "Include /unapproved\n",
        "  LocalCommand touch /unapproved\n",
        "  IdentityFile /another\n",
    ):
        changed = raw["config"] + suffix.encode()
        Path(transport["config"]["path"]).write_bytes(changed)
        transport["config"]["sha256"] = _sha(changed)
        with pytest.raises(StateError, match="closed selected"):
            component.driver._validate_transport(recipe)


def test_changed_copied_credential_cannot_be_deleted(component):
    with component.invocation() as (ctx, op):
        outcome = component.driver.up(ctx)
        path = component.driver._private / "client.key"
        path.write_bytes(b"different-owned-identity")
        down = component.down_context(ctx, op, outcome.artifacts)
        with pytest.raises(StateError, match="credential copy changed"):
            component.driver.down(down)
        assert path.read_bytes() == b"different-owned-identity"
        assert component.journal.state["environment_reserved"]


def renewal_input(component, raw):
    path = component.proof.parent / "normal-issued-certificate.pub"
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def test_fresh_renewal_activates_only_after_same_grant_authenticated_probe(component, monkeypatch):
    with component.invocation() as (ctx, op):
        outcome = component.driver.up(ctx)
        original = deepcopy(component.driver._effective_transport())
        binding = component.driver._binding
        owner = (component.driver._private / "owner.json").read_bytes()
        issued = renewal_input(component, issued_certificate(serial=2, valid_before=int(time.time()) + 7200))
        commands = []

        def probe(argv, deadline, **kwargs):
            assert component.driver._effective_transport() == original
            assert argv[-2:] == ["retained", "true"]
            commands.append(argv)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(tcg, "_run", probe)
        receipt = component.driver.renew_certificate(ctx, issued)
        result = json.loads(receipt.read_bytes())
        assert result["status"] == "authenticated" and result["operation_id"] == op
        assert result["certificate_grant"]["grant_sha256"] == result["original_grant_sha256"]
        assert result["certificate_grant"]["identity_sha256"] == result["original_identity_sha256"]
        active = component.driver._effective_transport()
        assert active["certificate"]["sha256"] == _sha(issued.read_bytes())
        assert all(active[key] == original[key] for key in original if key not in ("config", "certificate"))
        assert (
            component.driver._binding == binding
            and (component.driver._private / "owner.json").read_bytes() == owner
        )
        assert len(commands) == 1
        down = component.down_context(ctx, op, outcome.artifacts)
        cleaned = component.driver.down(down)
        report = component.driver.cleanup_observation(cleaned)
        assert report["complete_cleanup"]
        chain = report["transport_generations"]
        assert chain["initial_certificate_sha256"] == original["certificate"]["sha256"]
        assert chain["current_certificate_sha256"] == active["certificate"]["sha256"]
        assert chain["renewals"][0]["receipt_sha256"] == _sha(receipt.read_bytes())
        assert receipt in cleaned.artifacts
        assert not chain["recipe_identity_changed"]
        with pytest.raises(StateError, match="original"):
            fresh.OwnedFreshRKE2Foundation(
                component.journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0"
            )._effective_transport()


@pytest.mark.parametrize(
    "field", ["key", "key_id", "principals", "critical", "extensions", "reserved", "issuer"]
)
def test_fresh_renewal_rejects_changed_grant_before_probe(component, monkeypatch, field):
    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        original = component.driver._effective_transport()
        issued = renewal_input(component, issued_certificate(serial=2, **{field: b"changed"}))
        monkeypatch.setattr(tcg, "_run", lambda *a, **k: pytest.fail("changed grant probed"))
        with pytest.raises(StateError, match="issuer, key and grant"):
            component.driver.renew_certificate(ctx, issued)
        assert component.driver._effective_transport() == original and component.driver._renewals == []


@pytest.mark.parametrize("after,before", [(-100, -1), (30, 7200), (-100, 90)])
def test_fresh_renewal_rejects_expired_future_or_short_certificate(component, monkeypatch, after, before):
    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        now = int(time.time())
        issued = renewal_input(
            component, issued_certificate(serial=2, valid_after=now + after, valid_before=now + before)
        )
        monkeypatch.setattr(tcg, "_run", lambda *a, **k: pytest.fail("invalid lifetime probed"))
        with pytest.raises(StateError, match="bounded action"):
            component.driver.renew_certificate(ctx, issued)
        assert component.driver._renewals == []


def test_fresh_failed_renewal_keeps_original_route_and_uncertain_probe_evidence(component, monkeypatch):
    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        original = component.driver._effective_transport()
        issued = renewal_input(component, issued_certificate(serial=2))

        def failure(*a, **kw):
            raise tcg.TCGError("controlled probe failed")

        monkeypatch.setattr(tcg, "_run", failure)
        with pytest.raises(tcg.TCGError):
            component.driver.renew_certificate(ctx, issued)
        assert component.driver._effective_transport() == original and not component.driver._renewals
        record = component.driver._renewal_attempts[0]
        assert json.loads(record["receipt"].read_bytes())["status"] == "rejected-or-unobserved"
        assert record["intent"].exists()


def test_fresh_renewal_chain_and_original_evidence_cannot_be_replaced(component, monkeypatch):
    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        monkeypatch.setattr(
            tcg, "_run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        )
        first = component.driver.renew_certificate(
            ctx, renewal_input(component, issued_certificate(serial=2))
        )
        second = component.driver.renew_certificate(
            ctx, renewal_input(component, issued_certificate(serial=3))
        )
        chain = component.driver._transport_evidence()["renewals"]
        assert len(chain) == 2 and chain[1]["previous_certificate_sha256"] == chain[0]["certificate_sha256"]
        assert first != second
        first.write_bytes(b"{}")
        with pytest.raises(StateError, match="evidence changed"):
            component.driver._effective_transport()


def test_remote_packet_keeps_initial_recipe_while_ssh_uses_renewed_certificate(component, monkeypatch):
    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        monkeypatch.setattr(
            tcg, "_run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        )
        component.driver.renew_certificate(ctx, renewal_input(component, issued_certificate(serial=2)))
        active = component.driver._effective_transport()
        op, recipe, hashes, binding, _, _ = component.driver._admit(ctx, "reconcile")

        def remote_read(argv, deadline, **kwargs):
            assert argv[argv.index("-F") + 1] == active["config"]["path"]
            packet = json.loads(kwargs["data"])
            assert packet["recipe"]["transport"] == recipe["transport"]
            assert packet["binding"] == binding
            return SimpleNamespace(
                returncode=0,
                stdout=_canonical(
                    {
                        "binding": binding,
                        "operation_id": op,
                        "observation": {"binding": binding, "operation_id": op},
                    }
                ),
                stderr=b"",
            )

        monkeypatch.setattr(tcg, "_run", remote_read)
        observed = fresh.OwnedFreshRKE2Foundation._dispatch(
            component.driver, ctx, "reconcile", op, recipe, hashes, binding
        )
        assert observed["operation_id"] == op


def test_fresh_renewal_requires_original_current_journal_context(component, monkeypatch):
    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        issued = renewal_input(component, issued_certificate(serial=2))
        monkeypatch.setattr(tcg, "_run", lambda *a, **k: pytest.fail("invalid context probed"))
        with pytest.raises(StateError, match="active canonical"):
            component.driver.renew_certificate(replace(ctx, execution_mode="operator"), issued)
        with pytest.raises(StateError, match="bounded"):
            component.driver.renew_certificate(replace(ctx, deadline=time.monotonic() - 1), issued)


def test_fresh_provisioning_grant_expiry_during_probe_does_not_activate_certificate(component, monkeypatch):
    from datetime import timedelta

    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        original = component.driver._effective_transport()
        issued = renewal_input(component, issued_certificate(serial=2))
        real = fresh.datetime

        class ExpiredClock(real):
            @classmethod
            def now(cls, tz=None):
                return real.fromisoformat(ctx.inputs["config"]["expires_at"]) + timedelta(seconds=1)

        def probe(*a, **kw):
            monkeypatch.setattr(fresh, "datetime", ExpiredClock)
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(tcg, "_run", probe)
        with pytest.raises(StateError, match="grant expired during renewal"):
            component.driver.renew_certificate(ctx, issued)
        assert component.driver._effective_transport() == original
        assert not component.driver._renewals


def test_rejected_renewal_evidence_tamper_blocks_later_transport(component, monkeypatch):
    with component.invocation() as (ctx, _):
        component.driver.up(ctx)
        issued = renewal_input(component, issued_certificate(serial=2))

        def failed(*a, **kw):
            raise tcg.TCGError("controlled read-only probe failed")

        monkeypatch.setattr(tcg, "_run", failed)
        with pytest.raises(tcg.TCGError):
            component.driver.renew_certificate(ctx, issued)
        component.driver._renewal_attempts[0]["receipt"].write_bytes(b"{}")
        with pytest.raises(StateError, match="attempt evidence changed"):
            component.driver._effective_transport()


def test_shared_fresh_transport_is_same_original_route_and_journal_before_and_after_up(
    component, monkeypatch
):
    with component.invocation() as (ctx, _):
        selected = deepcopy(component.values["config"]["runtime"]["foundation"]["transport"])
        prepared = component.driver.current_transport_for(selected, component.journal)
        assert prepared == selected
        prepared["host_key_sha256"] = "f" * 64
        assert component.driver.current_transport_for(selected, component.journal) == selected
        component.driver.up(ctx)
        monkeypatch.setattr(
            tcg, "_run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        )
        component.driver.renew_certificate(ctx, renewal_input(component, issued_certificate(serial=2)))
        shared = component.driver.current_transport_for(selected, component.journal)
        assert shared["certificate"] != selected["certificate"]
        assert all(shared[key] == selected[key] for key in selected if key not in ("certificate", "config"))
        with pytest.raises(StateError, match="original active journal"):
            component.driver.current_transport_for(selected, object())
        with pytest.raises(StateError, match="original fresh route"):
            component.driver.current_transport_for(
                {**selected, "host_key_sha256": "f" * 64}, component.journal
            )


def test_registry_uses_fixed_shared_current_certificate_without_rewriting_recipe(component, monkeypatch):
    from devsecops.factory import owned_registry, service_recipe

    with component.invocation() as (ctx, _):
        registry = owned_registry.OwnedOCIRegistry(
            component.journal,
            recipe_path=component.proof,
            recipe_sha256=_sha(component.proof.read_bytes()),
            plugin_id="devsecops@devsecops-local",
            plugin_version="0.1.0",
        )
        registry.bind_foundation_transport(component.driver)
        with pytest.raises(StateError, match="one original"):
            registry.bind_foundation_transport(component.driver)
        component.driver.up(ctx)
        monkeypatch.setattr(
            tcg, "_run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        )
        component.driver.renew_certificate(ctx, renewal_input(component, issued_certificate(serial=2)))
        selected = deepcopy(component.values["config"]["runtime"]["foundation"]["transport"])
        current = component.driver.current_transport_for(selected, component.journal)
        hashes = {
            name: _sha(Path(module.__file__).read_bytes())
            for name, module in [
                ("factory/tcg.py", tcg),
                ("factory/service_recipe.py", service_recipe),
                ("factory/registry_remote.py", registry_remote),
            ]
        }
        packet = {
            "binding": "a" * 64,
            "operation_id": "controlled",
            "package_modules": hashes,
            "recipe": {"transport": selected},
        }

        def dispatch(argv, deadline, **kwargs):
            assert argv[argv.index("-F") + 1] == current["config"]["path"]
            sent = json.loads(kwargs["data"])
            assert sent["recipe"]["transport"] == selected
            return SimpleNamespace(
                returncode=0,
                stdout=_canonical(
                    {"binding": "a" * 64, "operation_id": "controlled", "observation": {"controlled": True}}
                ),
                stderr=b"",
            )

        monkeypatch.setattr(tcg, "_run", dispatch)
        assert registry._dispatch(
            {"transport": selected}, packet, component.driver._private / "receipts", ctx.deadline
        ) == {"controlled": True}
        with pytest.raises(StateError, match="original fresh route"):
            registry._dispatch(
                {"transport": {**selected, "identity_agent": "/changed"}},
                packet,
                component.driver._private / "receipts",
                ctx.deadline,
            )


def test_shared_transport_cannot_restore_a_forged_foundation_handle(component):
    from devsecops.factory.owned_registry import OwnedOCIRegistry

    registry = OwnedOCIRegistry(
        component.journal,
        recipe_path=component.proof,
        recipe_sha256=_sha(component.proof.read_bytes()),
        plugin_id="devsecops@devsecops-local",
        plugin_version="0.1.0",
    )
    with pytest.raises(StateError, match="one original"):
        registry.bind_foundation_transport(
            {"transport": component.values["config"]["runtime"]["foundation"]["transport"]}
        )
