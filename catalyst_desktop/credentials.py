"""Optional OS credential storage. No plaintext or third-party cloud fallback."""
import sys
from .scisure import SciSureError, tenant_origin, token_value

SERVICE = 'CATALYST SciSure desktop'

class CredentialError(Exception):
    pass


def tenant_key(tenant):
    try:
        return tenant_origin(tenant)
    except SciSureError as error:
        raise CredentialError(str(error)) from None

def system_store():
    try:
        # Select a specific OS backend instead of user-configured third-party keyrings.
        if sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring
            return WinVaultKeyring()
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
            return Keyring()
        from keyring.backends.SecretService import Keyring
        return Keyring()
    except Exception:
        raise CredentialError('The operating system credential store is unavailable. Use the token for this session only.') from None

def load_token(tenant, store=None):
    key = tenant_key(tenant)
    try:
        value = (store if store is not None else system_store()).get_password(SERVICE, key)
        return token_value(value) if value is not None else None
    except Exception:
        raise CredentialError('The saved token could not be opened. Enter it for this session.') from None

def save_token(tenant, token, store=None):
    key = tenant_key(tenant)
    try:
        value = token_value(token)
        (store if store is not None else system_store()).set_password(SERVICE, key, value)
    except Exception:
        raise CredentialError('The token was not saved. You can continue using it for this session.') from None

def forget_token(tenant, store=None):
    key = tenant_key(tenant)
    try:
        vault = store if store is not None else system_store()
        if vault.get_password(SERVICE, key) is not None:
            vault.delete_password(SERVICE, key)
    except Exception:
        raise CredentialError('The saved token could not be removed. Remove the CATALYST entry in your system credential manager.') from None
