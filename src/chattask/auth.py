"""Optional shared ChatLogin adapter; no second authentication protocol."""
import json
from urllib.parse import urlsplit
from chatlogin import PasswordBackend,Principal,SessionManager,MemorySessionStore,hash_password
from chatlogin.fastapi import CookieSettings,FastAPIAuth
from chatlogin.ui import LoginUI


def create_auth(settings):
    if not settings.username or not settings.password:
        raise ValueError('Web serving requires CHATTASK_LOGIN_USERNAME and CHATTASK_LOGIN_PASSWORD')
    users={settings.username:(Principal('site-operator','站点管理员'),hash_password(settings.password))}
    for entry in json.loads(settings.login_users_json or '[]'):
        users[entry['username']]=(Principal(entry['principal_id'],entry['display_name']),hash_password(entry['password']))
    backend=PasswordBackend(users)
    return FastAPIAuth(backend,SessionManager(MemorySessionStore(max_sessions=128),instance='chattask',ttl=86400),
                      origin=settings.origin,cookie=CookieSettings(name='sandbox_site_session',secure=urlsplit(settings.origin).scheme=='https'),
                      ui=LoginUI(title='Sandbox',subtitle='任务实验台 · 使用统一站点账号登录后，运行任务或继续一个环境。',
                                 palette='amber',layout='split',appearance='light'))
