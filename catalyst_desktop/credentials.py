"""Optional OS credential storage. No plaintext or third-party cloud fallback."""
import sys

SERVICE = 'CATALYST SciSure desktop'

class CredentialError(Exception):
    pass

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
    try:
        return (store or system_store()).get_password(SERVICE, tenant)
    except Exception:
        raise CredentialError('The saved token could not be opened. Enter it for this session.') from None

def save_token(tenant, token, store=None):
    try:
        (store or system_store()).set_password(SERVICE, tenant, token)
    except Exception:
        raise CredentialError('The token was not saved. You can continue using it for this session.') from None

def forget_token(tenant, store=None):
    try:
        vault = store or system_store()
        if vault.get_password(SERVICE, tenant) is not None:
            vault.delete_password(SERVICE, tenant)
    except Exception:
        raise CredentialError('The saved token could not be removed. Remove the CATALYST entry in your system credential manager.') from None
