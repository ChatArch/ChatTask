"""Read bounded Kubernetes runtime evidence without granting mutation rights.

The Kubernetes client is optional and imported only by this server-side reader.
Secret-bearing BatchSandbox annotations never enter returned evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import time


GROUP = 'sandbox.opensandbox.io'
VERSION = 'v1alpha1'
PLURAL = 'batchsandboxes'
ID_LABEL = 'opensandbox.io/id'
PARENT_LABEL = 'batch-sandbox.sandbox.opensandbox.io/name'
PID_ANNOTATION = 'chatsandbox.io/pid-attestation'
_TIMEOUT = (3, 10)
_NAME = re.compile(r'^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$')
_LABEL_KEY = re.compile(r'^(?:[a-zA-Z0-9][-a-zA-Z0-9./_]{0,251}[a-zA-Z0-9]|[a-zA-Z0-9])$')
_LABEL_VALUE = re.compile(r'^[a-zA-Z0-9](?:[-a-zA-Z0-9_.]{0,61}[a-zA-Z0-9])?$')
_DANGEROUS_CAPS = {'SYS_ADMIN', 'NET_ADMIN', 'SYS_MODULE', 'SYS_PTRACE', 'SYS_RAWIO', 'SYS_TIME', 'SYS_BOOT'}
_SCALES = {'n': Decimal('1e-9'), 'u': Decimal('1e-6'), 'm': Decimal('1e-3'), '': Decimal(1),
           'k': Decimal(1000), 'M': Decimal(1000) ** 2, 'G': Decimal(1000) ** 3,
           'T': Decimal(1000) ** 4, 'P': Decimal(1000) ** 5, 'E': Decimal(1000) ** 6,
           **{unit + 'i': Decimal(1024) ** power for power, unit in enumerate('KMGTPE', 1)}}


class KubernetesEvidenceError(RuntimeError):
    """Evidence is incomplete, ambiguous, unavailable, or violates policy."""


def _quantity(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise KubernetesEvidenceError('Invalid Kubernetes resource quantity')
    match = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)', str(value))
    if not match or match[2] not in _SCALES:
        raise KubernetesEvidenceError('Invalid Kubernetes resource quantity')
    try:
        result = Decimal(match[1]) * _SCALES[match[2]]
    except InvalidOperation:
        raise KubernetesEvidenceError('Invalid Kubernetes resource quantity') from None
    if not result.is_finite() or result <= 0:
        raise KubernetesEvidenceError('Non-positive Kubernetes resource quantity')
    return result


def _metadata(value):
    if not isinstance(value, dict):
        raise KubernetesEvidenceError('Metadata must be a mapping')
    for key, item in value.items():
        if (not isinstance(key, str) or not _LABEL_KEY.fullmatch(key) or
                not isinstance(item, str) or not _LABEL_VALUE.fullmatch(item)):
            raise KubernetesEvidenceError('Invalid ownership metadata')
    return value


def _matches(labels, expected):
    return isinstance(labels, dict) and all(labels.get(key) == value for key, value in expected.items())


class KubernetesEvidenceReader:
    """Read one namespace using an explicit kubeconfig or in-cluster identity.

    ``None`` never falls back to a user's default/admin kubeconfig. The caller's
    ServiceAccount needs read-only Pods/BatchSandboxes and Node get permissions.
    PID attestation is optional evidence from a trusted platform baseline publisher;
    absent/stale evidence reports None, so a strict-128 caller must reject it.
    """
    def __init__(self, namespace='opensandbox', kubeconfig=None):
        if not isinstance(namespace, str) or not _NAME.fullmatch(namespace):
            raise ValueError('Invalid workload namespace')
        try:
            from kubernetes import client, config
            from kubernetes.client.exceptions import ApiException
        except ImportError:
            raise RuntimeError('Kubernetes evidence requires the ChatSandbox server extra') from None
        if kubeconfig is None:
            config.load_incluster_config()
        else:
            config.load_kube_config(config_file=str(kubeconfig))
        self.namespace = namespace
        self._api_exception = ApiException
        self._client = client.ApiClient()
        self._core = client.CoreV1Api(self._client)
        self._custom = client.CustomObjectsApi(self._client)
        self._token_file = None
        if kubeconfig is not None and Path(kubeconfig).is_file():
            # A static tokenFile has no expiry in Kubernetes' config loader, so
            # refresh it explicitly after the root operator rotates the file.
            import yaml
            source = Path(kubeconfig)
            body = yaml.safe_load(source.read_text())
            current = next(x['context'] for x in body['contexts'] if x['name'] == body['current-context'])
            user = next(x['user'] for x in body['users'] if x['name'] == current['user'])
            if user.get('tokenFile'):
                path = Path(user['tokenFile'])
                self._token_file = path if path.is_absolute() else source.parent / path
                self._client.configuration.refresh_api_key_hook = None

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            raise ValueError('Invalid Sandbox id')
        return value

    def _call(self, function, *, allow_not_found=False, **kwargs):
        try:
            if self._token_file is not None:
                token = self._token_file.read_text().strip()
                if not re.fullmatch(r'[A-Za-z0-9._-]{20,32768}', token):
                    raise KubernetesEvidenceError('Managed evidence credential unavailable')
                self._client.configuration.api_key['authorization'] = 'Bearer ' + token
            result = function(_request_timeout=_TIMEOUT, **kwargs)
            result = self._client.sanitize_for_serialization(result)
            if not isinstance(result, dict):
                raise KubernetesEvidenceError('Unexpected Kubernetes response')
            return result
        except self._api_exception as error:
            if allow_not_found and error.status == 404:
                return None
            raise KubernetesEvidenceError('Kubernetes evidence request rejected (' + str(error.status) + ')') from None
        except KubernetesEvidenceError:
            raise
        except Exception:
            raise KubernetesEvidenceError('Kubernetes evidence request unavailable') from None

    def _workload(self, sandbox_id, *, absent=False):
        return self._call(self._custom.get_namespaced_custom_object, allow_not_found=absent,
                          group=GROUP, version=VERSION, namespace=self.namespace,
                          plural=PLURAL, name=sandbox_id)

    def _pods(self):
        body = self._call(self._core.list_namespaced_pod, namespace=self.namespace, limit=1000)
        if body.get('metadata', {}).get('continue') or not isinstance(body.get('items'), list):
            raise KubernetesEvidenceError('Incomplete Pod inventory')
        return body['items']

    @staticmethod
    def _owners(pod):
        refs = pod.get('metadata', {}).get('ownerReferences', [])
        return [x for x in refs if x.get('kind') == 'BatchSandbox' and
                x.get('apiVersion') == GROUP + '/' + VERSION and x.get('controller') is True]

    def inspect_owned(self, sandbox_id, *, metadata=None, cpu_millis=1000, memory_mib=512):
        sandbox_id = self._id(sandbox_id)
        expected = _metadata({} if metadata is None else metadata)
        if type(cpu_millis) is not int or type(memory_mib) is not int or cpu_millis <= 0 or memory_mib <= 0:
            raise ValueError('Resource targets must be positive integers')
        workload = self._workload(sandbox_id)
        info = workload.get('metadata', {})
        labels = info.get('labels', {})
        if (info.get('name') != sandbox_id or info.get('namespace') != self.namespace or
                not info.get('uid') or labels.get(ID_LABEL) != sandbox_id or
                info.get('deletionTimestamp') or not _matches(labels, expected) or
                workload.get('spec', {}).get('replicas') != 1):
            raise KubernetesEvidenceError('Sandbox ownership or lifecycle is ambiguous')
        candidates = [pod for pod in self._pods() if
                      pod.get('metadata', {}).get('labels', {}).get(PARENT_LABEL) == sandbox_id or
                      any(ref.get('name') == sandbox_id for ref in self._owners(pod))]
        if len(candidates) != 1:
            raise KubernetesEvidenceError('Sandbox must have exactly one owned Pod')
        pod = candidates[0]
        pod_info = pod.get('metadata', {})
        refs = self._owners(pod)
        if (len(refs) != 1 or refs[0].get('name') != sandbox_id or refs[0].get('uid') != info['uid'] or
                pod_info.get('namespace') != self.namespace or not pod_info.get('uid') or
                pod_info.get('deletionTimestamp') or
                not _matches(pod_info.get('labels', {}), {**expected, ID_LABEL: sandbox_id, PARENT_LABEL: sandbox_id})):
            raise KubernetesEvidenceError('Pod does not prove Sandbox ownership')
        spec, status = pod.get('spec', {}), pod.get('status', {})
        node_name = spec.get('nodeName')
        if (status.get('phase') != 'Running' or not node_name or
                not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', []))):
            raise KubernetesEvidenceError('Sandbox Pod is not running and ready')
        if any(spec.get(key, False) for key in ['hostNetwork', 'hostPID', 'hostIPC']):
            raise KubernetesEvidenceError('Sandbox uses a host namespace')
        if spec.get('automountServiceAccountToken') is not False or spec.get('ephemeralContainers'):
            raise KubernetesEvidenceError('Sandbox has service credentials or debug containers')
        volumes = spec.get('volumes', [])
        if any(set(volume) - {'name', 'emptyDir'} or 'emptyDir' not in volume for volume in volumes):
            raise KubernetesEvidenceError('Sandbox has unaudited host, persistent, or credential volumes')
        pod_security = spec.get('securityContext', {})
        if pod_security.get('seccompProfile', {}).get('type') == 'Unconfined' or pod_security.get('sysctls'):
            raise KubernetesEvidenceError('Sandbox disables runtime security policy')
        containers = spec.get('containers', [])
        if len(containers) != 1:
            raise KubernetesEvidenceError('Sandbox has an unaudited sidecar')
        all_containers = containers + spec.get('initContainers', [])
        for container in all_containers:
            security = container.get('securityContext', {})
            if security.get('privileged') is True or _DANGEROUS_CAPS.intersection(security.get('capabilities', {}).get('add', [])):
                raise KubernetesEvidenceError('Sandbox container has elevated capabilities')
            if security.get('seccompProfile', {}).get('type') == 'Unconfined' or any(p.get('hostPort', 0) for p in container.get('ports', [])):
                raise KubernetesEvidenceError('Sandbox exposes host resources')
        main = containers[0]
        expected_cpu, expected_mem = Decimal(cpu_millis) / 1000, Decimal(memory_mib) * 1024 ** 2
        for kind in ['requests', 'limits']:
            resources = main.get('resources', {}).get(kind, {})
            if _quantity(resources.get('cpu')) != expected_cpu or _quantity(resources.get('memory')) != expected_mem:
                raise KubernetesEvidenceError('Sandbox CPU or memory policy mismatch')
        for init in spec.get('initContainers', []):
            resources = init.get('resources', {})
            if (_quantity(resources.get('limits', {}).get('cpu')) > expected_cpu or
                    _quantity(resources.get('limits', {}).get('memory')) > expected_mem):
                raise KubernetesEvidenceError('Init container exceeds the reserved Sandbox budget')
        container_status = [x for x in status.get('containerStatuses', []) if x.get('name') == main.get('name')]
        if len(container_status) != 1 or not container_status[0].get('ready') or not container_status[0].get('imageID'):
            raise KubernetesEvidenceError('Sandbox image or container state is unavailable')
        node = self._call(self._core.read_node, name=node_name)
        pids_limit = self._pids_limit(node, pod_info)
        return {'provider': 'kubernetes', 'sandbox_id': sandbox_id, 'namespace': self.namespace,
                'node': node_name, 'uid': pod_info['uid'], 'workload_uid': info['uid'],
                'pod_name': pod_info['name'], 'container_name': main['name'],
                'image': main.get('image'), 'image_id': container_status[0]['imageID'],
                'cpu_millis': cpu_millis, 'memory_mib': memory_mib,
                'nano_cpus': cpu_millis * 1000000, 'memory_bytes': memory_mib * 1024 ** 2,
                'pids_limit': pids_limit, 'pids_limit_attested': pids_limit is not None,
                'pids_evidence_source': 'kubelet-config+cgroup-baseline' if pids_limit is not None else None,
                'privileged': False, 'host_namespaces': False, 'host_mount_count': 0,
                'service_account_automounted': False, 'evidence_source': 'kubernetes-api',
                'observed_at': datetime.now(timezone.utc).isoformat()}

    @staticmethod
    def _pids_limit(node, pod_info):
        raw = node.get('metadata', {}).get('annotations', {}).get(PID_ANNOTATION)
        if raw is None:
            return None
        try:
            fact = json.loads(raw)
            def stamp(value):
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError('Timezone required')
                return parsed.timestamp()
            observed = stamp(fact['verified_at'])
            if (fact.get('version') != 1 or fact.get('source') != 'kubelet-config+cgroup-baseline' or
                    not -5 <= time.time() - observed <= 180 or fact.get('limit') != 128 or
                    fact.get('node_uid') != node.get('metadata', {}).get('uid') or
                    fact.get('boot_id') != node.get('status', {}).get('nodeInfo', {}).get('bootID') or
                    not fact.get('node_uid') or not fact.get('boot_id') or
                    stamp(pod_info['creationTimestamp']) < stamp(fact['enforced_since'])):
                return None
            return 128
        except (ValueError, TypeError, KeyError, AttributeError):
            return None

    def verify_absent(self, sandbox_id):
        sandbox_id = self._id(sandbox_id)
        if self._workload(sandbox_id, absent=True) is not None:
            return False
        for pod in self._pods():
            labels = pod.get('metadata', {}).get('labels', {})
            if (labels.get(PARENT_LABEL) == sandbox_id or labels.get(ID_LABEL) == sandbox_id or
                    any(ref.get('name') == sandbox_id for ref in self._owners(pod))):
                return False
        return True

    def find_owned(self, metadata):
        expected = _metadata(metadata)
        if not expected:
            raise ValueError('Ownership inventory requires non-empty metadata')
        body = self._call(self._custom.list_namespaced_custom_object, group=GROUP, version=VERSION,
                          namespace=self.namespace, plural=PLURAL, limit=1000)
        if body.get('metadata', {}).get('continue') or not isinstance(body.get('items'), list):
            raise KubernetesEvidenceError('Incomplete Sandbox inventory')
        result = set()
        for workload in body['items']:
            info = workload.get('metadata', {})
            labels = info.get('labels', {})
            if _matches(labels, expected):
                sandbox_id = self._id(info.get('name'))
                if labels.get(ID_LABEL) != sandbox_id:
                    raise KubernetesEvidenceError('Sandbox inventory ownership is ambiguous')
                result.add(sandbox_id)
        for pod in self._pods():
            labels = pod.get('metadata', {}).get('labels', {})
            owners = self._owners(pod)
            if not owners:
                continue
            if _matches(labels, expected):
                if len(owners) != 1 or labels.get(ID_LABEL) != owners[0].get('name'):
                    raise KubernetesEvidenceError('Orphan Pod ownership is ambiguous')
                result.add(self._id(owners[0]['name']))
            elif any(key not in labels for key in expected) and all(labels.get(key, value) == value for key, value in expected.items()):
                raise KubernetesEvidenceError('Pod inventory lost ownership metadata')
        return sorted(result)
