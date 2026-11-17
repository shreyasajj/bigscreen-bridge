"""Load a user-private service configuration without exposing its bearer token."""
import json
import os
from pathlib import Path
import stat


def load_config(path):
    path = Path(path)
    parent = path.parent.lstat()
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or not stat.S_ISDIR(parent.st_mode)
            or info.st_uid != os.getuid() or parent.st_uid != os.getuid()
            or (info.st_mode | parent.st_mode) & 0o077):
        raise ValueError('Bridge config and its directory must be private and owned by this user')
    config = json.loads(path.read_text())
    if (not isinstance(config, dict) or not isinstance(config.get('token'), str)
            or not 32 <= len(config['token']) <= 512 or not isinstance(config.get('device_id'), str)
            or not 1 <= len(config['device_id']) <= 128):
        raise ValueError('A bounded device ID and at least 32-character token are required')
    port = config.get('port', 8765)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Bridge port must be between 1 and 65535')
    return config
