import requests

def check_connection():
    try:
        requests.get('https://www.google.com',timeout=3)
        return True
    except:
        return False

BANNED = '<>:"/\\|?*\0'


def safe_name(title):
    """Title with the banned characters swapped for '-', still readable."""
    for c in BANNED:
        title = title.replace(c, '-')
    title = title.encode()[:180].decode(errors='ignore')
    return title.strip('-. ') or 'untitled'
