"""Bounded supported-interface probe. Never starts or restarts a daemon."""
import json
import subprocess


def main():
    try:
        result = subprocess.run(['codex', 'app-server', 'daemon', 'version'],
                                capture_output=True, text=True, timeout=10)
        available = result.returncode == 0
        report = {
            'supported_daemon_reachable': available,
            'desktop_subscription_verified': False,
            'production_enabled': False,
            'next_step': ('Verify an explicitly owned worker subscription on this transport.'
                          if available else 'Supported daemon unavailable. Desktop capture remains blocked.'),
        }
    except (OSError, subprocess.TimeoutExpired):
        report = {'supported_daemon_reachable': False, 'desktop_subscription_verified': False,
                  'production_enabled': False, 'next_step': 'Resolve supported CLI access.'}
    print(json.dumps(report, indent=2))
    return 0 if report['supported_daemon_reachable'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
