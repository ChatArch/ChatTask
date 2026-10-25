"""ChatEnv's canonical ChatTask profile; no import-time reads or writes."""
from chatenv import BaseEnvConfig, EnvField

class ChatTaskConfig(BaseEnvConfig):
    _title = 'ChatTask'
    _aliases = ['chattask', 'task']
    _storage_dir = 'ChatTask'

    CHATTASK_SANDBOX_ENDPOINT = EnvField('CHATTASK_SANDBOX_ENDPOINT', desc='Trusted OpenSandbox control-plane HTTP(S) origin.')
    CHATTASK_SANDBOX_API_KEY = EnvField('CHATTASK_SANDBOX_API_KEY', desc='OpenSandbox control-plane API key.', is_sensitive=True)
    CHATTASK_TOOL_PROFILE = EnvField('CHATTASK_TOOL_PROFILE', default='task-tools', desc='Name of an existing PyPI ChatEnv connection profile; no service URL or token is stored here.')
    CHATTASK_PUBLIC_DEPS_PATH = EnvField('CHATTASK_PUBLIC_DEPS_PATH', desc='Optional trusted public Python dependency archive for offline Sandbox tests/builds.')
    CHATTASK_PUBLIC_DEPS_SHA256 = EnvField('CHATTASK_PUBLIC_DEPS_SHA256', desc='Required SHA256 digest of the trusted dependency archive.')
    CHATTASK_IMAGE = EnvField('CHATTASK_IMAGE', default='python:3.10-slim', desc='Operator-selected workload image; never accepted from browser input.')
    CHATTASK_WEB_ORIGIN = EnvField('CHATTASK_WEB_ORIGIN', default='http://127.0.0.1:8000', desc='Fixed canonical browser origin.')
    CHATTASK_LOGIN_USERNAME = EnvField('CHATTASK_LOGIN_USERNAME', desc='Explicit fixed site account; no built-in production account.')
    CHATTASK_LOGIN_PASSWORD = EnvField('CHATTASK_LOGIN_PASSWORD', desc='Site password verified through ChatLogin.', is_sensitive=True)

    CHATTASK_CODEX_BASE_URL = EnvField('CHATTASK_CODEX_BASE_URL', desc='Configured model gateway base, with its API prefix.')
    CHATTASK_CODEX_API_KEY = EnvField('CHATTASK_CODEX_API_KEY', desc='Runtime task/model access token; injected into guest processes, never baked into images.', is_sensitive=True)
    CHATTASK_CODEX_MODEL = EnvField('CHATTASK_CODEX_MODEL', default='gpt-6-sol', desc='Codex model selected by the operator.')
    CHATTASK_CODEX_IMAGES = EnvField('CHATTASK_CODEX_IMAGES', default='{}', desc='JSON mapping of permitted image profile IDs to operator-provisioned images.')
    CHATTASK_CODEX_TOOLS_URL = EnvField('CHATTASK_CODEX_TOOLS_URL', desc='Optional task-scoped tool gateway root; original provider credentials remain server-side.')

    CHATTASK_GATEWAY_URL = EnvField('CHATTASK_GATEWAY_URL', desc='Optional HTTPS task-session broker origin.')
    CHATTASK_GATEWAY_CLIENT_TOKEN = EnvField('CHATTASK_GATEWAY_CLIENT_TOKEN', desc='Trusted broker control token, never sent to a Sandbox.', is_sensitive=True)
    CHATTASK_GATEWAY_CA_FILE = EnvField('CHATTASK_GATEWAY_CA_FILE', desc='Optional absolute path to an operator-owned public PEM CA for the gateway.')
    CHATTASK_CODEX_HOSTS = EnvField('CHATTASK_CODEX_HOSTS', default='{}', desc='Optional operator-approved guest hostname to IP mappings.')
    CHATTASK_CODEX_TOOLS_TOKEN = EnvField('CHATTASK_CODEX_TOOLS_TOKEN', desc='Optional restricted task-tool access token, separate from the model credential.', is_sensitive=True)

    @classmethod
    def test(cls):
        fields=cls.get_fields()
        for key in ['CHATTASK_SANDBOX_API_KEY','CHATTASK_LOGIN_PASSWORD','CHATTASK_GATEWAY_CLIENT_TOKEN']:
            if not fields[key].is_sensitive:
                raise RuntimeError('ChatTask secret-field contract failed')
        print('ChatTask schema registered; sensitive fields are masked. This check does not execute a workload.')
