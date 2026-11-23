"""Resolve explicit local profiles without consulting runtime credentials."""
from pathlib import Path

import pytest
from chatenv import EnvStore, get_paths

from chatsandbox import auth, evidence, gateway, ledger
from chatsandbox.config import BackendSettings, ChatSandboxConfig, load_backend_settings, load_server, origin, outbound_proxy, private_value


SYNTHETIC_KEY = 'synthetic-unit-test-credential'


def save_profile(home, **fields):
    values = {'CHATSANDBOX_ENDPOINT': 'https://sandbox.example.test',
              'CHATSANDBOX_IMAGE': 'approved-image@sha256:example', **fields}
    return EnvStore(get_paths(home).envs_dir).save_profile(ChatSandboxConfig, 'consumer', values)


@pytest.mark.parametrize('value', [None, '', 'sandbox.example.test', 'https://user:pass@example.test',
                                  'https://example.test/path', 'https://example.test?query=1',
                                  'https://example.test#fragment', 'http://example.test:0',
                                  'http://example.test:invalid', 'https://example.test\n'])
def test_endpoint_requires_an_explicit_origin(value):
    with pytest.raises(ValueError): origin(value)


def test_origin_normalization_and_credentials_are_not_in_settings_repr():
    assert origin('https://sandbox.example.test/') == 'https://sandbox.example.test'
    assert origin('http://127.0.0.1:10251') == 'http://127.0.0.1:10251'
    settings = BackendSettings('https://sandbox.example.test', SYNTHETIC_KEY)
    assert SYNTHETIC_KEY not in repr(settings)


@pytest.mark.parametrize('change', [{'provider': 'automatic'}, {'cpu_millis': True},
                                    {'memory_mib': 0}, {'pids_limit': -1}])
def test_unknown_providers_and_invalid_resource_policy_are_rejected(change):
    with pytest.raises(ValueError):
        BackendSettings('https://sandbox.example.test', SYNTHETIC_KEY, **change)


def test_named_profile_uses_selected_home_without_process_key_fallback(tmp_path, monkeypatch):
    home = tmp_path / 'selected-home'
    save_profile(home, CHATSANDBOX_API_KEY=SYNTHETIC_KEY)
    monkeypatch.setenv('CHATSANDBOX_API_KEY', 'unrelated-process-value')
    settings = load_backend_settings('consumer', home=home)
    assert settings.api_key == SYNTHETIC_KEY
    assert settings.provider == 'remote'
    assert settings.image == 'approved-image@sha256:example'
    save_profile(home)
    with pytest.raises(ValueError): load_backend_settings('consumer', home=home)


def test_resource_and_lifetime_overrides_are_loaded_from_private_profile(tmp_path):
    home = tmp_path / 'resource-home'
    save_profile(home, CHATSANDBOX_API_KEY=SYNTHETIC_KEY,
                 CHATSANDBOX_CPU_MILLIS='2000', CHATSANDBOX_MEMORY_MIB='8192',
                 CHATSANDBOX_PIDS_LIMIT='4096', CHATSANDBOX_STORAGE_MIB='51200',
                 CHATSANDBOX_MAX_TTL_SECONDS='43200')
    settings = load_backend_settings('consumer', home=home)
    assert settings.cpu_millis == 2000
    assert settings.memory_mib == 8192
    assert settings.pids_limit == 4096
    assert settings.storage_mib == 51200
    assert settings.max_ttl_seconds == 43200


def test_server_profile_wires_configured_storage_budget(tmp_path, monkeypatch):
    home = tmp_path / 'server-home'
    internal_key = tmp_path / 'internal.key'
    signing_key = tmp_path / 'signing.key'
    kubeconfig = tmp_path / 'evidence.kubeconfig'
    internal_key.write_text('synthetic-internal-key')
    signing_key.write_text('x' * 32)
    kubeconfig.write_text('apiVersion: v1\nkind: Config\n')
    for path in [internal_key, signing_key, kubeconfig]:
        path.chmod(0o600)
    EnvStore(get_paths(home).envs_dir).save_profile(ChatSandboxConfig, 'server', {
        'CHATSANDBOX_CRS_ORIGIN': 'https://crs.example.test',
        'CHATSANDBOX_INTERNAL_API': 'http://native.example.test',
        'CHATSANDBOX_INTERNAL_INGRESS': 'http://ingress.example.test',
        'CHATSANDBOX_INTERNAL_KEY_FILE': str(internal_key),
        'CHATSANDBOX_SIGNING_KEY_FILE': str(signing_key),
        'CHATSANDBOX_PUBLIC_ORIGIN': 'https://sandbox.example.test',
        'CHATSANDBOX_KUBECONFIG': str(kubeconfig),
        'CHATSANDBOX_NAMESPACE': 'opensandbox',
        'CHATSANDBOX_MAX_STORAGE_MIB': '102400',
    })
    captured = {}
    class FakeVerifier:
        def __init__(self, origin, proxy=None): self.origin, self.proxy = origin, proxy
    class FakeReader:
        def __init__(self, namespace, kubeconfig=None): self.namespace, self.kubeconfig = namespace, kubeconfig
    class FakeLedger:
        def __init__(self, path): self.path = path
    def fake_create_app(settings, **kwargs):
        captured['settings'] = settings
        captured.update(kwargs)
        return 'synthetic-app'
    monkeypatch.setattr(auth, 'CRSVerifier', FakeVerifier)
    monkeypatch.setattr(evidence, 'KubernetesEvidenceReader', FakeReader)
    monkeypatch.setattr(ledger, 'Ledger', FakeLedger)
    monkeypatch.setattr(gateway, 'create_app', fake_create_app)

    assert load_server('server', home=home) == 'synthetic-app'
    assert captured['settings'].max_storage_mib == 102400


