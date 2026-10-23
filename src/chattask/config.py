"""ChatEnv's canonical ChatTask profile; no import-time reads or writes."""
from chatenv import BaseEnvConfig, EnvField

class ChatTaskConfig(BaseEnvConfig):
    _title = 'ChatTask'
    _aliases = ['chattask', 'task']
    _storage_dir = 'ChatTask'

    CHATTASK_SANDBOX_ENDPOINT = EnvField('CHATTASK_SANDBOX_ENDPOINT', desc='Trusted OpenSandbox control-plane HTTP(S) origin.')
    CHATTASK_SANDBOX_API_KEY = EnvField('CHATTASK_SANDBOX_API_KEY', desc='OpenSandbox control-plane API key.', is_sensitive=True)
    CHATTASK_IMAGE = EnvField('CHATTASK_IMAGE', default='python:3.10-slim', desc='Operator-selected workload image; never accepted from browser input.')
    CHATTASK_WEB_ORIGIN = EnvField('CHATTASK_WEB_ORIGIN', default='http://127.0.0.1:8000', desc='Fixed canonical browser origin.')
    CHATTASK_LOGIN_USERNAME = EnvField('CHATTASK_LOGIN_USERNAME', desc='Explicit fixed site account; no built-in production account.')
    CHATTASK_LOGIN_PASSWORD = EnvField('CHATTASK_LOGIN_PASSWORD', desc='Site password verified through ChatLogin.', is_sensitive=True)

    @classmethod
    def test(cls):
        fields=cls.get_fields()
        for key in ['CHATTASK_SANDBOX_API_KEY','CHATTASK_LOGIN_PASSWORD']:
            if not fields[key].is_sensitive:
                raise RuntimeError('ChatTask secret-field contract failed')
        print('ChatTask schema registered; sensitive fields are masked. This check does not execute a workload.')
