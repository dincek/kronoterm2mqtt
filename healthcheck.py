#!/usr/bin/env python3

"""
    Container health check
    ~~~~~~~~~~~~~~~~~~~~~~

    Asks the health endpoint of the running publish loop and exits 0 (healthy) or
    1 (unhealthy). Deliberately stdlib only and free of project imports, so it starts
    fast and works in an image without curl, wget or a package manager.

    Host and port can be overridden with KRONOTERM_HEALTH_HOST / KRONOTERM_HEALTH_PORT.
"""

import json
import os
import sys
import urllib.error
import urllib.request


HOST = os.environ.get('KRONOTERM_HEALTH_HOST', '127.0.0.1')
PORT = os.environ.get('KRONOTERM_HEALTH_PORT', '8099')
TIMEOUT = float(os.environ.get('KRONOTERM_HEALTH_TIMEOUT', '5'))


def main() -> int:
    url = f'http://{HOST}:{PORT}/health'
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            json.loads(response.read())  # A malformed answer is a failure, too
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors='replace')
        try:
            problems = json.loads(body).get('problems', [])
        except json.JSONDecodeError:
            problems = [body[:200]]
        print(f'unhealthy: {"; ".join(problems)}')
        return 1
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as err:
        print(f'unhealthy: no answer from {url}: {err}')
        return 1

    print('healthy')
    return 0


if __name__ == '__main__':
    sys.exit(main())