@pytest.mark.parametrize('field,value', [
    ('CHATSANDBOX_CPU_MILLIS', '0'),
    ('CHATSANDBOX_MEMORY_MIB', '1.5'),
    ('CHATSANDBOX_PIDS_LIMIT', '-1'),
    ('CHATSANDBOX_STORAGE_MIB', '50Gi'),
    ('CHATSANDBOX_MAX_TTL_SECONDS', '59'),
])
def test_invalid_resource_and_lifetime_overrides_fail_closed(tmp_path, field, value):
    home = tmp_path / 'invalid-resource-home'
    save_profile(home, CHATSANDBOX_API_KEY=SYNTHETIC_KEY, **{field: value})
    with pytest.raises(ValueError):
        load_backend_settings('consumer', home=home)


def test_server_profile_wires_configured_maximum_ttl(tmp_path, monkeypatch):
    home = tmp_path / 'server-home'
    internal_key = tmp_path / 'internal.key'
    signing_key = tmp_path / 'signing.key'
    kubeconfig = tmp_path / 'evidence.kubeconfig'
    internal_key.write_text('synthetic-internal-key')
    signing_key.write_text('x' * 32)
    kubeconfig.write_text('apiVersion: v1\nkind: Config\n')
    for path in [internal_key, signing_key, kubeconfig]:
        path.chmod(0o600)
    EnvStore(get_paths(home).envs_dir).save_profile(ChatSandboxConfig, 'server', {
        'CHATSANDBOX_CRS_ORIGIN': 'https://crs.example.test',
        'CHATSANDBOX_INTERNAL_API': 'http://native.example.test',
        'CHATSANDBOX_INTERNAL_INGRESS': 'http://ingress.example.test',
        'CHATSANDBOX_INTERNAL_KEY_FILE': str(internal_key),
        'CHATSANDBOX_SIGNING_KEY_FILE': str(signing_key),
        'CHATSANDBOX_PUBLIC_ORIGIN': 'https://sandbox.example.test',
        'CHATSANDBOX_KUBECONFIG': str(kubeconfig),
        'CHATSANDBOX_NAMESPACE': 'opensandbox',
        'CHATSANDBOX_MAX_TTL_SECONDS': '43200',
    })
    captured = {}
    class FakeVerifier:
        def __init__(self, origin, proxy=None):
            self.origin, self.proxy = origin, proxy
    class FakeReader:
        def __init__(self, namespace, kubeconfig=None):
            self.namespace, self.kubeconfig = namespace, kubeconfig
    class FakeLedger:
        def __init__(self, path):
            self.path = path
    def fake_create_app(settings, **kwargs):
        captured['settings'] = settings
        captured.update(kwargs)
        return 'synthetic-app'
    monkeypatch.setattr(auth, 'CRSVerifier', FakeVerifier)
    monkeypatch.setattr(evidence, 'KubernetesEvidenceReader', FakeReader)
    monkeypatch.setattr(ledger, 'Ledger', FakeLedger)
    monkeypatch.setattr(gateway, 'create_app', fake_create_app)

    assert load_server('server', home=home) == 'synthetic-app'
    assert captured['settings'].max_ttl_seconds == 43200
    assert captured['evidence'].namespace == 'opensandbox'
    assert captured['evidence'].kubeconfig == str(kubeconfig)


@pytest.mark.parametrize('name', ['', '../consumer', '/consumer', 'has space', 'a' * 129, None])
def test_profile_name_is_validated_before_any_file_lookup(tmp_path, name):
    with pytest.raises(ValueError): load_backend_settings(name, home=tmp_path / 'unused')
    assert not (tmp_path / 'unused').exists()


def test_private_file_reference_keeps_credential_out_of_sandbox_profile(tmp_path):
    home = tmp_path / 'home'
    reference = tmp_path / 'caller-value'
    reference.write_text(SYNTHETIC_KEY + '\n')
    reference.chmod(0o600)
    profile = save_profile(home, CHATSANDBOX_API_KEY_FILE=str(reference))
    assert load_backend_settings('consumer', home=home).api_key == SYNTHETIC_KEY
    assert SYNTHETIC_KEY not in profile.read_text()


