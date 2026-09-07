"""Canonical env loader. .env is source of truth (PM2 may carry stale shell env)."""
import os


def load_env(path="/entropy/.env"):
    """Load .env into os.environ, overriding existing values. Returns the dict."""
    if not os.path.exists(path):
        return {}
    vals = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    for k, v in vals.items():
        os.environ[k] = v
    return vals