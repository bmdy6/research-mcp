#!/usr/bin/env python3
"""Inject locally stored free API keys into CLI environment; never print values.

Only use with accounts already verified free-only. This launcher does not create,
modify, or verify billing settings; providers additionally preflight usage.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def keys_from_vault(data):
    found = {}
    def visit(node):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key in ('TAVILY_API_KEY', 'OPENALEX_API_KEY'):
                if key in found or not isinstance(value, dict) or not isinstance(value.get('value'), str) or not value['value'].strip():
                    raise ValueError('Missing, duplicated or malformed free key entry')
                found[key] = value['value']
            else:
                visit(value)
    visit(data)
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--verified-free-accounts', action='store_true', required=True)
    p.add_argument('--tavily-payg-disabled', action='store_true', help='Account UI already verified PAYG Disabled')
    p.add_argument('cli_args', nargs=argparse.REMAINDER)
    args = p.parse_args()
    try:
        values = keys_from_vault(json.loads((Path.home()/'.config/ai-vault/vault.json').read_text()))
    except (OSError, ValueError, TypeError):
        p.exit(2, 'Cannot read unambiguous free API keys from local vault.\n')
    env = dict(os.environ)
    # Account assertions come from this explicit invocation, never inherited flags.
    for key in ('TAVILY_API_KEY','OPENALEX_API_KEY','TAVILY_FREE_ONLY','OPENALEX_FREE_ONLY','TAVILY_PAYG_DISABLED'):
        env.pop(key, None)
    for name, value in values.items():
        env[name] = value
        env[name.replace('_API_KEY','_FREE_ONLY')] = '1'
    if args.tavily_payg_disabled:
        env['TAVILY_PAYG_DISABLED'] = '1'
    cli = args.cli_args
    if cli[:1] == ['--']:
        cli = cli[1:]
    return subprocess.call([sys.executable, '-m', 'research_mcp', *cli], env=env)


if __name__ == '__main__':
    sys.exit(main())