def test_existing_owner_profile_can_be_in_an_explicit_local_home(tmp_path):
    home, owner_home = tmp_path / 'consumer-home', tmp_path / 'owner-home'
    owner = Path(get_paths(owner_home).envs_dir) / 'CRS' / 'existing.env'
    owner.parent.mkdir(parents=True)
    owner.write_text('CRS_API_KEY=' + SYNTHETIC_KEY + '\n')
    owner.chmod(0o600)
    profile = save_profile(home, CHATSANDBOX_KEY_PROFILE='CRS/existing',
                           CHATSANDBOX_KEY_HOME=str(owner_home))
    assert load_backend_settings('consumer', home=home).api_key == SYNTHETIC_KEY
    assert SYNTHETIC_KEY not in profile.read_text()
    owner.chmod(0o644)
    with pytest.raises(ValueError, match='owner is unavailable'):
        load_backend_settings('consumer', home=home)


@pytest.mark.parametrize('owner_name,field', [('CRS', 'CRS_API_KEY'), ('OpenAI', 'OPENAI_API_KEY')])
def test_owner_reference_defaults_to_selected_consumer_home(tmp_path, owner_name, field):
    owner = Path(get_paths(tmp_path).envs_dir) / owner_name / 'existing.env'
    owner.parent.mkdir(parents=True)
    owner.write_text(field + '=' + SYNTHETIC_KEY + '\n')
    owner.chmod(0o600)
    save_profile(tmp_path, CHATSANDBOX_KEY_PROFILE=owner_name + '/existing')
    assert load_backend_settings('consumer', home=tmp_path).api_key == SYNTHETIC_KEY


@pytest.mark.parametrize('fields', [
    {'CHATSANDBOX_API_KEY': SYNTHETIC_KEY, 'CHATSANDBOX_API_KEY_FILE': '/unused'},
    {'CHATSANDBOX_API_KEY': SYNTHETIC_KEY, 'CHATSANDBOX_KEY_PROFILE': 'CRS/existing'},
    {'CHATSANDBOX_API_KEY_FILE': '/unused', 'CHATSANDBOX_KEY_PROFILE': 'CRS/existing'},
])
def test_ambiguous_credential_authorities_fail_before_reference_reads(tmp_path, fields):
    save_profile(tmp_path, **fields)
    with pytest.raises(ValueError, match='one sandbox credential authority'):
        load_backend_settings('consumer', home=tmp_path)


@pytest.mark.parametrize('owner', ['Other/existing', 'CRS/../existing', 'CRS/missing'])
def test_invalid_or_missing_owner_reference_has_no_fallback(tmp_path, owner, monkeypatch):
    save_profile(tmp_path, CHATSANDBOX_KEY_PROFILE=owner)
    monkeypatch.setenv('CRS_API_KEY', 'unrelated-process-value')
    with pytest.raises(ValueError): load_backend_settings('consumer', home=tmp_path)


def test_private_reference_rejects_public_permissions_symlinks_and_multiline(tmp_path):
    source = tmp_path / 'value'
    source.write_text(SYNTHETIC_KEY)
    source.chmod(0o644)
    with pytest.raises(ValueError): private_value(source)
    source.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(source)
    with pytest.raises(ValueError): private_value(link)
    source.write_text('first\nsecond\n')
    with pytest.raises(ValueError): private_value(source)
    with pytest.raises(ValueError): private_value('relative-file')


def proxy_file(tmp_path, text):
    path = tmp_path / 'existing-proxy.env'
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_existing_proxy_references_are_expanded_as_data_without_global_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv('https_proxy', 'http://unrelated-process.example.test:7890')
    path = proxy_file(tmp_path, 'proxy_host=proxy.example.test\nall_proxy=http://$proxy_host:7890\nhttps_proxy=$all_proxy\n')
    assert outbound_proxy(path) == 'http://proxy.example.test:7890'
    assert path.read_text().endswith('https_proxy=$all_proxy\n')
    import os
    assert os.environ['https_proxy'] == 'http://unrelated-process.example.test:7890'


@pytest.mark.parametrize('name', ['https_proxy', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'])
def test_supported_existing_proxy_variable_names(tmp_path, name):
    path = proxy_file(tmp_path, name + '=http://proxy.example.test:7890\n')
    assert outbound_proxy(path) == 'http://proxy.example.test:7890'


def test_proxy_reference_must_remain_private_and_resolvable(tmp_path):
    path = proxy_file(tmp_path, 'https_proxy=http://proxy.example.test:7890\n')
    path.chmod(0o644)
    with pytest.raises(ValueError): outbound_proxy(path)
    path.chmod(0o600)
    link = tmp_path / 'linked.env'
    link.symlink_to(path)
    with pytest.raises(ValueError): outbound_proxy(link)
    path.write_text('https_proxy=$cycle\ncycle=$https_proxy\n')
    with pytest.raises(ValueError): outbound_proxy(path)
    assert path.read_text() == 'https_proxy=$cycle\ncycle=$https_proxy\n'
