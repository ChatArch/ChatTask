"""Server-side evidence policy with synthetic Kubernetes API responses."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


# Load only this module while other package files are developed concurrently.
path = Path(__file__).resolve().parents[1] / 'src/chatsandbox/evidence.py'
spec = importlib.util.spec_from_file_location('chatsandbox_evidence_test_module', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Reader, EvidenceError = module.KubernetesEvidenceReader, module.KubernetesEvidenceError
ID = 'a' * 32
META = {'purpose': 'chattask', 'durable_run_id': 'b' * 32}


class ApiError(Exception):
    def __init__(self, status):
        self.status = status


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


@pytest.fixture
def state(monkeypatch):
    labels = {module.ID_LABEL: ID, **META}
    workload = {'metadata': {'name': ID, 'namespace': 'opensandbox', 'uid': 'workload-uid', 'labels': labels,
                            'annotations': {'opensandbox.io/secure-access-token': 'synthetic-private-route-token'}},
                'spec': {'replicas': 1}}
    pod = {'metadata': {'name': ID + '-0', 'namespace': 'opensandbox', 'uid': 'pod-uid',
                        'creationTimestamp': stamp(-30), 'labels': {**labels, module.PARENT_LABEL: ID},
                        'ownerReferences': [{'apiVersion': module.GROUP + '/' + module.VERSION,
                                             'kind': 'BatchSandbox', 'name': ID, 'uid': 'workload-uid', 'controller': True}]},
           'spec': {'nodeName': 'hitk', 'automountServiceAccountToken': False,
                    'containers': [{'name': 'main', 'image': 'python:3.11-slim', 'resources': {
                        'requests': {'cpu': '1', 'memory': '512Mi'}, 'limits': {'cpu': '1000m', 'memory': '536870912'}}}],
                    'initContainers': [{'name': 'execd-init', 'resources': {
                        'requests': {'cpu': '50m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}}}],
                    'volumes': [{'name': 'opensandbox-bin', 'emptyDir': {}}]},
           'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                      'containerStatuses': [{'name': 'main', 'ready': True, 'imageID': 'image@sha256:synthetic'}]}}
    fact = {'version': 1, 'source': 'kubelet-config+cgroup-baseline', 'limit': 128,
            'node_uid': 'node-uid', 'boot_id': 'boot-id', 'enforced_since': stamp(-300), 'verified_at': stamp()}
    node = {'metadata': {'uid': 'node-uid', 'annotations': {module.PID_ANNOTATION: json.dumps(fact)}},
            'status': {'nodeInfo': {'bootID': 'boot-id'}}}
    state = SimpleNamespace(workload=workload, pods=[pod], node=node, workloads=[workload], get_error=None,
                            pod_error=None, pod_continue=None, workload_continue=None, config_calls=[])
    state.configuration = SimpleNamespace(api_key={}, refresh_api_key_hook=lambda _: None)

    class Core:
        def __init__(self, client):
            pass
        def list_namespaced_pod(self, **kwargs):
            assert kwargs['namespace'] == 'opensandbox' and kwargs['_request_timeout'] == (3, 10)
            if state.pod_error:
                raise state.pod_error
            return {'items': copy.deepcopy(state.pods), 'metadata': {'continue': state.pod_continue}}
        def read_node(self, **kwargs):
            assert kwargs['name'] == 'hitk'
            return copy.deepcopy(state.node)

    class Custom:
        def __init__(self, client):
            pass
        def get_namespaced_custom_object(self, **kwargs):
            assert kwargs['name'] == ID and kwargs['namespace'] == 'opensandbox'
            if state.get_error:
                raise state.get_error
            return copy.deepcopy(state.workload)
        def list_namespaced_custom_object(self, **kwargs):
            return {'items': copy.deepcopy(state.workloads), 'metadata': {'continue': state.workload_continue}}

    package = ModuleType('kubernetes')
    package.client = SimpleNamespace(ApiClient=lambda: SimpleNamespace(sanitize_for_serialization=lambda x: x,
                                                                      configuration=state.configuration),
                                     CoreV1Api=Core, CustomObjectsApi=Custom)
    package.config = SimpleNamespace(load_incluster_config=lambda: state.config_calls.append('incluster'),
                                     load_kube_config=lambda **kw: state.config_calls.append(kw['config_file']))
    client = ModuleType('kubernetes.client')
    exceptions = ModuleType('kubernetes.client.exceptions'); exceptions.ApiException = ApiError
    monkeypatch.setitem(sys.modules, 'kubernetes', package)
    monkeypatch.setitem(sys.modules, 'kubernetes.client', client)
    monkeypatch.setitem(sys.modules, 'kubernetes.client.exceptions', exceptions)
    return state


def test_valid_runtime_evidence_excludes_secrets(state):
    evidence = Reader(kubeconfig='/synthetic/evidence.kubeconfig').inspect_owned(ID, metadata=META)
    assert evidence['provider'] == 'kubernetes'
    assert evidence['uid'] == 'pod-uid' and evidence['workload_uid'] == 'workload-uid'
    assert evidence['cpu_millis'] == 1000 and evidence['memory_bytes'] == 512 * 1024 ** 2
    assert evidence['pids_limit'] == 128 and evidence['pids_limit_attested'] is True
    assert evidence['pids_evidence_source'] == 'kubelet-config+cgroup-baseline'
    assert 'synthetic-private-route-token' not in json.dumps(evidence)
    assert state.config_calls == ['/synthetic/evidence.kubeconfig']


def test_ephemeral_storage_is_verified_and_reported(state):
    resources = state.pods[0]['spec']['containers'][0]['resources']
    resources['requests']['ephemeral-storage'] = '50Gi'
    resources['limits']['ephemeral-storage'] = '53687091200'
    evidence = Reader(kubeconfig='/synthetic/evidence.kubeconfig').inspect_owned(
        ID, metadata=META, cpu_millis=1000, memory_mib=512, storage_mib=51200)
    assert evidence['storage_mib'] == 51200
    assert evidence['ephemeral_storage_bytes'] == 51200 * 1024 ** 2


def test_storage_mismatch_or_missing_expected_storage_fails_closed(state):
    resources = state.pods[0]['spec']['containers'][0]['resources']
    resources['requests']['ephemeral-storage'] = '49Gi'
    resources['limits']['ephemeral-storage'] = '50Gi'
    with pytest.raises(EvidenceError, match='storage policy mismatch'):
        Reader(kubeconfig='/synthetic/evidence.kubeconfig').inspect_owned(
            ID, metadata=META, storage_mib=51200)

    resources['requests']['ephemeral-storage'] = '50Gi'
    resources['limits'].pop('ephemeral-storage')
    with pytest.raises(EvidenceError, match='storage policy mismatch'):
        Reader(kubeconfig='/synthetic/evidence.kubeconfig').inspect_owned(
            ID, metadata=META, storage_mib=51200)


def test_default_identity_is_incluster_never_admin_fallback(state):
    Reader()
    assert state.config_calls == ['incluster']


def test_managed_token_rotation_is_read_on_every_api_request(state, tmp_path):
    token = tmp_path / 'evidence-token'
    token.write_text('synthetic.initial.token.for-test')
    config = tmp_path / 'config.yaml'
    config.write_text(json.dumps({'current-context': 'evidence',
        'contexts': [{'name': 'evidence', 'context': {'user': 'reader'}}],
        'users': [{'name': 'reader', 'user': {'tokenFile': 'evidence-token'}}]}))
    reader = Reader(kubeconfig=config)
    reader.inspect_owned(ID)
    assert state.configuration.api_key['authorization'] == 'Bearer synthetic.initial.token.for-test'
    assert state.configuration.refresh_api_key_hook is None
    token.write_text('synthetic.rotated.token.for-test')
    reader.inspect_owned(ID)
    assert state.configuration.api_key['authorization'] == 'Bearer synthetic.rotated.token.for-test'
    token.write_text('synthetic.token\nAuthorization: unsafe')
    with pytest.raises(EvidenceError):
        reader.inspect_owned(ID)


@pytest.mark.parametrize('change', [
    lambda pod: pod['spec'].update(hostNetwork=True),
    lambda pod: pod['spec'].update(hostPID=True),
    lambda pod: pod['spec'].update(hostIPC=True),
    lambda pod: pod['spec'].pop('automountServiceAccountToken'),
    lambda pod: pod['spec'].update(automountServiceAccountToken=True),
    lambda pod: pod['spec']['volumes'].append({'name': 'host', 'hostPath': {'path': '/'}}),
    lambda pod: pod['spec']['volumes'].append({'name': 'credential', 'secret': {'secretName': 'x'}}),
    lambda pod: pod['spec']['volumes'].append({'name': 'disk', 'persistentVolumeClaim': {'claimName': 'x'}}),
    lambda pod: pod['spec']['containers'][0].update(securityContext={'privileged': True}),
    lambda pod: pod['spec']['initContainers'][0].update(securityContext={'privileged': True}),
    lambda pod: pod['spec']['containers'][0].update(securityContext={'capabilities': {'add': ['NET_ADMIN']}}),
    lambda pod: pod['spec']['containers'][0].update(ports=[{'containerPort': 80, 'hostPort': 8080}]),
    lambda pod: pod['spec']['containers'].append({'name': 'sidecar'}),
    lambda pod: pod['spec'].update(ephemeralContainers=[{'name': 'debug'}]),
    lambda pod: pod['spec']['containers'][0]['resources']['limits'].update(cpu='2'),
    lambda pod: pod['spec']['containers'][0]['resources']['requests'].update(memory='1Gi'),
    lambda pod: pod['spec']['initContainers'][0]['resources']['limits'].update(memory='1Gi'),
    lambda pod: pod['metadata']['ownerReferences'][0].update(uid='another-owner'),
    lambda pod: pod['metadata']['labels'].update(purpose='someone-else'),
    lambda pod: pod['status'].update(phase='Pending'),
])
def test_unsafe_or_incomplete_pod_fails_closed(state, change):
    change(state.pods[0])
    with pytest.raises(EvidenceError):
        Reader().inspect_owned(ID, metadata=META)


def test_multiple_pods_are_ambiguous(state):
    state.pods.append(copy.deepcopy(state.pods[0]))
    with pytest.raises(EvidenceError, match='exactly one'):
        Reader().inspect_owned(ID)


@pytest.mark.parametrize('change', [
    lambda node, fact: fact.update(verified_at=stamp(-181)),
    lambda node, fact: fact.update(verified_at=stamp(20)),
    lambda node, fact: fact.update(node_uid='old-node'),
    lambda node, fact: fact.update(boot_id='old-boot'),
    lambda node, fact: fact.update(limit=129),
    lambda node, fact: fact.update(enforced_since=stamp(-5)),
    lambda node, fact: fact.update(source='profile-claim'),
])
def test_unproven_pid_limit_is_not_attested(state, change):
    fact = json.loads(state.node['metadata']['annotations'][module.PID_ANNOTATION])
    change(state.node, fact)
    state.node['metadata']['annotations'][module.PID_ANNOTATION] = json.dumps(fact)
    result = Reader().inspect_owned(ID)
    assert result['pids_limit'] is None and result['pids_limit_attested'] is False


def test_absence_requires_cr_and_residual_pods_gone(state):
    reader = Reader()
    assert reader.verify_absent(ID) is False
    state.get_error = ApiError(404)
    assert reader.verify_absent(ID) is False
    state.pods = []
    assert reader.verify_absent(ID) is True


@pytest.mark.parametrize('error', [ApiError(403), ApiError(500), TimeoutError()])
def test_errors_never_become_absence(state, error):
    state.get_error = error
    with pytest.raises(EvidenceError):
        Reader().verify_absent(ID)


def test_pod_list_forbidden_does_not_prove_absence(state):
    state.get_error = ApiError(404); state.pod_error = ApiError(403)
    with pytest.raises(EvidenceError):
        Reader().verify_absent(ID)


def test_inventory_includes_orphan_and_rejects_lost_metadata(state):
    reader = Reader()
    assert reader.find_owned(META) == [ID]
    state.workloads = []
    assert reader.find_owned(META) == [ID]
    state.pods[0]['metadata']['labels'].pop('durable_run_id')
    with pytest.raises(EvidenceError, match='lost ownership'):
        reader.find_owned(META)


def test_incomplete_inventory_fails_closed(state):
    state.pod_continue = 'more'
    with pytest.raises(EvidenceError):
        Reader().find_owned(META)
