#!/usr/bin/env python3
"""Generate Kamal deploy config from provisioning output and workflow inputs.

Reads from environment:
  INPUT_WORKERS_ENABLED - Whether workers are enabled
  INPUT_WORKERS_CMD     - Command for worker containers
  INPUT_DB_ENABLED      - Whether database is enabled
  INPUT_DB_PLAN         - Database VM plan (micro, small, medium, etc.)
  INPUT_DOMAIN          - Custom domain (optional, enables SSL via Let's Encrypt)
  REPO_NAME             - Repository name
  REPO_FULL             - Full repository path (owner/name)
  REPO_OWNER            - Repository owner

Reads from files:
  /tmp/provision-output.json      - Provisioning output (IPs, etc.)
  /tmp/kamal_custom_vars.json     - Custom clear env vars from ENV_VARS
  /tmp/kamal_custom_secrets.json  - Custom secret env var names from SECRET_ENV_VARS

Outputs:
  config/deploy.yml - Kamal deployment configuration
"""
import json
import os

import yaml

# Plan name -> RAM in MiB
PLAN_RAM_MB = {
    'micro': 1024,
    'small': 2048,
    'medium': 4096,
    'large': 8192,
    'xlarge': 16384,
    '2xlarge': 32768,
    '4xlarge': 65536,
}


def compute_pg_params(plan):
    """Compute PostgreSQL tuning parameters based on DB VM plan size."""
    ram_mb = PLAN_RAM_MB[plan]

    if ram_mb <= 4096:
        max_conn = 100
    elif ram_mb <= 16384:
        max_conn = 200
    else:
        max_conn = 400

    shared_buffers_mb = ram_mb // 4
    effective_cache_size_mb = ram_mb * 3 // 4
    work_mem_mb = ram_mb // max_conn // 4
    work_mem_mb = max(work_mem_mb, 2)
    maintenance_work_mem_mb = ram_mb // 16
    maintenance_work_mem_mb = min(maintenance_work_mem_mb, 2048)

    def fmt(mb):
        if mb >= 1024 and mb % 1024 == 0:
            return f'{mb // 1024}GB'
        return f'{mb}MB'

    return {
        'shared_buffers': fmt(shared_buffers_mb),
        'effective_cache_size': fmt(effective_cache_size_mb),
        'work_mem': fmt(work_mem_mb),
        'maintenance_work_mem': fmt(maintenance_work_mem_mb),
        'max_connections': str(max_conn),
    }


d = json.load(open('/tmp/provision-output.json'))

workers_enabled = os.environ.get('INPUT_WORKERS_ENABLED') == 'true'
workers_cmd = os.environ.get('INPUT_WORKERS_CMD', '')
db_enabled = os.environ.get('INPUT_DB_ENABLED') == 'true'
db_plan = os.environ.get('INPUT_DB_PLAN', 'medium')
domain = os.environ.get('INPUT_DOMAIN', '').strip()
repo_name = os.environ['REPO_NAME']
repo_full = os.environ['REPO_FULL']
repo_owner = os.environ['REPO_OWNER']

web_ip = d.get('web_ip', '')
worker_ips = d.get('worker_ips', [])
db_ip = d.get('db_ip', '')
db_internal_ip = d.get('db_internal_ip', '')

config = {
    'service': repo_name,
    'image': repo_full,
    'registry': {
        'server': 'ghcr.io',
        'username': repo_owner,
        'password': ['KAMAL_REGISTRY_PASSWORD'],
    },
    'ssh': {
        'user': 'root',
        'keys': ['.kamal/ssh_key'],
    },
    'servers': {
        'web': {
            'hosts': [web_ip],
        },
    },
    'proxy': {
        'host': domain if domain else f'{web_ip}.nip.io',
        'app_port': 80,
        'forward_headers': False,
        'ssl': bool(domain),
        'healthcheck': {
            'path': '/up',
            'interval': 3,
            'timeout': 5,
        },
    },
    'env': {
        'clear': {
            'BLOB_STORAGE_PATH': '/data/blobs',
        },
    },
    'volumes': [
        '/data/blobs:/data/blobs',
    ],
    'builder': {
        'arch': 'amd64',
        'cache': {
            'type': 'gha',
            'options': 'mode=max',
        },
    },
    'readiness_delay': 15,
    'deploy_timeout': 180,
    'drain_timeout': 30,
}

if workers_enabled and worker_ips:
    config['servers']['workers'] = {
        'hosts': worker_ips,
        'cmd': workers_cmd,
        'proxy': False,
    }

if db_enabled:
    config['env']['clear']['POSTGRES_HOST'] = db_internal_ip
    config['env']['clear']['POSTGRES_DB'] = 'postgres'
    config['env']['clear']['POSTGRES_USER'] = 'postgres'
    config['env']['secret'] = [
        'POSTGRES_PASSWORD',
        'DATABASE_URL',
    ]
    pg_params = compute_pg_params(db_plan)
    pg_cmd = 'postgres -D /etc/postgresql ' + ' '.join(
        f'-c {k}={v}' for k, v in pg_params.items()
    )
    config['accessories'] = {
        'db': {
            'image': 'supabase/postgres:17.6.1.084',
            'host': db_ip,
            'port': '5432:5432',
            'cmd': pg_cmd,
            'env': {
                'secret': [
                    'POSTGRES_PASSWORD',
                ],
            },
            'directories': [
                '/data/db/pgdata:/var/lib/postgresql/data',
            ],
        },
    }

# Merge custom variables and secrets from ENV_VARS / SECRET_ENV_VARS
custom_vars = json.load(open('/tmp/kamal_custom_vars.json'))
custom_secrets = json.load(open('/tmp/kamal_custom_secrets.json'))
for k, v in custom_vars.items():
    config['env']['clear'][k] = v
if custom_secrets:
    config['env'].setdefault('secret', [])
    config['env']['secret'].extend(custom_secrets)

os.makedirs('config', exist_ok=True)
with open('config/deploy.yml', 'w') as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)

print('Generated config/deploy.yml:')
with open('config/deploy.yml') as f:
    print(f.read())
